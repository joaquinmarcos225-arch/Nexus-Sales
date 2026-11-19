"""Agenda de Nexus Support.

- Pagos de Nexus (Railway, Vercel, proveedores): cuándo vence cada uno.
- Por empresa cliente: cuándo cobrarle, cuándo cargar OpenAI / Prospeo / Brave para ella
  y cuándo acreditarle los créditos del mes. Más la ficha con todos sus datos.
"""

from __future__ import annotations

import calendar
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing_ops_cycle import BillingOpsCycle
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider
from app.models.mailbox_health import MailboxHealth
from app.models.ops_agenda import CompanyOpsProfile, OpsPayment
from app.models.user import User
from app.services.billing_ops import get_or_create_cycle, serialize_cycle
from app.services.credit_ledger import current_plan_cycle_key
from app.services.credit_plans import plan_definition

CHARGE_NOTICE_DAYS = 5
PAY_BEFORE_DAYS = 5
BLOCK_FROM_DEFAULT = "2026-11"
PAYMENT_NOTICE_DAYS = 7
FREQUENCIES = ("monthly", "yearly", "once")
DEFAULT_PAYMENTS = (
    ("Railway (servidor)", "monthly"),
    ("Vercel (web)", "monthly"),
)
# Los proveedores se pagan por empresa (ver company_billing), no como gasto fijo de Nexus.
_PER_COMPANY_PROVIDER_ROWS = ("Prospeo (plan)", "OpenAI (saldo)", "Brave Search (plan)")
ROLE_LABELS = {"owner": "Dueño", "gerente": "Director", "manager": "Manager", "sdr": "Vendedor"}


def _today() -> date:
    return datetime.now(UTC).date()


def _ops_company_id() -> int:
    try:
        return int((os.getenv("NEXUS_OPS_COMPANY_ID") or "1").strip() or "1")
    except ValueError:
        return 1


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(max(1, day), calendar.monthrange(year, month)[1]))


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return _clamp_day(d.year + y, m + 1, d.day)


def _status(due: date | None, today: date) -> str:
    if due is None:
        return "sin_fecha"
    days = (due - today).days
    if days < 0:
        return "vencido"
    if days == 0:
        return "hoy"
    if days <= 3:
        return "pronto"
    return "despues"


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


# ——— Pagos de Nexus ———


def seed_default_payments(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(OpsPayment)):
        return
    for name, freq in DEFAULT_PAYMENTS:
        db.add(OpsPayment(name=name, frequency=freq, active=True))
    db.flush()


def serialize_payment(p: OpsPayment, today: date | None = None) -> dict[str, Any]:
    today = today or _today()
    return {
        "id": p.id,
        "name": p.name,
        "amount_usd": p.amount_usd,
        "frequency": p.frequency,
        "next_due": _iso(p.next_due),
        "notes": p.notes,
        "last_paid_at": _iso(p.last_paid_at),
        "active": bool(p.active),
        "status": _status(p.next_due, today),
    }


def list_payments(db: Session) -> list[OpsPayment]:
    seed_default_payments(db)
    for row in db.scalars(
        select(OpsPayment).where(
            OpsPayment.active.is_(True),
            OpsPayment.name.in_(_PER_COMPANY_PROVIDER_ROWS),
            OpsPayment.next_due.is_(None),
            OpsPayment.amount_usd.is_(None),
        )
    ).all():
        row.active = False
    db.flush()
    rows = db.scalars(select(OpsPayment).where(OpsPayment.active.is_(True))).all()
    return sorted(rows, key=lambda p: (p.next_due is None, p.next_due or date.max, p.name))


def mark_payment_paid(db: Session, payment: OpsPayment) -> OpsPayment:
    payment.last_paid_at = datetime.now(UTC)
    base = payment.next_due or _today()
    if payment.frequency == "monthly":
        payment.next_due = _add_months(base, 1)
    elif payment.frequency == "yearly":
        payment.next_due = _add_months(base, 12)
    else:
        payment.active = False
    db.flush()
    return payment


# ——— Empresas ———


def get_profile(db: Session, company_id: int) -> CompanyOpsProfile | None:
    return db.scalars(select(CompanyOpsProfile).where(CompanyOpsProfile.company_id == company_id)).first()


def billing_day(db: Session, company: Company, profile: CompanyOpsProfile | None) -> int:
    """Día del mes en que se le activan los créditos (paga 5 días antes)."""
    if profile is not None and profile.billing_day:
        return int(profile.billing_day)
    first_granted = db.scalar(
        select(BillingOpsCycle.credits_granted_at)
        .where(BillingOpsCycle.company_id == company.id, BillingOpsCycle.credits_granted_at.is_not(None))
        .order_by(BillingOpsCycle.credits_granted_at.asc())
        .limit(1)
    )
    ref = first_granted or company.created_at
    return min(int(ref.day), 28) if ref else 1


def _cycle_due(cycle_key: str, day: int) -> date:
    year, month = (int(x) for x in cycle_key.split("-"))
    return _clamp_day(year, month, day)


def _next_cycle_key(key: str) -> str:
    year, month = (int(x) for x in key.split("-"))
    return f"{year + (month // 12)}-{month % 12 + 1:02d}"


def _prev_cycle_key(key: str) -> str:
    year, month = (int(x) for x in key.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _find_cycle(db: Session, company_id: int, key: str) -> BillingOpsCycle | None:
    return db.scalars(
        select(BillingOpsCycle).where(BillingOpsCycle.company_id == company_id, BillingOpsCycle.cycle_key == key)
    ).first()


def _block_from_key() -> str:
    return (os.getenv("NEXUS_CREDIT_BLOCK_FROM") or BLOCK_FROM_DEFAULT).strip()


def _plan_price(company: Company) -> float:
    return float(plan_definition(company.plan).price_usd or 0)


# Pruebas / cortesía: usan créditos que Nexus cargó a mano; no exigen pago de ciclo.
_TRIAL_BILLING_STATUSES = frozenset({"trialing", "trial", "demo", "comp", "courtesy"})
_TRIAL_BILLING_PROVIDERS = frozenset({"dev", "trial", "comp"})


def company_skips_plan_payment_block(company: Company) -> bool:
    """True si la empresa es de prueba/cortesía y no debe bloquearse por plan impago."""
    status = (getattr(company, "billing_status", None) or "").strip().lower()
    if status in _TRIAL_BILLING_STATUSES:
        return True
    provider = (getattr(company, "billing_provider", None) or "").strip().lower()
    return provider in _TRIAL_BILLING_PROVIDERS


def credit_date(key: str, day: int) -> date:
    """Día en que se le activan los créditos del mes `key`."""
    return _cycle_due(key, day)


def pay_by_date(key: str, day: int) -> date:
    """Hasta cuándo tiene para pagar: 5 días antes de que se activen los créditos."""
    return credit_date(key, day) - timedelta(days=PAY_BEFORE_DAYS)


def credits_block(db: Session, company: Company, *, today: date | None = None) -> dict[str, Any] | None:
    """Si llegó el día de activar créditos y no pagó, los créditos quedan bloqueados (no puede usar nuevos)."""
    from app.services.billing.service import company_can_auto_renew

    if (
        int(company.id) == _ops_company_id()
        or _plan_price(company) <= 0
        or company_skips_plan_payment_block(company)
        or company_can_auto_renew(company)
    ):
        return None
    today = today or _today()
    day = billing_day(db, company, get_profile(db, company.id))
    key = current_plan_cycle_key()
    if today < credit_date(key, day):
        key = _prev_cycle_key(key)
    if key < _block_from_key():
        return None
    cycle = _find_cycle(db, company.id, key)
    if cycle is not None and cycle.paid:
        return None
    return {"cycle_key": key, "since": credit_date(key, day).isoformat(), "pay_by": pay_by_date(key, day).isoformat()}


def credits_block_message(db: Session, company_id: int) -> str | None:
    company = db.get(Company, company_id)
    if company is None or credits_block(db, company) is None:
        return None
    return (
        "Los créditos están bloqueados porque todavía no se registró el pago del plan de este mes. "
        "Las secuencias que ya arrancaron siguen; para usar créditos nuevos, el director tiene que pagar el plan."
    )


def payment_notice(db: Session, company: Company, *, today: date | None = None) -> dict[str, Any] | None:
    """Aviso para el director del cliente: se acerca la fecha de pago, o ya se bloquearon los créditos."""
    today = today or _today()
    block = credits_block(db, company, today=today)
    if block is not None:
        return {"blocked": True, **block}
    if (
        int(company.id) == _ops_company_id()
        or _plan_price(company) <= 0
        or company_skips_plan_payment_block(company)
    ):
        return None
    from app.services.billing.service import company_can_auto_renew

    if company_can_auto_renew(company):
        return None
    day = billing_day(db, company, get_profile(db, company.id))
    for key in (current_plan_cycle_key(), _next_cycle_key(current_plan_cycle_key())):
        on = credit_date(key, day)
        if on < today:
            continue
        cycle = _find_cycle(db, company.id, key)
        if cycle is not None and cycle.paid:
            continue
        pay_by = pay_by_date(key, day)
        if (pay_by - today).days <= CHARGE_NOTICE_DAYS:
            return {"blocked": False, "cycle_key": key, "pay_by": pay_by.isoformat(), "credit_on": on.isoformat()}
        return None
    return None


_TOOL_LABELS = {"openai": "OpenAI", "prospeo": "Prospeo", "brave": "Brave"}


def company_billing(
    db: Session,
    company: Company,
    *,
    cycle: BillingOpsCycle,
    day: int,
    today: date,
) -> dict[str, Any] | None:
    """Pagos de esta empresa: en qué etapa está el mes, días que faltan y cuánto cargar a cada proveedor."""
    if int(company.id) == _ops_company_id():
        return None
    # Prueba/cortesía: sin ciclo de cobro. Support no debe mostrar Starter impago ni “no pagó”.
    if company_skips_plan_payment_block(company):
        return None
    from app.services.billing_ops import _snapshot_from_company

    key = cycle.cycle_key
    row: BillingOpsCycle | None = cycle
    if cycle.credits_granted:
        key = _next_cycle_key(key)
        row = _find_cycle(db, company.id, key)
    on = credit_date(key, day)
    pay_by = on - timedelta(days=PAY_BEFORE_DAYS)
    providers_by = on - timedelta(days=1)

    if row is not None:
        data = serialize_cycle(row, company)
        price = data["price_usd"]
        credits = data["credits_to_grant"]
        tools = [
            {"key": t["key"], "label": t["label"], "amount_usd": t["amount_usd"], "paid": bool(t["topped_up"])}
            for t in data["tools"]
            if t["required"]
        ]
        paid = bool(row.paid)
    else:
        snap = _snapshot_from_company(company, custom_credits=cycle.credits_to_grant)
        price = snap["price_usd"]
        credits = snap["credits_to_grant"]
        tools = [
            {"key": k, "label": _TOOL_LABELS[k], "amount_usd": float(snap[f"{k}_usd"]), "paid": False}
            for k in ("openai", "prospeo", "brave")
            if float(snap[f"{k}_usd"]) > 0
        ]
        paid = False

    block = credits_block(db, company, today=today)
    tools_left = [t for t in tools if not t["paid"]]
    if block is not None:
        stage = "bloqueado"
    elif not paid and today > pay_by:
        stage = "vencido"
    elif not paid and (pay_by - today).days <= CHARGE_NOTICE_DAYS:
        stage = "periodo_pago"
    elif not paid:
        stage = "al_dia"
    elif tools_left:
        stage = "pagar_proveedores"
    elif row is not None and not row.credits_granted:
        stage = "activar"
    else:
        stage = "al_dia"

    return {
        "cycle_key": key,
        "stage": stage,
        "price_usd": price,
        "credits": credits,
        "paid": paid,
        "pay_by": pay_by.isoformat(),
        "days_to_pay": (pay_by - today).days,
        "credit_on": on.isoformat(),
        "days_to_credit": (on - today).days,
        "providers_by": providers_by.isoformat(),
        "days_to_providers": (providers_by - today).days,
        "providers": tools,
        "providers_total_usd": round(sum(t["amount_usd"] for t in tools), 2),
    }


def company_tasks(
    db: Session,
    company: Company,
    *,
    cycle: BillingOpsCycle,
    day: int,
    today: date,
) -> list[dict[str, Any]]:
    """Lo que Soporte tiene que hacer con esta empresa, con fecha.

    Orden de cada mes: cobrarle (5 días antes de activar créditos) → pagar sus proveedores
    → activarle los créditos el día que corresponde.
    """
    if int(company.id) == _ops_company_id():
        return []
    if company_skips_plan_payment_block(company):
        return []
    tasks: list[dict[str, Any]] = []
    name = company.name

    def add(kind: str, title: str, due: date, key: str, **extra: Any) -> None:
        tasks.append(
            {
                "kind": kind,
                "company_id": company.id,
                "company_name": name,
                "title": title,
                "cycle_key": key,
                "due": due.isoformat(),
                "status": _status(due, today),
                **extra,
            }
        )

    for key in (cycle.cycle_key, _next_cycle_key(cycle.cycle_key)):
        row = cycle if key == cycle.cycle_key else _find_cycle(db, company.id, key)
        if row is not None and row.credits_granted:
            continue
        on = credit_date(key, day)
        pay_by = on - timedelta(days=PAY_BEFORE_DAYS)
        if row is None or not row.paid:
            price = float(row.price_usd) if row is not None else _plan_price(company)
            if price <= 0 or (pay_by - today).days > CHARGE_NOTICE_DAYS:
                break
            label = plan_definition(company.plan).label
            blocked = today >= on and key >= _block_from_key()
            if blocked:
                title = f"Cobrarle el plan {label} (USD {price:g}) · no pagó: créditos bloqueados desde el {on:%d/%m}"
            elif today > pay_by:
                title = f"Cobrarle el plan {label} (USD {price:g}) · se le venció el {pay_by:%d/%m}"
            else:
                title = f"Cobrarle el plan {label} (USD {price:g}) · tiene que pagar antes del {pay_by:%d/%m}"
            add("cobrar", title, pay_by, key, blocked=blocked)
            break

        data = serialize_cycle(row, company)
        pending = [t for t in data["tools"] if t["required"] and not t["topped_up"]]
        if pending:
            parts = ", ".join(f"USD {t['amount_usd']:g} a {t['label']}" for t in pending)
            add(
                "pagar_proveedores",
                f"Pagar lo de {name}: {parts}",
                on - timedelta(days=1),
                key,
                tools=[t["key"] for t in pending],
            )
        elif data["can_grant_credits"]:
            add("acreditar", f"Activarle {data['credits_to_grant']} créditos del mes", on, key, ready=today >= on)
        break
    return tasks


def _users(db: Session, company_id: int) -> list[User]:
    return list(
        db.scalars(
            select(User).where(User.company_id == company_id, User.deleted_at.is_(None)).order_by(User.id)
        ).all()
    )


def _gmail_state(db: Session, company_id: int) -> dict[int, str]:
    rows = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.provider == IntegrationProvider.gmail.value,
        )
    ).all()
    down = {
        int(h.seller_id)
        for h in db.scalars(
            select(MailboxHealth).where(
                MailboxHealth.company_id == company_id, MailboxHealth.gmail_down_since.is_not(None)
            )
        ).all()
    }
    out: dict[int, str] = {}
    for r in rows:
        if r.user_id is None:
            continue
        uid = int(r.user_id)
        out[uid] = "desconectado" if uid in down or (r.status or "") == "error" else (
            "conectado" if (r.status or "") == "connected" else "sin conectar"
        )
    return out


def company_card(db: Session, company: Company, *, today: date | None = None) -> dict[str, Any]:
    today = today or _today()
    key = current_plan_cycle_key()
    cycle = get_or_create_cycle(db, company.id, cycle_key=key)
    profile = get_profile(db, company.id)
    day = billing_day(db, company, profile)
    data = serialize_cycle(cycle, company)
    users = [u for u in _users(db, company.id) if u.is_active]
    gmail = _gmail_state(db, company.id)
    active_campaigns = db.scalar(
        select(func.count())
        .select_from(Campaign)
        .where(Campaign.company_id == company.id, Campaign.status.in_(("ready", "running")))
    )
    charge_key = key
    if cycle.paid:
        charge_key = _next_cycle_key(key)
        nxt = _find_cycle(db, company.id, charge_key)
        if nxt is not None and nxt.paid:
            charge_key = _next_cycle_key(charge_key)
    due = pay_by_date(charge_key, day)
    block = credits_block(db, company, today=today)
    is_trial = company_skips_plan_payment_block(company)
    return {
        "company_id": company.id,
        "name": company.name,
        "is_nexus": int(company.id) == _ops_company_id(),
        "is_trial": is_trial,
        "plan_label": "Plan de prueba" if is_trial else data["plan_label"],
        "price_usd": 0.0 if is_trial else data["price_usd"],
        "billing_day": day,
        "next_charge": None if is_trial else due.isoformat(),
        "next_credit_on": None if is_trial else credit_date(charge_key, day).isoformat(),
        "credits_blocked": block is not None,
        "paid_this_month": True if is_trial else bool(cycle.paid),
        "tools_ready": True if is_trial else bool(data["tools_ready"]),
        "credits_granted": True if is_trial else bool(cycle.credits_granted),
        "users": len(users),
        "active_campaigns": int(active_campaigns or 0),
        "gmail_down": sum(1 for s in gmail.values() if s == "desconectado"),
        "tasks": company_tasks(db, company, cycle=cycle, day=day, today=today),
        "billing": company_billing(db, company, cycle=cycle, day=day, today=today),
    }


def all_companies(db: Session) -> list[Company]:
    """Empresas que Soporte opera. Sin gente activa = ya no es cliente (queda CostGuard)."""
    rows = list(db.scalars(select(Company).order_by(Company.name.asc())).all())
    ops_id = _ops_company_id()
    out: list[Company] = []
    for company in rows:
        if int(company.id) == ops_id:
            out.append(company)
            continue
        active = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.company_id == int(company.id),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if int(active or 0) == 0:
            continue
        out.append(company)
    return out


def agenda(db: Session) -> dict[str, Any]:
    today = _today()
    cards = [company_card(db, c, today=today) for c in all_companies(db)]
    items: list[dict[str, Any]] = [t for card in cards for t in card["tasks"]]
    for p in list_payments(db):
        if p.next_due is None:
            items.append(
                {
                    "kind": "pago_nexus",
                    "payment_id": p.id,
                    "title": f"Poné la fecha de pago de {p.name}",
                    "due": None,
                    "status": "sin_fecha",
                }
            )
        elif (p.next_due - today).days <= PAYMENT_NOTICE_DAYS:
            amount = f" (USD {p.amount_usd:g})" if p.amount_usd else ""
            items.append(
                {
                    "kind": "pago_nexus",
                    "payment_id": p.id,
                    "title": f"Pagar {p.name}{amount}",
                    "due": p.next_due.isoformat(),
                    "status": _status(p.next_due, today),
                }
            )
    items.sort(key=lambda t: (t["due"] is None, t["due"] or ""))
    return {"today": today.isoformat(), "items": items, "companies": cards}


def _whatsapp_snapshot(db: Session, company: Company) -> dict[str, Any]:
    from app.services.whatsapp_commercial import snapshot

    return snapshot(db, company)


def company_detail(db: Session, company: Company) -> dict[str, Any]:
    card = company_card(db, company)
    profile = get_profile(db, company.id)
    gmail = _gmail_state(db, company.id)
    from app.services.credits import CreditError, get_wallet_totals

    try:
        _w, total, assigned, unassigned = get_wallet_totals(db, company.id)
        wallet = {"total": total, "assigned": assigned, "free": unassigned}
    except CreditError:
        wallet = None
    cycles = db.scalars(
        select(BillingOpsCycle)
        .where(BillingOpsCycle.company_id == company.id)
        .order_by(BillingOpsCycle.cycle_key.desc())
        .limit(6)
    ).all()
    campaigns = db.execute(
        select(Campaign.status, func.count())
        .where(Campaign.company_id == company.id)
        .group_by(Campaign.status)
    ).all()
    return {
        **card,
        "created_at": _iso(company.created_at),
        "billing_status": company.billing_status,
        "billing_country": company.billing_country,
        "last_payment_at": _iso(company.last_payment_at),
        "automation_stopped": bool(company.global_automation_stop),
        "profile": {
            "billing_day": profile.billing_day if profile else None,
            "contact_name": profile.contact_name if profile else None,
            "contact_email": profile.contact_email if profile else None,
            "contact_phone": profile.contact_phone if profile else None,
            "notes": profile.notes if profile else None,
        },
        "wallet": wallet,
        "campaigns_by_status": {str(s or ""): int(n) for s, n in campaigns},
        "team": [
            {
                "id": u.id,
                "name": (u.name or f"{u.first_name or ''} {u.last_name or ''}").strip() or u.email,
                "email": u.email,
                "role": ROLE_LABELS.get(str(u.role or ""), str(u.role or "")),
                "active": bool(u.is_active),
                "gmail": gmail.get(int(u.id), "sin conectar"),
            }
            for u in _users(db, company.id)
        ],
        "cycles": [serialize_cycle(c, company) for c in cycles],
        "whatsapp": _whatsapp_snapshot(db, company),
    }


def update_profile(db: Session, company_id: int, data: dict[str, Any]) -> CompanyOpsProfile:
    profile = get_profile(db, company_id)
    if profile is None:
        profile = CompanyOpsProfile(company_id=company_id)
        db.add(profile)
    for key in ("billing_day", "contact_name", "contact_email", "contact_phone", "notes"):
        if key in data:
            value = data[key]
            if isinstance(value, str):
                value = value.strip() or None
            setattr(profile, key, value)
    db.flush()
    return profile
