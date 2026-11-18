from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from datetime import UTC, datetime

from app.models import Company
from app.models.campaign import Campaign
from app.models.credit_ledger import CreditLedgerEntry
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


def assert_purchase_request_actor(user: User) -> None:
    """Director, owner o manager pueden solicitar compra de créditos."""
    role = _user_role(user)
    if role in (UserRole.gerente, UserRole.owner, UserRole.manager):
        return
    raise CreditError("Solo director o manager pueden solicitar compra de créditos")


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


def _assert_credits_not_blocked(session: Session, company_id: int) -> None:
    from app.services.ops_agenda import credits_block_message

    msg = credits_block_message(session, company_id)
    if msg:
        raise CreditError(msg)


def _atomic_use_allocation(
    session: Session, row: SellerCreditAllocation, amount: int, *, label: str = ""
) -> None:
    """Suma `amount` a used_balance en una sola escritura, solo si alcanza el saldo.

    Evita que dos pedidos simultáneos lean el mismo saldo y cobren/descuenten mal.
    """
    _assert_credits_not_blocked(session, int(row.company_id))
    session.flush()
    res = session.execute(
        update(SellerCreditAllocation)
        .where(
            SellerCreditAllocation.id == row.id,
            SellerCreditAllocation.allocated_balance - SellerCreditAllocation.used_balance >= amount,
        )
        .values(used_balance=SellerCreditAllocation.used_balance + amount)
        .execution_options(synchronize_session=False)
    )
    session.expire(row, ["used_balance", "allocated_balance"])
    if res.rowcount != 1:
        available = max(0, int(row.allocated_balance) - int(row.used_balance))
        raise CreditError(f"Créditos insuficientes{label}: disponible {available}, requerido {amount}")


def _atomic_take_pool(session: Session, company_id: int, amount: int, *, label: str = "") -> None:
    """Descuenta del pool libre de la empresa en una sola escritura, solo si alcanza."""
    _assert_credits_not_blocked(session, company_id)
    session.flush()
    assigned = (
        select(func.coalesce(func.sum(SellerCreditAllocation.allocated_balance), 0))
        .where(SellerCreditAllocation.company_id == company_id)
        .scalar_subquery()
    )
    res = session.execute(
        update(CreditWallet)
        .where(CreditWallet.company_id == company_id, CreditWallet.total_balance - assigned >= amount)
        .values(total_balance=CreditWallet.total_balance - amount)
        .execution_options(synchronize_session=False)
    )
    wallet = session.scalars(select(CreditWallet).where(CreditWallet.company_id == company_id)).one_or_none()
    if wallet is not None:
        session.expire(wallet, ["total_balance"])
    if res.rowcount != 1:
        _, _total, _assigned, unassigned = get_wallet_totals(session, company_id)
        raise CreditError(
            f"Créditos insuficientes en el pool de empresa{label}: "
            f"disponible {unassigned}, requerido {amount}"
        )


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


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def one_month_after(dt: datetime) -> datetime:
    """Mismo día del mes siguiente (31/01 → 28/02)."""
    import calendar

    dt = _as_utc(dt)
    year = dt.year + dt.month // 12
    month = dt.month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def renew_due_plan_credits(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Un mes después de la última carga de créditos: renueva el plan (pasarela activa) o vence lo no usado."""
    from app.services.billing.service import company_can_auto_renew

    now = _as_utc(now or datetime.now(UTC))
    wallets = session.scalars(select(CreditWallet)).all()
    renewed = 0
    expired = 0
    skipped = 0
    for wallet in wallets:
        anchor = wallet.plan_last_credited_at
        if anchor is None:
            # Saldo cargado antes de que se registrara la fecha de carga: el mes corre desde hoy.
            if int(wallet.total_balance or 0) > 0:
                wallet.plan_last_credited_at = now
            skipped += 1
            continue
        if now < one_month_after(anchor):
            skipped += 1
            continue
        company = session.get(Company, int(wallet.company_id))
        if company is None:
            skipped += 1
            continue

        if company_can_auto_renew(company):
            period_end = company.billing_period_end
            if period_end is not None and _as_utc(period_end) > now:
                # La pasarela todavía no cerró el período: acredita el webhook de pago.
                skipped += 1
                continue
            try:
                before = int(wallet.total_balance or 0)
                apply_plan_credits_to_company(
                    session,
                    int(wallet.company_id),
                    manual=False,
                    allow_repeat_cycle=True,
                )
                renewed += 1
                if before > 0:
                    expired += 1
            except CreditError:
                skipped += 1
            continue

        burned = expire_unused_plan_credits(
            session,
            int(wallet.company_id),
            note=f"Pasó un mes desde la carga del {_as_utc(anchor):%d/%m}: créditos no usados a 0",
        )
        wallet.plan_last_credited_at = None
        if burned > 0:
            expired += 1
        skipped += 1
    session.flush()
    return {
        "renewed": renewed,
        "expired": expired,
        "skipped": skipped,
        "cycle": current_plan_cycle_key(now),
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
    if int(amount) > 0:
        wallet.plan_last_credited_at = datetime.now(UTC)
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


def _seller_allocation(
    session: Session, company_id: int, user_id: int
) -> SellerCreditAllocation | None:
    return session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == user_id,
        )
    ).first()


def _has_personal_cupo(row: SellerCreditAllocation | None) -> bool:
    return row is not None and int(row.allocated_balance) > 0


def _uses_director_company_pool(user: User | None, row: SellerCreditAllocation | None) -> bool:
    """Director/owner gasta del pool salvo que tenga cupo personal asignado."""
    if user is None or not is_company_admin(user.role):
        return False
    return not _has_personal_cupo(row)


def get_user_available_credits(session: Session, company_id: int, user_id: int) -> int:
    """
    Créditos disponibles para crear/ampliar campañas.
    Director/Owner: pool no asignado de la empresa, salvo cupo personal asignado.
    SDR/Manager: allocated - used de su asignación.
    """
    user = session.get(User, user_id)
    row = _seller_allocation(session, company_id, user_id)
    if user is not None and user.company_id == company_id and _uses_director_company_pool(user, row):
        try:
            _, _total, _assigned, unassigned = get_wallet_totals(session, company_id)
        except CreditError:
            return 0
        return int(unassigned)

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
    label = f" ({reason})" if reason else ""
    get_wallet_totals(session, company_id)
    _atomic_take_pool(session, company_id, int(amount), label=label)
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
    row = _seller_allocation(session, company_id, user_id)
    if user is not None and user.company_id == company_id and _uses_director_company_pool(user, row):
        _consume_company_pool_credits(
            session,
            company_id,
            user_id,
            amount,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return None

    if row is None or int(row.allocated_balance) <= 0:
        label = f" ({reason})" if reason else ""
        raise CreditError(
            f"Créditos insuficientes{label}: no hay créditos asignados a este vendedor. "
            "Pedile al director que te asigne créditos."
        )
    label = f" ({reason})" if reason else ""
    _atomic_use_allocation(session, row, int(amount), label=label)
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
    row = _seller_allocation(session, company_id, user_id)
    if user is not None and user.company_id == company_id and _uses_director_company_pool(user, row):
        _consume_company_pool_credits(
            session,
            company_id,
            user_id,
            1,
            reason="secuencia individual",
            actor_user_id=actor_user_id,
        )
        return

    if row is None:
        raise CreditError(
            "Créditos insuficientes (secuencia individual): no hay saldo asignado a tu usuario"
        )
    _atomic_use_allocation(session, row, 1, label=" (secuencia individual)")
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


def consume_followup_credit(
    session: Session,
    company_id: int,
    user_id: int,
    *,
    actor_user_id: int | None = None,
    prospect_id: int | None = None,
) -> None:
    """1 crédito por follow-up manual desde Pipeline."""
    note = "follow-up pipeline"
    if prospect_id:
        note = f"follow-up pipeline · prospecto {prospect_id}"
    user = session.get(User, user_id)
    row = _seller_allocation(session, company_id, user_id)
    if user is not None and user.company_id == company_id and _uses_director_company_pool(user, row):
        _consume_company_pool_credits(
            session,
            company_id,
            user_id,
            1,
            reason=note,
            actor_user_id=actor_user_id,
        )
        return

    if row is None:
        raise CreditError(
            "Créditos insuficientes (follow-up): no hay saldo asignado al SDR de la campaña"
        )
    _atomic_use_allocation(session, row, 1, label=" (follow-up)")
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=user_id,
        actor_user_id=actor_user_id or user_id,
        kind="followup_reserve",
        amount=1,
        note=note,
    )
    session.flush()


def consume_explorar_credit(
    session: Session,
    company_id: int,
    user_id: int,
    *,
    kind: str,
    note: str,
) -> None:
    """1 crédito por uso de Explorar (revelar contacto o bloque de búsquedas)."""
    user = session.get(User, user_id)
    row = _seller_allocation(session, company_id, user_id)
    if user is not None and user.company_id == company_id and _uses_director_company_pool(user, row):
        get_wallet_totals(session, company_id)
        _atomic_take_pool(session, company_id, 1, label=f" ({note})")
    else:
        available = 0 if row is None else int(row.allocated_balance) - int(row.used_balance)
        if row is None or available < 1:
            raise CreditError(f"Créditos insuficientes ({note}): disponible {max(0, available)}, requerido 1")
        _atomic_use_allocation(session, row, 1, label=f" ({note})")
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=user_id,
        actor_user_id=user_id,
        kind=kind,
        amount=1,
        note=note,
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
    row = _seller_allocation(session, company_id, user_id)
    if user is not None and user.company_id == company_id and _uses_director_company_pool(user, row):
        _release_company_pool_credits(
            session,
            company_id,
            user_id,
            amount,
            reason=reason,
            actor_user_id=actor_user_id,
        )
        return None

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


def net_campaign_reserved_credits(
    session: Session,
    company_id: int,
    seller_id: int,
    campaign_name: str,
) -> int:
    """Neto reserve−release de esta campaña en esta empresa (ledger)."""
    name = (campaign_name or "").strip()
    if not name or seller_id <= 0:
        return 0
    token = f"campaña «{name}»"
    rows = session.scalars(
        select(CreditLedgerEntry).where(
            CreditLedgerEntry.company_id == int(company_id),
            CreditLedgerEntry.user_id == int(seller_id),
            CreditLedgerEntry.kind.in_(("campaign_reserve", "campaign_release")),
        )
    ).all()
    net = 0
    for row in rows:
        note = (row.note or "").strip()
        if token not in note and name not in note:
            continue
        amt = int(row.amount or 0)
        if row.kind == "campaign_reserve":
            net += amt
        elif row.kind == "campaign_release":
            net -= amt
    return max(0, net)


def ensure_campaign_search_credits_charged(session: Session, campaign: Campaign) -> int:
    """
    Si la búsqueda ya trajo prospectos y en esta empresa no están cobrados, descuenta el faltante.

    Cubre migraciones / devoluciones erróneas: búsqueda hecha ⇒ crédito sacado.
    """
    from app.services.campaign_prospects import get_campaign_prospects_found_total
    from app.services.manual_sequence_kickoff import is_individual_container_campaign
    from app.services.postergados_campaign import is_postergados_campaign
    from app.services.prospect_base_campaign import is_prospect_base_campaign

    if campaign is None or not getattr(campaign, "id", None):
        return 0
    if (
        is_individual_container_campaign(campaign)
        or is_postergados_campaign(campaign)
        or is_prospect_base_campaign(campaign)
    ):
        return 0
    seller_id = int(campaign.seller_id or 0)
    company_id = int(campaign.company_id or 0)
    if seller_id <= 0 or company_id <= 0:
        return 0
    found = int(
        get_campaign_prospects_found_total(session, int(campaign.id), campaign=campaign) or 0
    )
    if found <= 0:
        return 0
    net = net_campaign_reserved_credits(session, company_id, seller_id, campaign.name or "")
    gap = found - net
    if gap <= 0:
        return 0
    consume_user_credits(
        session,
        company_id,
        seller_id,
        gap,
        reason=f"campaña «{(campaign.name or '').strip()}» (búsqueda)",
    )
    return gap


def adjust_campaign_prospection_credits(
    session: Session,
    company_id: int,
    seller_id: int,
    old_count: int,
    new_count: int,
    *,
    campaign_name: str,
    campaign: Campaign | None = None,
) -> None:
    delta = int(new_count) - int(old_count)
    if delta == 0:
        return
    label = f"campaña «{campaign_name.strip()}»"
    if delta > 0:
        consume_user_credits(session, company_id, seller_id, delta, reason=f"ampliar {label}")
    elif campaign is not None:
        release_campaign_unused_credits(session, campaign, -delta, action="reducir")
    else:
        release_user_credits(session, company_id, seller_id, -delta, reason=f"reducir {label}")


def _current_period_start(session: Session, company_id: int) -> datetime | None:
    wallet = session.scalars(select(CreditWallet).where(CreditWallet.company_id == int(company_id))).one_or_none()
    return None if wallet is None else wallet.plan_last_credited_at


def refundable_campaign_credits(session: Session, campaign: Campaign, unused: int) -> int:
    """Solo vuelven créditos reservados en el mes en curso; los de un mes ya cerrado vencieron."""
    unused = max(0, int(unused))
    start = _current_period_start(session, campaign.company_id)
    if unused == 0 or start is None:
        return 0
    start = _as_utc(start).replace(microsecond=0)
    if campaign.created_at is not None and _as_utc(campaign.created_at) >= start:
        return unused
    expanded = session.scalar(
        select(func.coalesce(func.sum(CreditLedgerEntry.amount), 0)).where(
            CreditLedgerEntry.company_id == campaign.company_id,
            CreditLedgerEntry.user_id == campaign.seller_id,
            CreditLedgerEntry.kind == "campaign_reserve",
            CreditLedgerEntry.note == f"ampliar campaña «{(campaign.name or '').strip()}»",
            CreditLedgerEntry.created_at >= start,
        )
    )
    return min(unused, max(0, int(expanded or 0)))


def release_campaign_unused_credits(session: Session, campaign: Campaign, unused: int, *, action: str) -> int:
    """Devuelve lo no usado de una campaña (al eliminarla o achicarla), solo si es del mes en curso."""
    label = f"{action} campaña «{(campaign.name or '').strip()}»"
    refund = refundable_campaign_credits(session, campaign, unused)
    if refund > 0:
        release_user_credits(session, campaign.company_id, campaign.seller_id, refund, reason=label)
    expired = max(0, int(unused)) - refund
    if expired > 0:
        record_credit_ledger(
            session,
            company_id=campaign.company_id,
            user_id=campaign.seller_id,
            kind="campaign_release_expired",
            amount=0,
            note=f"{label}: {expired} créditos no vuelven porque eran de un mes ya cerrado",
        )
        session.flush()
    return refund


def _allocation_row(session: Session, company_id: int, user_id: int) -> SellerCreditAllocation | None:
    return session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == user_id,
        )
    ).first()


def transfer_campaign_reservation(
    session: Session,
    campaign,
    from_user_id: int,
    to_user_id: int,
    *,
    actor_user_id: int | None = None,
) -> int:
    """Al cambiar el vendedor de una campaña, los lugares aún sin llenar viajan con ella.

    Sale de la asignación (o del pool) del vendedor anterior y entra a la del nuevo,
    sin cobrar de nuevo ni crear créditos. Devuelve cuántos créditos se movieron.
    """
    from app.services.campaign_prospects import get_campaign_prospects_found_total
    from app.services.manual_sequence_kickoff import is_individual_container_campaign
    from app.services.postergados_campaign import is_postergados_campaign
    from app.services.prospect_base_campaign import is_prospect_base_campaign

    if not from_user_id or not to_user_id or int(from_user_id) == int(to_user_id):
        return 0
    if (
        is_individual_container_campaign(campaign)
        or is_postergados_campaign(campaign)
        or is_prospect_base_campaign(campaign)
    ):
        return 0
    company_id = int(campaign.company_id)
    found = get_campaign_prospects_found_total(session, int(campaign.id), campaign=campaign)
    pending = max(0, int(campaign.prospect_count or 0) - found)
    if pending <= 0:
        return 0

    old_user = session.get(User, int(from_user_id))
    new_user = session.get(User, int(to_user_id))
    old_admin = old_user is not None and is_company_admin(old_user.role)
    new_admin = new_user is not None and is_company_admin(new_user.role)

    if old_admin:
        moved = pending
    else:
        old_row = _allocation_row(session, company_id, int(from_user_id))
        if old_row is None:
            return 0
        # Si la reserva ya venció en un ciclo anterior, solo se mueve lo que sigue reservado.
        moved = min(pending, int(old_row.used_balance), int(old_row.allocated_balance))
        if moved <= 0:
            return 0
        old_row.used_balance = int(old_row.used_balance) - moved
        old_row.allocated_balance = int(old_row.allocated_balance) - moved

    wallet = None
    if old_admin or new_admin:
        wallet, *_ = get_wallet_totals(session, company_id)
    if old_admin and not new_admin:
        wallet.total_balance = int(wallet.total_balance) + moved
    if new_admin and not old_admin:
        wallet.total_balance = int(wallet.total_balance) - moved

    if not new_admin:
        new_row = _allocation_row(session, company_id, int(to_user_id))
        if new_row is None:
            new_row = SellerCreditAllocation(
                company_id=company_id, seller_id=int(to_user_id), allocated_balance=0, used_balance=0
            )
            session.add(new_row)
            session.flush()
        new_row.allocated_balance = int(new_row.allocated_balance) + moved
        new_row.used_balance = int(new_row.used_balance) + moved

    note = f"campaña «{campaign.name}»: {moved} lugares sin llenar pasan al nuevo vendedor"
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=int(from_user_id),
        actor_user_id=actor_user_id,
        kind="campaign_release",
        amount=moved,
        note=note,
    )
    record_credit_ledger(
        session,
        company_id=company_id,
        user_id=int(to_user_id),
        actor_user_id=actor_user_id,
        kind="campaign_reserve",
        amount=moved,
        note=note,
    )
    session.flush()
    return moved


def _is_credit_eligible_role(role: str | None) -> bool:
    raw = (role or "").strip().lower()
    return raw in (
        UserRole.sdr.value,
        UserRole.manager.value,
        UserRole.gerente.value,
        "seller",
        "director",
    )


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
