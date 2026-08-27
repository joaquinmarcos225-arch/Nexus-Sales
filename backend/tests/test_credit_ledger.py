"""Ledger de créditos — anti-duplicado de plan y movimientos."""

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.credit_ledger import CreditLedgerEntry
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User
from app.services.credit_ledger import current_plan_cycle_key, list_credit_ledger
from app.services.credits import (
    CreditError,
    allocate_to_seller,
    apply_plan_credits_to_company,
    ensure_wallet,
    renew_due_plan_credits,
    reserve_campaign_prospection_credits,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _company_with_wallet(db, plan: str = "starter") -> tuple[Company, CreditWallet]:
    company = Company(name="Co", employee_count=10, plan=plan)
    db.add(company)
    db.flush()
    wallet = ensure_wallet(db, company)
    db.commit()
    return company, wallet


def test_apply_plan_manual_blocks_duplicate_cycle():
    db = _session()
    company, wallet = _company_with_wallet(db)
    cycle = current_plan_cycle_key()

    apply_plan_credits_to_company(db, company.id, manual=True, actor_user_id=1)
    db.commit()
    assert int(wallet.total_balance) == 600
    assert wallet.plan_cycle_key == cycle

    try:
        apply_plan_credits_to_company(db, company.id, manual=True, actor_user_id=1)
        assert False, "expected CreditError"
    except CreditError as e:
        assert "ya fue acreditado" in str(e).lower()

    assert int(wallet.total_balance) == 600


def test_apply_plan_auto_renewal_skips_same_cycle():
    db = _session()
    company, wallet = _company_with_wallet(db)

    apply_plan_credits_to_company(db, company.id, manual=False)
    db.commit()
    balance_after_first = int(wallet.total_balance)

    apply_plan_credits_to_company(db, company.id, manual=False)
    db.commit()
    assert int(wallet.total_balance) == balance_after_first


def test_renew_due_plan_credits_new_cycle():
    db = _session()
    company, wallet = _company_with_wallet(db)
    company.billing_status = "active"
    company.billing_provider = "stripe"
    wallet.plan_cycle_key = "2020-01"
    wallet.total_balance = 100
    db.commit()

    meta = renew_due_plan_credits(db)
    db.commit()

    assert meta["renewed"] == 1
    assert int(wallet.total_balance) == 600
    assert wallet.plan_cycle_key == current_plan_cycle_key()


def test_renew_due_does_not_burn_top_up_without_cycle():
    db = _session()
    company, wallet = _company_with_wallet(db)
    company.billing_status = "none"
    wallet.plan_cycle_key = None
    wallet.total_balance = 140
    db.commit()

    meta = renew_due_plan_credits(db)
    db.commit()

    assert meta["renewed"] == 0
    assert meta["expired"] == 0
    assert int(wallet.total_balance) == 140
    assert wallet.plan_cycle_key == current_plan_cycle_key()


def test_renew_due_skips_demo_none():
    db = _session()
    company, wallet = _company_with_wallet(db)
    company.billing_status = "none"
    wallet.plan_cycle_key = "2020-01"
    wallet.total_balance = 100
    db.commit()

    meta = renew_due_plan_credits(db)
    db.commit()

    assert meta["renewed"] == 0
    assert meta["expired"] == 1
    assert int(wallet.total_balance) == 0


def test_apply_plan_expires_leftover_before_grant():
    db = _session()
    company, wallet = _company_with_wallet(db)
    wallet.plan_cycle_key = "2020-01"
    wallet.total_balance = 250
    db.commit()

    apply_plan_credits_to_company(db, company.id, manual=False)
    db.commit()

    assert int(wallet.total_balance) == 600
    kinds = [e.kind for e in list_credit_ledger(db, company.id, limit=10)]
    assert "plan_expiry" in kinds
    assert "plan_renewal" in kinds


def test_allocate_and_campaign_write_ledger():
    db = _session()
    company, wallet = _company_with_wallet(db)
    wallet.total_balance = 500
    wallet.plan_cycle_key = current_plan_cycle_key()
    db.flush()

    manager = User(
        company_id=company.id,
        email="mgr@test.com",
        first_name="M",
        last_name="G",
        name="Manager",
        role=UserRole.manager.value,
        password_hash="x",
    )
    sdr = User(
        company_id=company.id,
        email="sdr@test.com",
        first_name="S",
        last_name="D",
        name="SDR",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add_all([manager, sdr])
    db.commit()

    allocate_to_seller(db, company.id, manager.id, 200, actor_user_id=1)
    row = SellerCreditAllocation(
        company_id=company.id,
        seller_id=sdr.id,
        allocated_balance=50,
        used_balance=0,
    )
    db.add(row)
    db.commit()

    reserve_campaign_prospection_credits(db, company.id, sdr.id, 10, campaign_name="LATAM Q1")
    db.commit()

    kinds = {e.kind for e in list_credit_ledger(db, company.id)}
    assert "allocate_manager" in kinds
    assert "campaign_reserve" in kinds


def test_plan_seed_recorded_once_per_cycle_change():
    db = _session()
    company, wallet = _company_with_wallet(db)
    wallet.plan_cycle_key = "2020-01"
    db.commit()

    from app.services.credit_ledger import record_credit_ledger

    record_credit_ledger(
        db,
        company_id=company.id,
        kind="plan_seed",
        amount=600,
        note=f"Demo Starter: +600 créditos al pool ({current_plan_cycle_key()})",
    )
    wallet.plan_cycle_key = current_plan_cycle_key()
    db.commit()

    rows = db.scalars(
        select(CreditLedgerEntry).where(
            CreditLedgerEntry.company_id == company.id,
            CreditLedgerEntry.kind == "plan_seed",
        )
    ).all()
    assert len(rows) == 1
