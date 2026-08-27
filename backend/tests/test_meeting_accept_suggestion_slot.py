"""Accept-suggestion usa slot del hilo; no inventa +72h."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import ProspectStatus, UserRole
from app.models.outreach import OutreachMessage
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.meeting_booking import resolve_meeting_slot_from_prospect_thread


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
        first_name="SDR",
        last_name="T",
        name="SDR T",
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
        name="Camp",
        prospect_count=10,
        calendar_link="",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="profesional",
        estimated_meetings_min=1,
        estimated_meetings_max=2,
        estimated_cost_min=1,
        estimated_cost_max=2,
    )
    prospect = Prospect(
        company_id=company.id,
        campaign_id=None,
        name="Ana",
        company_name="Acme",
        email="ana@acme.com",
        status=ProspectStatus.interested.value,
        compatibility_score=80,
        interest_probability=80,
        interest_level="high",
        meeting_suggestion_pending=True,
    )
    db.add(campaign)
    db.flush()
    prospect.campaign_id = campaign.id
    db.add(prospect)
    db.flush()
    return campaign, prospect


def test_resolve_slot_from_inbound_message():
    db = _session()
    campaign, prospect = _bundle(db)
    # "mañana a las 15" needs a relative day; use absolute-ish parse if available
    db.add(
        OutreachMessage(
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            direction="inbound",
            sender_type="prospect",
            channel="email",
            message="Dale, el viernes a las 15:00 me viene bien.",
            created_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    db.commit()
    slot = resolve_meeting_slot_from_prospect_thread(db, prospect=prospect, campaign=campaign)
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.hour == 15
    assert local.weekday() == 4  # viernes


def test_resolve_slot_none_without_inbound():
    db = _session()
    campaign, prospect = _bundle(db)
    slot = resolve_meeting_slot_from_prospect_thread(db, prospect=prospect, campaign=campaign)
    assert slot is None


@patch("app.services.google_calendar_availability.fetch_busy_intervals", return_value=[])
@patch("app.services.available_hours.slot_within_available_hours", return_value=True)
@patch(
    "app.services.google_calendar_create.create_calendar_event",
    return_value={"event_id": "evt-acc", "html_link": "https://cal/evt-acc"},
)
def test_book_requires_scheduled_for_when_flagged(mock_create, _hours, _busy):
    from app.services.meeting_booking import book_prospect_meeting

    db = _session()
    campaign, prospect = _bundle(db)
    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=None,
        create_google_event=True,
        require_scheduled_for=True,
        testing=False,
    )
    assert result.get("meeting_id") is None
    assert result.get("needs_slot") is True
    mock_create.assert_not_called()
