"""Billing: activate, renew, upgrade/downgrade, no double purchase."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.services.billing import service as billing
from app.services.credits import CreditError, ensure_wallet


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _company(db, *, plan="starter", status="none") -> Company:
    c = Company(name="PayCo", plan=plan, employee_count=3, billing_status=status)
    db.add(c)
    db.flush()
    ensure_wallet(db, c)
    db.refresh(c)
    return c


def test_activate_grants_credits_once():
    db = _session()
    c = _company(db)
    billing.activate_paid_plan(db, c.id, plan_key="growth", provider="dev")
    db.flush()
    w = db.get(CreditWallet, c.wallet.id)
    assert int(w.total_balance) == 1_000
    assert c.billing_status == "active"
    assert c.plan == "growth"

    # second activate same cycle → no double credits
    billing.activate_paid_plan(db, c.id, plan_key="growth", provider="dev")
    db.refresh(w)
    assert int(w.total_balance) == 1_000


def test_upgrade_adds_delta():
    db = _session()
    c = _company(db, plan="starter", status="active")
    ensure_wallet(db, c)
    c.wallet.total_balance = 600
    from app.services.credit_ledger import current_plan_cycle_key

    c.wallet.plan_cycle_key = current_plan_cycle_key()
    db.flush()

    result = billing.change_plan_self_serve(db, c.id, "scaler")
    assert result["action"] == "upgraded"
    assert result["credits_added"] == 800  # 1400 - 600
    assert c.plan == "scaler"
    assert int(c.wallet.total_balance) == 1_400


def test_downgrade_is_pending():
    db = _session()
    c = _company(db, plan="elite", status="active")
    result = billing.change_plan_self_serve(db, c.id, "starter")
    assert result["action"] == "downgrade_scheduled"
    assert c.plan == "elite"
    assert c.pending_plan == "starter"


def test_same_plan_rejected():
    db = _session()
    c = _company(db, plan="growth", status="active")
    try:
        billing.change_plan_self_serve(db, c.id, "growth")
        assert False, "expected error"
    except CreditError:
        pass


def test_renew_applies_pending_and_skips_double():
    db = _session()
    c = _company(db, plan="elite", status="active")
    c.pending_plan = "growth"
    c.wallet.total_balance = 100
    db.flush()

    billing.renew_paid_cycle(db, c.id, provider="stripe")
    assert c.plan == "growth"
    assert c.pending_plan is None
    assert int(c.wallet.total_balance) == 1_000

    bal = int(c.wallet.total_balance)
    billing.renew_paid_cycle(db, c.id, provider="stripe")
    assert int(c.wallet.total_balance) == bal


def test_past_due_blocks_auto_renew_flag():
    db = _session()
    c = _company(db, status="past_due")
    assert billing.company_can_auto_renew(c) is False
    c.billing_status = "active"
    c.billing_provider = "stripe"
    assert billing.company_can_auto_renew(c) is True
    c.billing_provider = "ops"
    assert billing.company_can_auto_renew(c) is False
