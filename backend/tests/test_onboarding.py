"""Registro de workspace (empresa + directora)."""

import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.models.campaign import Campaign  # noqa: F401 — metadata for Secuencias individuales
from app.services.onboarding import OnboardingError, register_workspace


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_register_workspace_creates_company_wallet_user(monkeypatch):
    monkeypatch.setenv("NEXUS_ALLOW_WORKSPACE_SIGNUP", "1")
    db = _session()
    company, user = register_workspace(
        db,
        company_name="Acme Corp",
        employee_count=25,
        plan="growth",
        first_name="Ana",
        last_name="Directora",
        email="ana@acme.test",
        password="secret12",
    )
    db.commit()

    assert company.name == "Acme Corp"
    assert company.plan == "growth"
    assert user.role == UserRole.owner.value
    assert user.email == "ana@acme.test"

    wallet = db.scalars(select(CreditWallet).where(CreditWallet.company_id == company.id)).one()
    assert int(wallet.total_balance) == 1_000
    assert wallet.plan_cycle_key

    product = db.scalars(select(Product).where(Product.company_id == company.id)).first()
    assert product is not None
    assert product.name == "Mi producto"

    campaign = db.scalars(select(Campaign).where(Campaign.company_id == company.id)).first()
    assert campaign is not None
    assert campaign.name == "Secuencias individuales"


def test_register_workspace_blocked_without_flag(monkeypatch):
    monkeypatch.delenv("NEXUS_ALLOW_WORKSPACE_SIGNUP", raising=False)
    db = _session()
    try:
        register_workspace(
            db,
            company_name="X",
            employee_count=1,
            plan="starter",
            first_name="A",
            last_name="B",
            email="x@test.com",
            password="secret12",
        )
        assert False, "expected OnboardingError"
    except OnboardingError as e:
        assert "deshabilitado" in str(e).lower()


def test_register_workspace_rejects_duplicate_email(monkeypatch):
    monkeypatch.setenv("NEXUS_ALLOW_WORKSPACE_SIGNUP", "1")
    db = _session()
    register_workspace(
        db,
        company_name="One",
        employee_count=1,
        plan="starter",
        first_name="A",
        last_name="B",
        email="dup@test.com",
        password="secret12",
    )
    db.commit()
    try:
        register_workspace(
            db,
            company_name="Two",
            employee_count=1,
            plan="starter",
            first_name="C",
            last_name="D",
            email="dup@test.com",
            password="secret12",
        )
        assert False, "expected OnboardingError"
    except OnboardingError as e:
        assert "email" in str(e).lower()
