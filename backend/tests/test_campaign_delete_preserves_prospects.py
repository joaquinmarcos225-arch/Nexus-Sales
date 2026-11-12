"""Borrar campaña: los contactos siguen en la empresa (contenedor individual)."""

from sqlalchemy import func, select

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.routes.campaigns import delete_campaign
from app.services.campaign_delete import relocate_prospects_when_campaign_deleted
from app.services.manual_sequence_kickoff import INDIVIDUAL_CAMPAIGN_NAME


def test_relocate_prospects_on_campaign_delete():
    from tests.test_explorar import _session, _setup as base

    db = _session()
    company_id, user_id = base(db, credits=5)
    from app.models.product import Product

    product = Product(company_id=company_id, name="Prod", description="x", is_active=True)
    db.add(product)
    db.flush()
    camp = Campaign(
        company_id=company_id,
        seller_id=user_id,
        product_id=product.id,
        name="Campaña borrable",
        prospect_count=3,
        status="ready",
        calendar_link="https://calendar.google.com/",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="Lun-Vie 09:00-18:00",
        tone="profesional",
        estimated_meetings_min=0,
        estimated_meetings_max=0,
        estimated_cost_min=0,
        estimated_cost_max=0,
        estimated_avg_cost_per_meeting=0.0,
    )
    db.add(camp)
    db.flush()
    p = Prospect(
        company_id=company_id,
        campaign_id=camp.id,
        name="Test Contact",
        company_name="Acme",
        status="nuevo",
        compatibility_score=80,
        interest_probability=50,
    )
    db.add(p)
    db.commit()

    n = relocate_prospects_when_campaign_deleted(db, camp)
    assert n == 1
    db.refresh(p)
    assert p.campaign_id != camp.id
    container = db.scalars(
        select(Campaign).where(
            Campaign.company_id == company_id,
            Campaign.name == INDIVIDUAL_CAMPAIGN_NAME,
        )
    ).one()
    assert p.campaign_id == container.id
    assert p.origin_campaign_id == camp.id

    delete_campaign(db=db, campaign=camp)
    assert db.get(Prospect, p.id) is not None
    alive = db.scalar(
        select(func.count()).select_from(Prospect).where(Prospect.company_id == company_id)
    )
    assert int(alive or 0) >= 1
