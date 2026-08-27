"""Ops cobros: pagó → tools → créditos."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.services.billing_ops import (
    get_or_create_cycle,
    grant_cycle_credits,
    mark_paid,
    mark_tool_top_up,
    serialize_cycle,
    tools_ready,
)
from app.services.credit_ledger import current_plan_cycle_key
from app.services.credits import CreditError, ensure_wallet


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _company(db, *, name="Ops Co", plan="starter"):
    c = Company(name=name, plan=plan, employee_count=1)
    db.add(c)
    db.flush()
    return c


def test_ops_flow_grants_credits():
    db = _session()
    company = _company(db)
    wallet = ensure_wallet(db, company)
    wallet.total_balance = 0
    wallet.plan_cycle_key = None
    db.flush()

    cycle = get_or_create_cycle(db, company.id)
    assert cycle.credits_to_grant == 600
    assert abs(float(cycle.openai_usd) - 3.6) < 0.01

    try:
        mark_tool_top_up(db, company.id, "openai", actor_user_id=1)
        assert False, "expected error before paid"
    except CreditError:
        pass

    mark_paid(db, company.id, actor_user_id=1, paid=True)
    mark_tool_top_up(db, company.id, "openai", actor_user_id=1)
    mark_tool_top_up(db, company.id, "prospeo", actor_user_id=1)
    mark_tool_top_up(db, company.id, "brave", actor_user_id=1)
    assert tools_ready(cycle)

    cycle2, granted = grant_cycle_credits(db, company.id, actor_user_id=1)
    assert granted == 600
    assert cycle2.credits_granted is True
    db.refresh(wallet)
    assert int(wallet.total_balance) == 600
    assert wallet.plan_cycle_key == current_plan_cycle_key()

    ser = serialize_cycle(cycle2, company)
    assert ser["can_grant_credits"] is False
    assert ser["paid"] is True


def test_ops_does_not_double_grant_same_cycle():
    db = _session()
    company = _company(db, name="Ops Co 2")
    wallet = ensure_wallet(db, company)
    wallet.total_balance = 600
    wallet.plan_cycle_key = current_plan_cycle_key()
    db.flush()

    mark_paid(db, company.id, actor_user_id=1, paid=True)
    for tool in ("openai", "prospeo", "brave"):
        mark_tool_top_up(db, company.id, tool, actor_user_id=1)

    _, granted = grant_cycle_credits(db, company.id, actor_user_id=1)
    assert granted == 0
    db.refresh(wallet)
    assert int(wallet.total_balance) == 600

    try:
        grant_cycle_credits(db, company.id, actor_user_id=1)
        assert False, "expected already granted"
    except CreditError:
        pass


def test_ops_provider_does_not_auto_renew():
    """Tras marcar pago Ops, el scheduler no debe sumar créditos solo."""
    from app.services.billing.service import company_can_auto_renew

    db = _session()
    company = _company(db)
    mark_paid(db, company.id, actor_user_id=1, paid=True)
    db.refresh(company)
    assert company.billing_status == "active"
    assert (company.billing_provider or "") == "ops"
    assert company_can_auto_renew(company) is False
