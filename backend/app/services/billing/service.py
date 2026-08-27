"""Lógica de suscripción: activar, renovar, upgrade/downgrade, créditos."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.services.billing import config as billing_config
from app.services.credit_plans import credits_for_plan, normalize_plan_key, plan_definition
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credits import CreditError, ensure_wallet

_logger = logging.getLogger("nexus.billing")

STATUS_NONE = "none"
STATUS_ACTIVE = "active"
STATUS_PAST_DUE = "past_due"
STATUS_CANCELED = "canceled"

PROVIDER_STRIPE = "stripe"
PROVIDER_MP = "mercadopago"
PROVIDER_DLOCAL = "dlocal"
PROVIDER_DEV = "dev"


def get_company(db: Session, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    return company


def has_paid_subscription(company: Company) -> bool:
    """
    True solo si hay cobro real confirmado.
    Planes demo/dev o checkout a medias NO cuentan.
    """
    status = (company.billing_status or STATUS_NONE).strip().lower()
    if status != STATUS_ACTIVE:
        return False
    provider = (company.billing_provider or "").strip().lower()
    if provider == PROVIDER_STRIPE:
        return bool(company.stripe_subscription_id)
    if provider == PROVIDER_MP:
        return bool(company.mp_preapproval_id and company.last_payment_at)
    if provider == PROVIDER_DLOCAL:
        return bool(company.last_payment_at)
    return False


def clear_demo_billing(db: Session, company_id: int) -> Company:
    """Resetea suscripción demo para poder probar checkout real."""
    company = get_company(db, company_id)
    provider = (company.billing_provider or "").strip().lower()
    if has_paid_subscription(company):
        raise CreditError("Hay una suscripción de pago activa; no se puede resetear como demo.")
    if provider not in (PROVIDER_DEV, "", "none") and company.last_payment_at:
        raise CreditError("Estado de facturación no es demo.")
    company.billing_provider = None
    company.billing_status = STATUS_NONE
    company.pending_plan = None
    company.mp_preapproval_id = None
    company.stripe_subscription_id = None
    company.last_payment_at = None
    company.billing_period_end = None
    db.flush()
    return company


def billing_summary(company: Company) -> dict[str, Any]:
    from app.services.billing.latam import list_latam_countries

    plan = plan_definition(company.plan)
    pending = normalize_plan_key(company.pending_plan) if company.pending_plan else None
    pending_label = plan_definition(pending).label if pending else None
    paid = has_paid_subscription(company)
    return {
        "company_id": company.id,
        "plan": plan.key,
        "plan_label": plan.label,
        "plan_contact_credits": plan.monthly_contact_credits,
        "pending_plan": pending if paid else None,
        "pending_plan_label": pending_label if paid else None,
        "billing_provider": company.billing_provider,
        "billing_status": company.billing_status or STATUS_NONE,
        "billing_country": company.billing_country,
        "billing_period_end": company.billing_period_end,
        "last_payment_at": company.last_payment_at,
        "stripe_customer_id": company.stripe_customer_id,
        "stripe_subscription_id": company.stripe_subscription_id,
        "mp_preapproval_id": company.mp_preapproval_id,
        "has_paid_subscription": paid,
        "providers": billing_config.providers_status(),
        "plans": billing_config.list_billable_plans(),
        "latam_countries": list_latam_countries(),
        "can_self_serve": True,
    }


def company_can_auto_renew(company: Company) -> bool:
    """
    Renovación automática de créditos (scheduler):
    - Solo pasarelas de pago activas (Stripe / Mercado Pago / dLocal).
    - Demo (none) y cobro Ops: NO — Ops acredita a mano en /ops-cobros cada mes.
    - past_due / canceled: no
    """
    status = (company.billing_status or STATUS_NONE).strip().lower()
    if status in (STATUS_PAST_DUE, STATUS_CANCELED, STATUS_NONE, "", "legacy", "trialing"):
        return False
    if status != STATUS_ACTIVE:
        return False
    provider = (company.billing_provider or "").strip().lower()
    if provider in (PROVIDER_STRIPE, PROVIDER_MP, PROVIDER_DLOCAL):
        return True
    return False


def _grant_credits(
    db: Session,
    company: Company,
    *,
    amount: int,
    kind: str,
    note: str,
    actor_user_id: int | None = None,
    mark_cycle: bool = True,
) -> None:
    from app.services.credits import expire_unused_plan_credits

    wallet = ensure_wallet(db, company)
    if mark_cycle:
        # Nuevo ciclo mensual: sin arrastre de sobrantes.
        expire_unused_plan_credits(
            db,
            int(company.id),
            actor_user_id=actor_user_id,
            note=f"Nuevo ciclo {current_plan_cycle_key()}: créditos no usados a 0",
        )
        wallet.total_balance = int(amount)
        wallet.plan_cycle_key = current_plan_cycle_key()
        wallet.plan_last_credited_at = datetime.now(UTC)
    else:
        # Upgrade mid-cycle: sumar delta sin expirar.
        wallet.total_balance = int(wallet.total_balance) + int(amount)
    record_credit_ledger(
        db,
        company_id=company.id,
        kind=kind,
        amount=amount,
        note=note,
        actor_user_id=actor_user_id,
    )
    db.flush()


def activate_paid_plan(
    db: Session,
    company_id: int,
    *,
    plan_key: str,
    provider: str,
    actor_user_id: int | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    mp_preapproval_id: str | None = None,
    mp_payer_email: str | None = None,
    period_end: datetime | None = None,
    grant_credits: bool = True,
) -> Company:
    """Primer pago / reactivación: un solo plan activo + recarga del cupo."""
    company = get_company(db, company_id)
    plan = normalize_plan_key(plan_key)
    old_plan = normalize_plan_key(company.plan)
    old_status = (company.billing_status or STATUS_NONE).strip().lower()
    wallet = ensure_wallet(db, company)
    cycle = current_plan_cycle_key()

    company.pending_plan = None
    company.billing_provider = provider
    company.billing_status = STATUS_ACTIVE
    company.last_payment_at = datetime.now(UTC)
    if period_end is not None:
        company.billing_period_end = period_end
    if stripe_customer_id:
        company.stripe_customer_id = stripe_customer_id
    if stripe_subscription_id:
        company.stripe_subscription_id = stripe_subscription_id
    if mp_preapproval_id:
        company.mp_preapproval_id = mp_preapproval_id
    if mp_payer_email:
        company.mp_payer_email = mp_payer_email

    if grant_credits:
        amount = credits_for_plan(plan)
        label = plan_definition(plan).label
        already_same = (
            old_status == STATUS_ACTIVE
            and old_plan == plan
            and (wallet.plan_cycle_key or "") == cycle
        )
        if already_same:
            company.plan = plan
        elif (
            old_status == STATUS_ACTIVE
            and (wallet.plan_cycle_key or "") == cycle
            and amount > credits_for_plan(old_plan)
        ):
            delta = amount - credits_for_plan(old_plan)
            company.plan = plan
            _grant_credits(
                db,
                company,
                amount=delta,
                kind="plan_upgrade",
                note=f"Upgrade vía checkout a {label}: +{delta} créditos",
                actor_user_id=actor_user_id,
                mark_cycle=False,
            )
        else:
            company.plan = plan
            _grant_credits(
                db,
                company,
                amount=amount,
                kind="subscription_payment",
                note=f"Suscripción {label}: +{amount} créditos (pago confirmado)",
                actor_user_id=actor_user_id,
                mark_cycle=True,
            )
    else:
        company.plan = plan

    db.flush()
    _logger.info(
        "billing activate company=%s plan=%s provider=%s",
        company_id,
        plan,
        provider,
    )
    return company


def renew_paid_cycle(
    db: Session,
    company_id: int,
    *,
    provider: str | None = None,
    period_end: datetime | None = None,
) -> Company:
    """Renovación mensual tras cobro OK: aplica pending_plan si hay downgrade."""
    company = get_company(db, company_id)
    if company.pending_plan:
        company.plan = normalize_plan_key(company.pending_plan)
        company.pending_plan = None
    company.billing_status = STATUS_ACTIVE
    if provider:
        company.billing_provider = provider
    company.last_payment_at = datetime.now(UTC)
    if period_end is not None:
        company.billing_period_end = period_end

    amount = credits_for_plan(company.plan)
    label = plan_definition(company.plan).label
    wallet = ensure_wallet(db, company)
    cycle = current_plan_cycle_key()
    # Evitar doble recarga si webhook + scheduler llegan juntos
    if (wallet.plan_cycle_key or "") == cycle:
        db.flush()
        return company

    _grant_credits(
        db,
        company,
        amount=amount,
        kind="subscription_renewal",
        note=f"Renovación {label}: +{amount} créditos",
        mark_cycle=True,
    )
    _logger.info("billing renew company=%s plan=%s", company_id, company.plan)
    return company


def mark_payment_failed(db: Session, company_id: int) -> Company:
    company = get_company(db, company_id)
    company.billing_status = STATUS_PAST_DUE
    db.flush()
    record_credit_ledger(
        db,
        company_id=company_id,
        kind="payment_failed",
        amount=0,
        note="Pago de suscripción fallido — no se recargan créditos",
    )
    return company


def mark_canceled(db: Session, company_id: int) -> Company:
    company = get_company(db, company_id)
    company.billing_status = STATUS_CANCELED
    company.stripe_subscription_id = None
    company.mp_preapproval_id = None
    db.flush()
    return company


def change_plan_self_serve(
    db: Session,
    company_id: int,
    new_plan_key: str,
    *,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Upgrade inmediato (prorrateo simple: diferencia de créditos).
    Downgrade: queda en pending_plan y aplica en la próxima renovación.
    No permite «comprar el mismo plan» otra vez.
    """
    company = get_company(db, company_id)
    new_plan = normalize_plan_key(new_plan_key)
    current = normalize_plan_key(company.plan)
    if new_plan == current and not company.pending_plan:
        raise CreditError("Ya tenés ese plan activo.")
    if new_plan == current and company.pending_plan:
        company.pending_plan = None
        db.flush()
        return {
            "action": "cancel_pending_downgrade",
            "plan": current,
            "pending_plan": None,
            "message": "Se canceló el cambio de plan pendiente.",
        }

    old_credits = credits_for_plan(current)
    new_credits = credits_for_plan(new_plan)

    if new_credits > old_credits:
        # Upgrade inmediato
        company.plan = new_plan
        company.pending_plan = None
        delta = new_credits - old_credits
        _grant_credits(
            db,
            company,
            amount=delta,
            kind="plan_upgrade",
            note=(
                f"Upgrade {plan_definition(current).label} → {plan_definition(new_plan).label}: "
                f"+{delta} créditos"
            ),
            actor_user_id=actor_user_id,
            mark_cycle=False,
        )
        db.flush()
        return {
            "action": "upgraded",
            "plan": new_plan,
            "pending_plan": None,
            "credits_added": delta,
            "message": (
                f"Plan actualizado a {plan_definition(new_plan).label}. "
                f"Se acreditaron {delta} créditos de diferencia."
            ),
            "requires_checkout": company.billing_status != STATUS_ACTIVE,
        }

    # Downgrade → próximo ciclo
    company.pending_plan = new_plan
    db.flush()
    return {
        "action": "downgrade_scheduled",
        "plan": current,
        "pending_plan": new_plan,
        "credits_added": 0,
        "message": (
            f"El plan {plan_definition(new_plan).label} se activará en la próxima renovación. "
            f"Seguís con {plan_definition(current).label} hasta entonces."
        ),
        "requires_checkout": False,
    }


def find_company_by_stripe_customer(db: Session, customer_id: str) -> Company | None:
    if not customer_id:
        return None
    return db.scalars(
        select(Company).where(Company.stripe_customer_id == customer_id)
    ).first()


def find_company_by_stripe_subscription(db: Session, sub_id: str) -> Company | None:
    if not sub_id:
        return None
    return db.scalars(
        select(Company).where(Company.stripe_subscription_id == sub_id)
    ).first()


def find_company_by_mp_preapproval(db: Session, preapproval_id: str) -> Company | None:
    if not preapproval_id:
        return None
    return db.scalars(
        select(Company).where(Company.mp_preapproval_id == preapproval_id)
    ).first()
