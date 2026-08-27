"""
Ops de cobro mensual:
1) Marcar que el cliente pagó
2) Top-up sugerido OpenAI / Prospeo / Brave (cuentas CostGuard)
3) Acreditar créditos Nexus del plan
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing_ops_cycle import BillingOpsCycle
from app.models.company import Company
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credit_plans import (
    TOOL_KEYS,
    custom_tool_costs_for_credits,
    list_contact_plans,
    normalize_plan_key,
    plan_definition,
    plan_economics_dict,
)
from app.services.credits import CreditError, ensure_wallet


def _now() -> datetime:
    return datetime.now(UTC)


def _tool_amount(cycle: BillingOpsCycle, tool: str) -> float:
    if tool == "openai":
        return float(cycle.openai_usd or 0)
    if tool == "prospeo":
        return float(cycle.prospeo_usd or 0)
    if tool == "brave":
        return float(cycle.brave_usd or 0)
    raise CreditError(f"Tool inválida: {tool}")


def _tool_topped_at(cycle: BillingOpsCycle, tool: str) -> datetime | None:
    return getattr(cycle, f"{tool}_topped_up_at", None)


def _set_tool_topped(cycle: BillingOpsCycle, tool: str, *, user_id: int | None, when: datetime | None) -> None:
    setattr(cycle, f"{tool}_topped_up_at", when)
    setattr(cycle, f"{tool}_topped_up_by", user_id if when else None)


def tools_ready(cycle: BillingOpsCycle) -> bool:
    for tool in TOOL_KEYS:
        amount = _tool_amount(cycle, tool)
        if amount <= 0:
            continue
        if _tool_topped_at(cycle, tool) is None:
            return False
    return True


def serialize_cycle(cycle: BillingOpsCycle, company: Company | None = None) -> dict[str, Any]:
    tools = []
    for tool in TOOL_KEYS:
        amount = _tool_amount(cycle, tool)
        topped = _tool_topped_at(cycle, tool)
        tools.append(
            {
                "key": tool,
                "label": {"openai": "OpenAI", "prospeo": "Prospeo", "brave": "Brave"}.get(tool, tool),
                "amount_usd": amount,
                "required": amount > 0,
                "topped_up": topped is not None or amount <= 0,
                "topped_up_at": topped,
            }
        )
    cogs = round(float(cycle.openai_usd) + float(cycle.prospeo_usd) + float(cycle.brave_usd), 2)
    return {
        "id": cycle.id,
        "company_id": cycle.company_id,
        "company_name": company.name if company else None,
        "cycle_key": cycle.cycle_key,
        "plan_key": cycle.plan_key,
        "plan_label": plan_definition(cycle.plan_key).label,
        "credits_to_grant": int(cycle.credits_to_grant),
        "price_usd": float(cycle.price_usd),
        "openai_usd": float(cycle.openai_usd),
        "prospeo_usd": float(cycle.prospeo_usd),
        "brave_usd": float(cycle.brave_usd),
        "tools_cogs_usd": cogs,
        "margin_usd": round(float(cycle.price_usd) - cogs, 2),
        "paid": bool(cycle.paid),
        "paid_at": cycle.paid_at,
        "tools": tools,
        "tools_ready": tools_ready(cycle),
        "credits_granted": bool(cycle.credits_granted),
        "credits_granted_at": cycle.credits_granted_at,
        "credits_granted_amount": int(cycle.credits_granted_amount or 0),
        "can_grant_credits": bool(cycle.paid) and tools_ready(cycle) and not bool(cycle.credits_granted),
        "notes": cycle.notes,
    }


def _snapshot_from_company(company: Company, *, custom_credits: int | None = None) -> dict[str, Any]:
    plan = plan_definition(getattr(company, "plan", None))
    if plan.key == "custom":
        credits = max(0, int(custom_credits if custom_credits is not None else 0))
        eco = plan_economics_dict(plan, custom_credits=credits)
    else:
        eco = plan_economics_dict(plan)
    return {
        "plan_key": eco["key"],
        "credits_to_grant": int(eco["monthly_contact_credits"]),
        "price_usd": float(eco["price_usd"]),
        "openai_usd": float(eco["openai_usd"]),
        "prospeo_usd": float(eco["prospeo_usd"]),
        "brave_usd": float(eco["brave_usd"]),
    }


def get_or_create_cycle(
    db: Session,
    company_id: int,
    *,
    cycle_key: str | None = None,
    custom_credits: int | None = None,
) -> BillingOpsCycle:
    company = db.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    key = (cycle_key or current_plan_cycle_key()).strip()
    row = db.scalars(
        select(BillingOpsCycle).where(
            BillingOpsCycle.company_id == company_id,
            BillingOpsCycle.cycle_key == key,
        )
    ).first()
    if row is not None:
        # Si aún no pagó ni acreditó, refrescar snapshot del plan vigente.
        if not row.paid and not row.credits_granted:
            snap = _snapshot_from_company(
                company,
                custom_credits=custom_credits if custom_credits is not None else row.credits_to_grant,
            )
            for k, v in snap.items():
                setattr(row, k, v)
            db.flush()
        return row

    snap = _snapshot_from_company(company, custom_credits=custom_credits)
    row = BillingOpsCycle(company_id=company_id, cycle_key=key, **snap)
    db.add(row)
    db.flush()
    return row


def set_company_plan(
    db: Session,
    company_id: int,
    plan_key: str,
    *,
    custom_credits: int | None = None,
) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    key = normalize_plan_key(plan_key)
    company.plan = key
    # Refrescar ciclo abierto del mes
    cycle = get_or_create_cycle(db, company_id, custom_credits=custom_credits)
    if not cycle.paid and not cycle.credits_granted:
        snap = _snapshot_from_company(company, custom_credits=custom_credits)
        for k, v in snap.items():
            setattr(cycle, k, v)
    db.flush()
    return company


def mark_paid(
    db: Session,
    company_id: int,
    *,
    actor_user_id: int,
    cycle_key: str | None = None,
    paid: bool = True,
) -> BillingOpsCycle:
    cycle = get_or_create_cycle(db, company_id, cycle_key=cycle_key)
    if cycle.credits_granted and not paid:
        raise CreditError("Ya se acreditaron créditos este ciclo; no se puede desmarcar el pago.")
    if paid:
        cycle.paid = True
        cycle.paid_at = _now()
        cycle.paid_by_user_id = actor_user_id
        company = db.get(Company, company_id)
        if company is not None:
            company.billing_status = "active"
            company.billing_provider = company.billing_provider or "ops"
            company.last_payment_at = cycle.paid_at
    else:
        cycle.paid = False
        cycle.paid_at = None
        cycle.paid_by_user_id = None
    db.flush()
    return cycle


def mark_tool_top_up(
    db: Session,
    company_id: int,
    tool: str,
    *,
    actor_user_id: int,
    cycle_key: str | None = None,
    topped_up: bool = True,
) -> BillingOpsCycle:
    tool = str(tool or "").strip().lower()
    if tool not in TOOL_KEYS:
        raise CreditError(f"Tool inválida: {tool}. Usá openai, prospeo o brave.")
    cycle = get_or_create_cycle(db, company_id, cycle_key=cycle_key)
    if not cycle.paid:
        raise CreditError("Primero marcá que el cliente pagó este mes.")
    if cycle.credits_granted and not topped_up:
        raise CreditError("Créditos ya acreditados; no se puede desmarcar el top-up.")
    if topped_up:
        _set_tool_topped(cycle, tool, user_id=actor_user_id, when=_now())
    else:
        _set_tool_topped(cycle, tool, user_id=None, when=None)
    db.flush()
    return cycle


def grant_cycle_credits(
    db: Session,
    company_id: int,
    *,
    actor_user_id: int,
    cycle_key: str | None = None,
) -> tuple[BillingOpsCycle, int]:
    """Acredita créditos Nexus del ciclo (una sola vez)."""
    cycle = get_or_create_cycle(db, company_id, cycle_key=cycle_key)
    if cycle.credits_granted:
        raise CreditError(f"Los créditos de {cycle.cycle_key} ya fueron acreditados.")
    if not cycle.paid:
        raise CreditError("No se puede acreditar sin marcar el pago.")
    if not tools_ready(cycle):
        raise CreditError("Completá el top-up de OpenAI, Prospeo y Brave antes de acreditar.")

    amount = int(cycle.credits_to_grant)
    if amount < 1:
        raise CreditError("Cupo de créditos inválido (custom necesita cantidad > 0).")

    company = db.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    wallet = ensure_wallet(db, company)

    plan = plan_definition(cycle.plan_key)
    wallet_cycle = (wallet.plan_cycle_key or "").strip()
    granted_now = 0

    if wallet_cycle == cycle.cycle_key:
        # Ya había cupo del mes (seed/demo/legacy). Confirmamos Ops sin duplicar.
        record_credit_ledger(
            db,
            company_id=company_id,
            kind="plan_manual",
            amount=0,
            note=(
                f"Ops {cycle.cycle_key} · {plan.label}: ciclo confirmado "
                f"(wallet ya tenía cupo del mes; sin recarga duplicada)"
            ),
            actor_user_id=actor_user_id,
        )
        granted_now = 0
    else:
        from app.services.credits import expire_unused_plan_credits

        expire_unused_plan_credits(
            db,
            company_id,
            actor_user_id=actor_user_id,
            note=f"Ops {cycle.cycle_key}: créditos no usados del ciclo anterior a 0",
        )
        wallet.total_balance = int(amount)
        wallet.plan_cycle_key = cycle.cycle_key
        wallet.plan_last_credited_at = _now()
        record_credit_ledger(
            db,
            company_id=company_id,
            kind="plan_manual",
            amount=amount,
            note=f"Ops {cycle.cycle_key} · {plan.label}: +{amount} créditos (pago confirmado)",
            actor_user_id=actor_user_id,
        )
        granted_now = amount

    cycle.credits_granted = True
    cycle.credits_granted_at = _now()
    cycle.credits_granted_amount = granted_now if granted_now else amount
    cycle.credits_granted_by = actor_user_id
    db.flush()
    return cycle, granted_now


def company_ops_summary(db: Session, company: Company, *, cycle_key: str | None = None) -> dict[str, Any]:
    key = (cycle_key or current_plan_cycle_key()).strip()
    cycle = get_or_create_cycle(db, company.id, cycle_key=key)
    plan = plan_definition(company.plan)
    custom_credits = cycle.credits_to_grant if plan.key == "custom" else None
    wallet = ensure_wallet(db, company)
    from app.services.credits import get_wallet_totals

    try:
        _w, total, assigned, unassigned = get_wallet_totals(db, company.id)
        wallet_payload = {
            "total_balance": total,
            "assigned_balance": assigned,
            "unassigned_balance": unassigned,
            "plan_cycle_key": wallet.plan_cycle_key,
        }
    except CreditError:
        wallet_payload = {
            "total_balance": int(wallet.total_balance or 0),
            "assigned_balance": 0,
            "unassigned_balance": int(wallet.total_balance or 0),
            "plan_cycle_key": wallet.plan_cycle_key,
        }
    return {
        "company_id": company.id,
        "company_name": company.name,
        "plan": plan.key,
        "plans": [plan_economics_dict(p) for p in list_contact_plans()],
        "current_economics": plan_economics_dict(plan, custom_credits=custom_credits),
        "cycle": serialize_cycle(cycle, company),
        "wallet": wallet_payload,
    }


def board_for_companies(
    db: Session,
    companies: list[Company],
    *,
    cycle_key: str | None = None,
) -> dict[str, Any]:
    key = (cycle_key or current_plan_cycle_key()).strip()
    rows = []
    for company in companies:
        cycle = get_or_create_cycle(db, company.id, cycle_key=key)
        rows.append(serialize_cycle(cycle, company))
    return {"cycle_key": key, "companies": rows}


def update_custom_credits(
    db: Session,
    company_id: int,
    credits: int,
    *,
    cycle_key: str | None = None,
) -> BillingOpsCycle:
    company = db.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    if normalize_plan_key(company.plan) != "custom":
        raise CreditError("Solo el plan Customized usa cupo manual.")
    credits = max(0, int(credits))
    cycle = get_or_create_cycle(db, company_id, cycle_key=cycle_key, custom_credits=credits)
    if cycle.paid or cycle.credits_granted:
        raise CreditError("No se puede cambiar el cupo custom si el ciclo ya está pago o acreditado.")
    tools = custom_tool_costs_for_credits(credits)
    cycle.credits_to_grant = credits
    cycle.price_usd = round(credits * 0.03, 2)
    cycle.openai_usd = tools["openai_usd"]
    cycle.prospeo_usd = tools["prospeo_usd"]
    cycle.brave_usd = tools["brave_usd"]
    db.flush()
    return cycle
