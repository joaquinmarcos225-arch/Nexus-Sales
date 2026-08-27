"""Tests calculadora de capacidad ops."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.models.ops_provider_balance import OpsProviderBalance
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User
from app.services.capacity_calculator import (
    PROSPEO_CREDITS_PER_SEQUENCE,
    build_capacity_report,
    build_reverse_plan,
    compute_client_liability,
    patch_provider_balance_manual,
    sequence_economics_dict,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_economics_ssot():
    eco = sequence_economics_dict()
    assert eco["cogs_per_sequence_usd"] == 0.30
    assert eco["prospeo_credits_per_sequence"] == 11


def test_bottleneck_is_minimum_provider(monkeypatch):
    db = _session()
    db.add(OpsProviderBalance(provider="prospeo", balance_credits=110, source="manual", balance_usd=2.7))
    db.add(OpsProviderBalance(provider="openai", balance_usd=100.0, source="manual"))
    db.add(OpsProviderBalance(provider="brave", balance_usd=100.0, source="manual"))
    db.commit()

    monkeypatch.setattr(
        "app.services.capacity_calculator.fetch_prospeo_account_health",
        lambda: None,
    )

    report = build_capacity_report(db, refresh=False)
    assert report["bottleneck"]["provider"] == "prospeo"
    assert report["bottleneck"]["sequences_available"] == 110 // PROSPEO_CREDITS_PER_SEQUENCE


def test_net_headroom_subtracts_client_liability(monkeypatch):
    db = _session()
    company = Company(name="Cliente", plan="starter", employee_count=1)
    db.add(company)
    db.flush()
    db.add(CreditWallet(company_id=company.id, total_balance=200))
    db.add(OpsProviderBalance(provider="prospeo", balance_credits=1100, source="manual", balance_usd=27.0))
    db.add(OpsProviderBalance(provider="openai", balance_usd=100.0, source="manual"))
    db.add(OpsProviderBalance(provider="brave", balance_usd=100.0, source="manual"))
    db.commit()

    monkeypatch.setattr(
        "app.services.capacity_calculator.fetch_prospeo_account_health",
        lambda: None,
    )

    report = build_capacity_report(db, refresh=False)
    assert report["client_liability"]["total_credits_committed"] == 200
    assert report["gross_capacity_sequences"] == 100
    assert report["net_headroom_sequences"] == 100 - 200


def test_reverse_plan_uses_tool_split():
    providers = [
        {"key": "prospeo", "sequences_available": 500},
        {"key": "openai", "sequences_available": 500},
        {"key": "brave", "sequences_available": 500},
    ]
    plan = build_reverse_plan(proposed_grant=600, providers=providers, net_headroom=400)
    assert plan["topup_usd"]["total"] == round(600 * 0.30, 2)
    assert plan["feasible_with_current_balances"] is False
    assert plan["shortfall_sequences"] == 200


def test_client_liability_aggregates_pool_minus_used():
    db = _session()
    company = Company(name="A", plan="starter", employee_count=1)
    db.add(company)
    db.flush()
    seller = User(
        company_id=company.id,
        first_name="S",
        last_name="",
        name="S",
        email="s@a.com",
        role="sdr",
    )
    db.add(seller)
    db.flush()
    db.add(CreditWallet(company_id=company.id, total_balance=100))
    db.add(
        SellerCreditAllocation(
            company_id=company.id,
            seller_id=seller.id,
            allocated_balance=60,
            used_balance=10,
        )
    )
    db.commit()

    liab = compute_client_liability(db)
    assert liab["total_credits_committed"] == 90
    assert liab["top_companies"][0]["available_credits"] == 90


def test_patch_manual_balance_openai_only():
    db = _session()
    user = User(company_id=1, first_name="O", last_name="", name="O", email="o@x.com", role="owner")
    db.add(user)
    db.flush()
    row = patch_provider_balance_manual(
        db,
        provider="openai",
        balance_usd=42.5,
        notes="CostGuard",
        updated_by_user_id=int(user.id),
    )
    assert row.balance_usd == 42.5
    assert row.source == "manual"
