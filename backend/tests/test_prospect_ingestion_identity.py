"""Dedup empresa: mismo contacto no se duplica entre vendedores."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import ProspectOwnershipStatus, UserRole
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.lead_sourcing.linkedin_identity import linkedin_slug_key
from app.services.prospect_ingestion import find_duplicate_in_company, phone_identity_keys


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    company = Company(name="Acme", plan="starter", employee_count=5)
    db.add(company)
    db.flush()
    sdr_a = User(
        company_id=company.id,
        email="a@test.com",
        first_name="A",
        last_name="Sdr",
        name="A Sdr",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    sdr_b = User(
        company_id=company.id,
        email="b@test.com",
        first_name="B",
        last_name="Sdr",
        name="B Sdr",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    product = Product(company_id=company.id, name="Prod", description="d", is_active=True)
    db.add_all([sdr_a, sdr_b, product])
    db.flush()
    camp_a = Campaign(
        company_id=company.id,
        seller_id=sdr_a.id,
        product_id=product.id,
        name="Camp A",
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
    camp_b = Campaign(
        company_id=company.id,
        seller_id=sdr_b.id,
        product_id=product.id,
        name="Camp B",
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
    db.add_all([camp_a, camp_b])
    db.flush()
    return company, sdr_a, sdr_b, camp_a, camp_b


def _prospect(campaign, **kwargs):
    data = {
        "company_id": campaign.company_id,
        "campaign_id": campaign.id,
        "name": "Jane Doe",
        "company_name": "Target SA",
        "status": "imported",
        "compatibility_score": 80,
        "interest_probability": 40,
    }
    data.update(kwargs)
    return Prospect(**data)


def test_linkedin_slug_key_unifies_host_query_and_encoding():
    a = linkedin_slug_key("https://www.linkedin.com/in/mia-%C3%A1lvarez/?trk=abc")
    b = linkedin_slug_key("https://linkedin.com/in/mia-álvarez")
    assert a and a == b


def test_phone_identity_keys_match_ar_variants():
    keys = phone_identity_keys("+54 9 11 5555-1234", None)
    assert phone_identity_keys("5491155551234", None) & keys
    assert phone_identity_keys("11 5555 1234", None) & keys


def test_company_dedup_same_email_two_campaigns():
    db = _session()
    company, _a, _b, camp_a, camp_b = _seed(db)
    db.add(_prospect(camp_a, email="jane@target.com"))
    db.flush()
    hit = find_duplicate_in_company(
        db,
        company_id=company.id,
        linkedin_url=None,
        email="Jane@Target.com",
    )
    assert hit is not None
    assert hit.campaign_id == camp_a.id
    assert camp_b.id != camp_a.id


def test_company_dedup_same_linkedin_slug_different_urls():
    db = _session()
    company, _a, _b, camp_a, _camp_b = _seed(db)
    db.add(_prospect(camp_a, linkedin_url="https://www.linkedin.com/in/jane-doe/?trk=x"))
    db.flush()
    hit = find_duplicate_in_company(
        db,
        company_id=company.id,
        linkedin_url="https://linkedin.com/in/jane-doe",
        email=None,
    )
    assert hit is not None


def test_company_dedup_same_phone_no_email():
    db = _session()
    company, _a, _b, camp_a, _camp_b = _seed(db)
    db.add(_prospect(camp_a, phone="+54 9 11 4444-0000", email=None, linkedin_url=None))
    db.flush()
    hit = find_duplicate_in_company(
        db,
        company_id=company.id,
        linkedin_url=None,
        email=None,
        phone="5491144440000",
    )
    assert hit is not None


def test_persist_skips_locked_owner_of_other_seller():
    from fastapi import HTTPException

    from app.routes.prospects import _persist_new_prospect
    from app.schemas.prospect import ProspectCreate

    db = _session()
    company, sdr_a, _sdr_b, camp_a, camp_b = _seed(db)
    existing = _prospect(
        camp_a,
        email="shared@target.com",
        owner_user_id=sdr_a.id,
        ownership_status=ProspectOwnershipStatus.tomado.value,
    )
    db.add(existing)
    db.flush()

    try:
        _persist_new_prospect(
            db,
            camp_b,
            ProspectCreate(
                name="Jane Doe",
                company_name="Target SA",
                email="shared@target.com",
                source_provider="manual",
            ),
        )
        assert False, "expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "otro vendedor" in str(exc.detail).lower()
    _ = company
