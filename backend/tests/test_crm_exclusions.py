"""Exclusiones CRM: match email/dominio/empresa y bloqueo de ingest."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.crm_exclusion import CrmExclusion
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.services.crm import exclusions as crm_exclusions


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_company(db) -> Company:
    company = Company(name="Acme", plan="starter", employee_count=5)
    db.add(company)
    db.flush()
    return company


def test_normalize_helpers():
    assert crm_exclusions.normalize_email("  Ana@Acme.io ") == "ana@acme.io"
    assert crm_exclusions.normalize_domain("https://www.acme.io/path") == "acme.io"
    assert crm_exclusions.normalize_domain("gmail.com") is None
    assert crm_exclusions.normalize_company_key("Acme Inc.") == "acme inc"


def test_is_crm_excluded_by_domain_and_email():
    db = _session()
    company = _seed_company(db)
    db.add(
        CrmExclusion(
            company_id=company.id,
            provider="hubspot",
            match_type="domain",
            match_value="acme.io",
            reason="touched",
        )
    )
    db.add(
        CrmExclusion(
            company_id=company.id,
            provider="hubspot",
            match_type="email",
            match_value="ceo@other.com",
            reason="touched",
        )
    )
    db.flush()

    hit = crm_exclusions.is_crm_excluded(
        db,
        company.id,
        email="sdr@acme.io",
        company_name="Other",
    )
    assert hit is not None
    assert hit.match_type == "domain"

    hit2 = crm_exclusions.is_crm_excluded(
        db,
        company.id,
        email="ceo@other.com",
        company_name="Other",
    )
    assert hit2 is not None
    assert hit2.match_type == "email"

    miss = crm_exclusions.is_crm_excluded(
        db,
        company.id,
        email="new@fresh.co",
        company_name="Fresh Co",
        company_website="https://fresh.co",
    )
    assert miss is None


def test_upsert_hits_replaces_stale():
    db = _session()
    company = _seed_company(db)
    first = crm_exclusions._upsert_hits(
        db,
        company.id,
        "hubspot",
        [
            crm_exclusions.ExclusionHit("domain", "old.io", reason="a"),
            crm_exclusions.ExclusionHit("email", "a@old.io", reason="a"),
        ],
    )
    assert first.total == 2
    second = crm_exclusions._upsert_hits(
        db,
        company.id,
        "hubspot",
        [crm_exclusions.ExclusionHit("domain", "new.io", reason="b")],
    )
    assert second.total == 1
    rows = db.query(CrmExclusion).filter(CrmExclusion.company_id == company.id).all()
    assert len(rows) == 1
    assert rows[0].match_value == "new.io"


def test_persist_blocked_by_exclusion(monkeypatch):
    from fastapi import HTTPException

    from app.routes.prospects import _persist_new_prospect
    from app.schemas.prospect import ProspectCreate

    db = _session()
    company = _seed_company(db)
    user = User(
        company_id=company.id,
        email="sdr@test.com",
        first_name="SDR",
        last_name="Test",
        name="SDR Test",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    product = Product(company_id=company.id, name="Prod", description="d", is_active=True)
    db.add_all([user, product])
    db.flush()
    campaign = Campaign(
        company_id=company.id,
        seller_id=user.id,
        product_id=product.id,
        name="Camp",
        prospect_count=10,
        calendar_link="https://cal.example.com",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="profesional",
        estimated_meetings_min=1,
        estimated_meetings_max=3,
        estimated_cost_min=10,
        estimated_cost_max=30,
    )
    db.add(campaign)
    db.add(
        CrmExclusion(
            company_id=company.id,
            provider="salesforce",
            match_type="company_name",
            match_value="blocked co",
            reason="sf",
        )
    )
    db.flush()

    try:
        _persist_new_prospect(
            db,
            campaign,
            ProspectCreate(
                name="Ana",
                company_name="Blocked Co",
                email="ana@fresh.io",
            ),
        )
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "exclusiones" in str(exc.detail).lower()
