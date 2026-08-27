"""Sincronización manual Gmail no requiere ENABLE_GMAIL_AUTOMATION=1."""

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.services.gmail_inbound_sync import sync_campaign_gmail_inbound


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _bundle(db):
    company = Company(name="Co", employee_count=5, plan="starter")
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        email="sdr@test.com",
        first_name="A",
        last_name="B",
        name="A B",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    product = Product(company_id=company.id, name="P", description="d", is_active=True)
    db.add_all([user, product])
    db.flush()
    campaign = Campaign(
        company_id=company.id,
        seller_id=user.id,
        product_id=product.id,
        name="C",
        prospect_count=5,
        calendar_link="https://cal.test",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="profesional",
        estimated_meetings_min=1,
        estimated_meetings_max=2,
        estimated_cost_min=1,
        estimated_cost_max=2,
    )
    db.add(campaign)
    db.commit()
    return company, user, campaign


@patch("app.services.gmail_inbound_sync.gmail_automation_enabled", return_value=False)
@patch(
    "app.services.gmail_inbound_sync.mseq.reconcile_meeting_vs_postergado_for_campaign",
    return_value=0,
)
def test_manual_sync_allowed_when_flag_off(_mock_reconcile, _mock_flag):
    db = _session()
    company, user, campaign = _bundle(db)

    with patch(
        "app.services.gmail_inbound_sync.get_valid_gmail_connection",
        side_effect=ValueError("no gmail"),
    ):
        try:
            sync_campaign_gmail_inbound(
                db,
                company_id=company.id,
                user_id=user.id,
                campaign_id=campaign.id,
                allow_manual=True,
            )
        except ValueError as e:
            assert "no gmail" in str(e)
            return

    raise AssertionError("expected ValueError from gmail connection")


@patch("app.services.gmail_inbound_sync.gmail_automation_enabled", return_value=False)
def test_scheduler_sync_skipped_when_flag_off(_mock_flag):
    db = _session()
    company, user, campaign = _bundle(db)
    out = sync_campaign_gmail_inbound(
        db,
        company_id=company.id,
        user_id=user.id,
        campaign_id=campaign.id,
        allow_manual=False,
    )
    assert out.get("skipped") is True
    assert out.get("reason") == "gmail_automation_disabled"
