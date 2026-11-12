"""Revelar en Explorar deja el contacto en la base de prospectos."""

from sqlalchemy import select

from app.models.explorar import ExplorarReveal
from app.models.product import Product
from app.models.prospect import Prospect
from app.services import explorar as svc
from app.services.manual_sequence_kickoff import INDIVIDUAL_CAMPAIGN_NAME


def test_ensure_prospect_from_reveal_creates_base_row():
    from tests.test_explorar import _session, _setup as base

    db = _session()
    company_id, user_id = base(db, credits=5)
    product = Product(company_id=company_id, name="Prod", description="x", is_active=True)
    db.add(product)
    db.flush()

    reveal = ExplorarReveal(
        company_id=company_id,
        person_id="prospeo-123",
        revealed_by_user_id=user_id,
        charged=1,
        name="Ana Test",
        role="Ops Manager",
        company_name="Retail SA",
        country="Chile",
        industry="General Retail",
        linkedin_url="https://www.linkedin.com/in/ana-test",
        email="ana@retail.cl",
        phone="+56911111111",
    )
    db.add(reveal)
    db.flush()

    row = svc.ensure_prospect_from_reveal(
        db,
        company_id=company_id,
        user_id=user_id,
        reveal=reveal,
        snapshot={"person_id": "prospeo-123", "company_size": "51-200"},
    )
    assert row is not None
    assert int(reveal.prospect_id) == int(row.id)
    assert row.email == "ana@retail.cl"
    assert row.phone == "+56911111111"
    assert row.source_person_id == "prospeo-123"
    assert "Explorar" in (row.notes or "")

    from app.models.campaign import Campaign

    camp = db.get(Campaign, int(row.campaign_id))
    assert camp is not None
    assert camp.name == INDIVIDUAL_CAMPAIGN_NAME

    # Idempotente: no duplica.
    again = svc.ensure_prospect_from_reveal(
        db,
        company_id=company_id,
        user_id=user_id,
        reveal=reveal,
        snapshot={"person_id": "prospeo-123"},
    )
    assert again is not None
    assert int(again.id) == int(row.id)
    n = db.scalar(
        select(Prospect).where(
            Prospect.company_id == company_id,
            Prospect.source_person_id == "prospeo-123",
        )
    )
    assert n is not None
