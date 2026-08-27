"""Marca de campaña e ICP en contexto de outreach."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import ProspectStatus, UserRole
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.campaign_outreach_context import campaign_dict_for_outreach, company_brand_name
from app.services.sdr_outreach_compose import campaign_dict_for_sdr


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _bundle(db):
    company = Company(name="CostGuard Demo Client", employee_count=50, plan="starter")
    db.add(company)
    db.flush()
    product = Product(
        company_id=company.id,
        name="Plataforma Nexus",
        description="Software outbound",
        value_proposition="Automatiza prospección",
        is_active=True,
    )
    user = User(
        company_id=company.id,
        email="sdr@test.com",
        first_name="Joaquin",
        last_name="Test",
        name="Joaquin Test",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add_all([product, user])
    db.flush()
    campaign = Campaign(
        company_id=company.id,
        seller_id=user.id,
        product_id=product.id,
        name="Outbound LATAM Q1.2",
        prospect_count=10,
        calendar_link="https://cal.test",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="profesional",
        estimated_meetings_min=1,
        estimated_meetings_max=2,
        estimated_cost_min=1,
        estimated_cost_max=2,
        sender_name="Joaquin",
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return company, campaign, user


def test_brand_name_uses_company_not_campaign_or_product():
    db = _session()
    company, campaign, user = _bundle(db)
    assert company_brand_name(campaign) == "CostGuard Demo Client"

    sdr_dict = campaign_dict_for_sdr(db, campaign)
    assert sdr_dict["brand_name"] == "CostGuard Demo Client"
    assert sdr_dict["brand_name"] != campaign.name
    assert sdr_dict["brand_name"] != "Plataforma Nexus"

    outreach_dict = campaign_dict_for_outreach(campaign)
    assert outreach_dict["brand_name"] == "CostGuard Demo Client"
    assert outreach_dict["sender_name"] == "Joaquin"


def test_followup_skips_not_compatible_prospect():
    from unittest.mock import MagicMock, patch

    from app.services.followup_engine import run_due_followups_for_campaign

    db = MagicMock()
    camp = MagicMock()
    camp.id = 4
    camp.company_id = 1
    camp.automation_paused = False
    camp.post_sequence_followup_enabled = True
    camp.max_auto_followups = 2
    camp.followup_delay_days = 30
    camp.allowed_channels = ["email"]
    camp.calendar_link = ""
    camp.product = None
    camp.company = None
    camp.name = "Outbound LATAM Q1.2"
    camp.tone = "profesional"
    camp.target_company_size = ""
    camp.target_industry = ""
    camp.target_country = ""
    camp.target_language = ""
    camp.target_role = ""
    camp.sender_name = "Joaquin"
    camp.sender_email = ""
    camp.icp_ai_last_analysis = None

    task = MagicMock()
    task.prospect_id = 10
    task.status = "pending"
    task.due_at = __import__("datetime").datetime.now(__import__("datetime").UTC)

    prospect = MagicMock()
    prospect.id = 10
    prospect.status = ProspectStatus.not_compatible.value
    prospect.sequence_group = None

    db.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[task])),
        MagicMock(first=MagicMock(return_value=camp)),
    ]
    db.get.return_value = prospect
    db.scalar.return_value = 0

    with patch("app.services.outreach_metrics.is_real_mode", return_value=False):
        result = run_due_followups_for_campaign(db, 4, education="")
    assert result["skipped"] >= 1
    assert result["processed"] == 0
