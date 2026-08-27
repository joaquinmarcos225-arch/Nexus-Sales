from sqlalchemy import func, select
from sqlalchemy.orm import Session
from datetime import UTC, datetime

from app.models import Company
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User


from app.core.permissions import is_company_admin, normalize_role
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credit_plans import credits_for_plan, plan_definition


class CreditError(Exception):
    pass


def _user_role(user: User) -> UserRole:
    return normalize_role(user.role)


def assert_gerente_actor(user: User) -> None:
    if not is_company_admin(user.role):
        raise CreditError("Solo el owner/directora puede gestionar el pool de créditos de la empresa")


def assert_manager_or_gerente_actor(user: User) -> None:
    if _user_role(user) not in (UserRole.manager, UserRole.gerente, UserRole.owner):
        raise CreditError("Solo owner, gerente o manager pueden gestionar transferencias de créditos")


def assert_allocate_target_eligible(target: User) -> None:
    if not _is_credit_eligible_role(target.role):
        raise CreditError("El pool de empresa solo se asigna a SDR o Manager")


# Compat alias (tests / imports viejos)
def assert_allocate_target_is_manager(target: User) -> None:
    assert_allocate_target_eligible(target)


def assert_transfer_actor(user: User) -> None:
    """Cualquier usuario con saldo personal (SDR/Manager) o admin de empresa puede transferir."""
    role = _user_role(user)
    if is_company_admin(role):
        return
    if _is_credit_eligible_role(user.role):
        return
    raise CreditError("No tenés permiso para transferir créditos")


def assert_transfer_allowed(*, actor: User, from_user: User, to_user: User) -> None:
    """Transferencia peer-to-peer: desde tu propio saldo a cualquier SDR/Manager de la empresa."""
    assert_transfer_actor(actor)
    if from_user.id != actor.id:
        raise CreditError("Solo podés transferir desde tu propio saldo")
    if to_user.id == actor.id:
        raise CreditError("Origen y destino deben ser distintos")
    if from_user.company_id != to_user.company_id:
        raise CreditError("Solo podés transferir dentro de tu empresa")
    if not _is_credit_eligible_role(to_user.role):
        raise CreditError("Solo se puede transferir a usuarios SDR o Manager")


def visible_allocation_user_ids(actor: User, users: list[User]) -> set[int]:
    role = _user_role(actor)
    if is_company_admin(role):
        return {u.id for u in users}
    # Manager/SDR: ven saldos de todos los elegibles (transferencias peer-to-peer).
    if role in (UserRole.manager, UserRole.sdr) or _is_credit_eligible_role(actor.role):
        ids = {actor.id}
        for u in users:
            if _is_credit_eligible_role(u.role):
                ids.add(u.id)
        return ids
    return {actor.id}


def reconcile_wallet_pool(session: Session, company_id: int) -> bool:
    """
    Corrige datos legacy donde la suma asignada supera el total del pool.
    Devuelve True si hubo ajuste.
    """
    wallet = session.scalars(
        select(CreditWallet).where(CreditWallet.company_id == company_id)
    ).one_or_none()
    if wallet is None:
        return False
    assigned = session.scalar(
        select(func.coalesce(func.sum(SellerCreditAllocation.allocated_balance), 0)).where(
            SellerCreditAllocation.company_id == company_id
        )
    )
    assigned = int(assigned or 0)
    total = int(wallet.total_balance)
    if assigned > total:
        delta = assigned - total
        wallet.total_balance = assigned
        record_credit_ledger(
            session,
            company_id=company_id,
            kind="reconcile",
            amount=delta,
            note=f"Ajuste de pool: +{delta} créditos (asignado superaba total)",
        )
        session.flush()
        return True
    return False


def get_wallet_totals(session: Session, company_id: int) -> tuple[CreditWallet, int, int, int]:
    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    wallet = session.scalars(select(CreditWallet).where(CreditWallet.company_id == company_id)).one_or_none()
    if wallet is None:
        raise CreditError("Wallet no encontrada para esta empresa")
    reconcile_wallet_pool(session, company_id)
    assigned = session.scalar(
        select(func.coalesce(func.sum(SellerCreditAllocation.allocated_balance), 0)).where(
            SellerCreditAllocation.company_id == company_id
        )
    )
    assigned = int(assigned or 0)
    total = int(wallet.total_balance)
    unassigned = max(0, total - assigned)
    return wallet, total, assigned, unassigned


def ensure_wallet(session: Session, company: Company) -> CreditWallet:
    if company.wallet is not None:
        return company.wallet
    existing = session.scalars(
        select(CreditWallet).where(CreditWallet.company_id == company.id)
    ).first()
    if existing is not None:
        return existing
    w = CreditWallet(company_id=company.id, total_balance=0)
    session.add(w)
    session.flush()
    return w


def expire_unused_plan_credits(
    session: Session,
    company_id: int,
    *,
    actor_user_id: int | None = None,
    note: str | None = None,
) -> int:
    """
    Fin de ciclo: lo no usado vuelve a 0 (pool + asignaciones SDR).
    Devuelve la cantidad expirada (>= 0).
    """
    wallet = session.scalars(
        select(CreditWallet).where(CreditWallet.company_id == int(company_id))
    ).one_or_none()
    if wallet is None:
        return 0

    allocs = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == int(company_id)
        )
    ).all()
    for alloc in allocs:
        alloc.allocated_balance = 0
        alloc.used_balance = 0

    leftover = max(0, int(wallet.total_balance or 0))
    wallet.total_balance = 0
    if leftover > 0:
        record_credit_ledger(
            session,
            company_id=int(company_id),
            kind="plan_expiry",
            amount=-leftover,
            note=(note or "").strip()
            or f"Fin de mes: {leftover} créditos no usados vuelven a 0",
            actor_user_id=actor_user_id,
        )
    session.flush()
    return leftover


def apply_plan_credits_to_company(
    session: Session,
    company_id: int,
    *,
    manual: bool = False,
    actor_user_id: int | None = None,
    allow_repeat_cycle: bool = False,
) -> CreditWallet:
    """
    Acredita al pool los contactos del plan vigente (1 ciclo por mes calendario).
    Sin arrastre: antes de acreditar, los sobrantes del mes anterior pasan a 0.
    manual=True: botón «Acreditar plan» (error si ya se acreditó este mes).
  """
    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    wallet = ensure_wallet(session, company)
    cycle = current_plan_cycle_key()
    if wallet.plan_cycle_key == cycle and not allow_repeat_cycle:
        if manual:
            raise CreditError(
                f"El plan ya fue acreditado en {cycle}. La renovación automática corre cada mes; "
                "el próximo ciclo se acredita al cambiar de mes."
            )
        return wallet

    expire_unused_plan_credits(
        session,
        company_id,
        actor_user_id=actor_user_id,
        note=f"Ciclo nuevo {cycle}: créditos no usados del mes anterior a 0",
    )
    amount = credits_for_plan(getattr(company, "plan", None))
    wallet.total_balance = int(amount)
    wallet.plan_cycle_key = cycle
    wallet.plan_last_credited_at = datetime.now(UTC)
    plan = plan_definition(getattr(company, "plan", None))
    record_credit_ledger(
        session,
        company_id=company_id,
        kind="plan_manual" if manual else "plan_renewal",
        amount=amount,
        note=f"Plan {plan.label}: +{amount} créditos al pool",
        actor_user_id=actor_user_id,
    )
    session.flush()
    return wallet


def renew_due_plan_credits(session: Session) -> dict[str, int]:
    """Renovación mensual + expiración de sobrantes (sin arrastre)."""
    from app.services.billing.service import company_can_auto_renew

    cycle = current_plan_cycle_key()
    wallets = session.scalars(select(CreditWallet)).all()
    renewed = 0
    expired = 0
    skipped = 0
    for wallet in wallets:
        prev_cycle = (wallet.plan_cycle_key or "").strip()
        if prev_cycle == cycle:
            skipped += 1
            continue
        # Top-up / piloto sin ciclo de plan: no es un mes vencido. Marcar el mes
        # actual y no quemar el saldo (CostGuard video: 00:05 UTC 2026-08-20).
        if not prev_cycle:
            wallet.plan_cycle_key = cycle
            skipped += 1
            continue
        company = session.get(Company, int(wallet.company_id))
        if company is None:
            skipped += 1
            continue

        will_grant = False
        if company_can_auto_renew(company):
            status = (company.billing_status or "none").strip().lower()
            provider = (company.billing_provider or "").strip().lower()
            if status == "active" and provider in ("stripe", "mercadopago"):
                period_end = company.billing_period_end
                if period_end is not None:
                    pe = period_end if period_end.tzinfo else period_end.replace(tzinfo=UTC)
                    if pe > datetime.now(UTC):
                        will_grant = False
                    else:
                        will_grant = True
                else:
                    will_grant = True
            else:
                will_grant = True

        if will_grant:
            try:
                # apply_plan_credits_to_company ya expira sobrantes antes de acreditar.
                before = int(wallet.total_balance or 0)
                apply_plan_credits_to_company(
                    session,
                    int(wallet.company_id),
                    manual=False,
                    allow_repeat_cycle=False,
                )
                renewed += 1
                if before > 0:
                    expired += 1
            except CreditError:
                skipped += 1
            continue

        # Sin renovación: igual se pierden los sobrantes al cambiar el mes.
        burned = expire_unused_plan_credits(
            session,
            int(wallet.company_id),
            note=f"Fin de ciclo {(wallet.plan_cycle_key or '?')}: créditos no usados a 0",
        )
        if burned > 0:
            expired += 1
        skipped += 1
    session.flush()
    return {
        "renewed": renewed,
        "expired": expired,
        "skipped": skipped,
        "cycle": cycle,
    }


def plan_wallet_summary(company: Company) -> dict[str, str | int]:
    plan = plan_definition(getattr(company, "plan", None))
    return {
        "plan": plan.key,
        "plan_label": plan.label,
        "plan_contact_credits": plan.monthly_contact_credits,
        "plan_description": plan.description,
    }


def top_up_company(
    session: Session,
    company_id: int,
    amount: int,
    *,
    actor_user_id: int | None = None,
    note: str = "",
) -> CreditWallet:
    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    wallet = ensure_wallet(session, company)
    wallet.total_balance = int(wallet.total_balance) + int(amount)
    if not (wallet.plan_cycle_key or "").strip():
        wallet.plan_cycle_key = current_plan_cycle_key()
    record_credit_ledger(
        session,
        company_id=company_id,
        kind="pool_top_up",
        amount=int(amount),
        note=note.strip() or f"Ajuste manual del pool: +{amount} créditos",
        actor_user_id=actor_user_id,
    )
    session.flush()
    return wallet


def get_user_available_credits(session: Session, company_id: int, user_id: int) -> int:
    """
    Créditos disponibles para crear/ampliar campañas.
    Director/Owner: pool no asignado de la empresa.
    SDR/Manager: allocated - used de su asignación.
    """
    user = session.get(User, user_id)
    if user is not None and user.company_id == company_id and is_company_admin(user.role):
        try:
            _, _total, _assigned, unassigned = get_wallet_totals(session, company_id)
        except CreditError:
            return 0
        return int(unassigned)

    row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == user_id,
        )
    ).first()
    if row is None:
        return 0
    return max(0, int(row.allocated_balance) - int(row.used_balance))


def _campaign_ledger_reason(reason: str) -> bool:
    raw = (reason or "").strip().lower()
    return (
        "campaña" in raw
        or "campana" in raw
        or "secuencia individual" in raw
    )


def _consume_company_pool_credits(
    session: Session,
    company_id: int,
    user_id: int,
    amount: int,
    *,
    reason: str = "",
    actor_user_id: int | None = None,
) -> None:
    """Descuenta del pool no asignado (uso directo del director/owner)."""
    wallet, _total, _assigned, unassigned = get_wallet_totals(session, company_id)
    if amount > unassigned:
        label = f" ({reason})" if reason else ""
        raise CreditError(
            f"Créditos insuficientes en el pool de empresa{label}: "
            f"disponible {unassigned}, requerido {amount}"
        )
    wallet.total_balance = int(wallet.total_balance) - int(amount)
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        kind="campaign_reserve",
        amount=int(amount),
        note=reason.strip() or f"Reserva de campaña desde pool: {amount} créditos",
    )
    session.flush()


def _release_company_pool_credits(
    session: Session,
    company_id: int,
    user_id: int,
    amount: int,
    *,
    reason: str = "",
    actor_user_id: int | None = None,
) -> None:
    wallet, *_ = get_wallet_totals(session, company_id)
    wallet.total_balance = int(wallet.total_balance) + int(amount)
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        kind="campaign_release",
        amount=int(amount),
        note=reason.strip() or f"Devolución a pool: {amount} créditos",
    )
    session.flush()


def consume_user_credits(
    session: Session,
    company_id: int,
    user_id: int,
    amount: int,
    *,
    reason: str = "",
    actor_user_id: int | None = None,
) -> SellerCreditAllocation | None:
    """Descuenta créditos del usuario o, si es director/owner, del pool de empresa."""
    if amount <= 0:
        return None

    user = session.get(User, user_id)
    if user is not None and user.company_id == company_id and is_company_admin(user.role):
        _consume_company_pool_credits(
            session,
            company_id,
            user_id,
            amount,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return None

    row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == user_id,
        )
    ).first()
    if row is None or int(row.allocated_balance) <= 0:
        return None
    available = int(row.allocated_balance) - int(row.used_balance)
    if amount > available:
        label = f" ({reason})" if reason else ""
        raise CreditError(f"Créditos insuficientes{label}: disponible {available}, requerido {amount}")
    row.used_balance = int(row.used_balance) + int(amount)
    if _campaign_ledger_reason(reason):
        record_credit_ledger(
            session,
            company_id=company_id,
            user_id=user_id,
            actor_user_id=actor_user_id,
            kind="campaign_reserve",
            amount=int(amount),
            note=reason.strip() or f"Reserva de campaña: {amount} créditos",
        )
    session.flush()
    return row


def consume_sequence_individual_credit(
    session: Session,
    company_id: int,
    user_id: int,
    *,
    actor_user_id: int | None = None,
) -> None:
    """1 crédito obligatorio al iniciar secuencia individual (manual)."""
    user = session.get(User, user_id)
    if user is not None and user.company_id == company_id and is_company_admin(user.role):
        _consume_company_pool_credits(
            session,
            company_id,
            user_id,
            1,
            reason="secuencia individual",
            actor_user_id=actor_user_id,
        )
        return

    row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == user_id,
        )
    ).first()
    if row is None:
        raise CreditError(
            "Créditos insuficientes (secuencia individual): no hay saldo asignado a tu usuario"
        )
    available = int(row.allocated_balance) - int(row.used_balance)
    if available < 1:
        raise CreditError(
            f"Créditos insuficientes (secuencia individual): disponible {available}, requerido 1"
        )
    row.used_balance = int(row.used_balance) + 1
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        kind="campaign_reserve",
        amount=1,
        note="secuencia individual",
    )
    session.flush()


def release_user_credits(
    session: Session,
    company_id: int,
    user_id: int,
    amount: int,
    *,
    reason: str = "",
    actor_user_id: int | None = None,
) -> SellerCreditAllocation | None:
    """Devuelve créditos no utilizados al saldo disponible del usuario o al pool (director)."""
    if amount <= 0:
        return None

    user = session.get(User, user_id)
    if user is not None and user.company_id == company_id and is_company_admin(user.role):
        _release_company_pool_credits(
            session,
            company_id,
            user_id,
            amount,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return None

    row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == user_id,
        )
    ).first()
    if row is None:
        return None
    before = int(row.used_balance)
    row.used_balance = max(0, int(row.used_balance) - int(amount))
    released = before - int(row.used_balance)
    if released > 0 and _campaign_ledger_reason(reason):
        record_credit_ledger(
            session,
            company_id=company_id,
            user_id=user_id,
            actor_user_id=actor_user_id,
            kind="campaign_release",
            amount=released,
            note=reason.strip() or f"Devolución de campaña: {released} créditos",
        )
    session.flush()
    return row


def reserve_campaign_prospection_credits(
    session: Session,
    company_id: int,
    seller_id: int,
    prospect_count: int,
    *,
    campaign_name: str,
) -> SellerCreditAllocation | None:
    """1 prospección = 1 crédito, comprometido al crear/ampliar la campaña."""
    label = f"campaña «{campaign_name.strip()}»"
    return consume_user_credits(
        session,
        company_id,
        seller_id,
        int(prospect_count),
        reason=label,
    )


def adjust_campaign_prospection_credits(
    session: Session,
    company_id: int,
    seller_id: int,
    old_count: int,
    new_count: int,
    *,
    campaign_name: str,
) -> None:
    delta = int(new_count) - int(old_count)
    if delta == 0:
        return
    label = f"campaña «{campaign_name.strip()}»"
    if delta > 0:
        consume_user_credits(session, company_id, seller_id, delta, reason=f"ampliar {label}")
    else:
        release_user_credits(session, company_id, seller_id, -delta, reason=f"reducir {label}")


def _is_credit_eligible_role(role: str | None) -> bool:
    raw = (role or "").strip().lower()
    return raw in (UserRole.sdr.value, UserRole.manager.value, "seller")


def allocate_to_seller(
    session: Session,
    company_id: int,
    seller_id: int,
    amount: int,
    *,
    actor_user_id: int | None = None,
) -> SellerCreditAllocation:
    seller = session.get(User, seller_id)
    if seller is None or seller.company_id != company_id:
        raise CreditError("Usuario no encontrado en esta empresa")
    if not _is_credit_eligible_role(seller.role):
        raise CreditError("Solo se puede asignar saldo a usuarios SDR o Manager")
    assert_allocate_target_eligible(seller)

    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    ensure_wallet(session, company)
    session.flush()

    _, _total, _assigned, unassigned = get_wallet_totals(session, company_id)
    if amount > unassigned:
        raise CreditError("Saldo no asignado insuficiente")

    row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == seller_id,
        )
    ).first()
    if row is None:
        row = SellerCreditAllocation(company_id=company_id, seller_id=seller_id, allocated_balance=0, used_balance=0)
        session.add(row)
        session.flush()

    row.allocated_balance = int(row.allocated_balance) + int(amount)
    seller_label = seller.name or seller.email or f"usuario {seller_id}"
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=seller_id,
        actor_user_id=actor_user_id,
        kind="allocate_manager",
        amount=int(amount),
        note=f"Asignación a {seller_label}: +{amount} créditos",
    )
    session.flush()
    return row


def transfer_credits_between_users(
    session: Session,
    company_id: int,
    from_user_id: int,
    to_user_id: int,
    amount: int,
    *,
    actor: User | None = None,
) -> tuple[SellerCreditAllocation, SellerCreditAllocation]:
    """Manager (o gerente con pool propio) transfiere créditos a otro usuario de la empresa."""
    if amount <= 0:
        raise CreditError("El monto debe ser mayor a cero")
    if from_user_id == to_user_id:
        raise CreditError("Origen y destino deben ser distintos")

    from_user = session.get(User, from_user_id)
    to_user = session.get(User, to_user_id)
    if from_user is None or from_user.company_id != company_id:
        raise CreditError("Usuario origen no válido")
    if to_user is None or to_user.company_id != company_id:
        raise CreditError("Usuario destino no válido")
    if not _is_credit_eligible_role(to_user.role):
        raise CreditError("Solo se puede transferir a usuarios SDR o Manager")
    if actor is not None:
        assert_transfer_allowed(actor=actor, from_user=from_user, to_user=to_user)

    from_row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == from_user_id,
        )
    ).first()
    if from_row is None:
        raise CreditError("El usuario origen no tiene créditos asignados")
    available = int(from_row.allocated_balance) - int(from_row.used_balance)
    if amount > available:
        raise CreditError("Créditos disponibles insuficientes en el origen")

    to_row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == to_user_id,
        )
    ).first()
    if to_row is None:
        to_row = SellerCreditAllocation(
            company_id=company_id,
            seller_id=to_user_id,
            allocated_balance=0,
            used_balance=0,
        )
        session.add(to_row)
        session.flush()

    from_row.allocated_balance = int(from_row.allocated_balance) - int(amount)
    to_row.allocated_balance = int(to_row.allocated_balance) + int(amount)
    from_label = from_user.name or from_user.email or f"usuario {from_user_id}"
    to_label = to_user.name or to_user.email or f"usuario {to_user_id}"
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=to_user_id,
        from_user_id=from_user_id,
        actor_user_id=actor.id if actor is not None else from_user_id,
        kind="transfer",
        amount=int(amount),
        note=f"Transferencia {from_label} → {to_label}: {amount} créditos",
    )
    session.flush()
    return from_row, to_row


def company_dashboard_counts(session: Session, company_id: int) -> dict[str, int]:
    active_products = session.scalar(
        select(func.count()).select_from(Product).where(
            Product.company_id == company_id, Product.is_active.is_(True)
        )
    )
    sellers = session.scalar(
        select(func.count()).select_from(User).where(
            User.company_id == company_id, User.role == UserRole.sdr.value
        )
    )
    return {
        "active_products": int(active_products or 0),
        "active_sellers": int(sellers or 0),
    }
