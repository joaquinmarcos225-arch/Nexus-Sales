"""Reutilización de reunión existente al re-ejecutar auto-book (worker programado)."""

from unittest.mock import patch

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import MeetingStatus, ProspectStatus, UserRole
from app.models.meeting import Meeting
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.meeting_booking import (
    CREATION_AUTO_NEXUS,
    _existing_booking_result_for_slot,
    _slot_matches_meeting,
    book_prospect_meeting,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _campaign_bundle(db):
    company = Company(name="Test Co", employee_count=10, plan="starter")
    db.add(company)
    db.flush()
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
    db.flush()
    prospect = Prospect(
        company_id=company.id,
        campaign_id=campaign.id,
        name="Ana Test",
        company_name="Acme",
        email="ana@example.com",
        status=ProspectStatus.interested.value,
        compatibility_score=80,
        interest_probability=50,
    )
    db.add(prospect)
    db.flush()
    return campaign, prospect


def test_slot_matches_meeting_within_tolerance():
    a = datetime(2026, 6, 24, 15, 0, tzinfo=UTC)
    b = datetime(2026, 6, 24, 15, 1, tzinfo=UTC)
    assert _slot_matches_meeting(a, b) is True
    c = datetime(2026, 6, 24, 16, 0, tzinfo=UTC)
    assert _slot_matches_meeting(a, c) is False


def test_existing_booking_result_reuses_calendar_confirmed_meeting():
    db = _session()
    campaign, prospect = _campaign_bundle(db)
    slot = datetime.now(UTC) + timedelta(days=1)
    slot = slot.replace(hour=15, minute=0, second=0, microsecond=0)

    db.add(
        Meeting(
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            title="Reunión · Ana Test",
            scheduled_for=slot,
            meeting_status=MeetingStatus.pending.value,
            timezone=campaign.timezone,
            google_calendar_event_id="evt-123",
            google_calendar_html_link="https://calendar.google.com/event/evt-123",
            creation_method=CREATION_AUTO_NEXUS,
        )
    )
    db.commit()

    result = _existing_booking_result_for_slot(
        db,
        campaign=campaign,
        prospect=prospect,
        slot=slot,
        tz=campaign.timezone,
        cal_link=campaign.calendar_link,
    )

    assert result is not None
    assert result.get("reused_existing") is True
    assert result.get("confirmation_reply")
    assert "Te agendé" in result["confirmation_reply"]

    rows = list(db.scalars(select(Meeting).where(Meeting.prospect_id == prospect.id)).all())
    assert len(rows) == 1


@patch("app.services.google_calendar_create.delete_calendar_event")
@patch("app.services.google_calendar_create.create_calendar_event")
def test_booking_new_slot_cancels_previous_meetings(mock_create, mock_delete):
    mock_create.return_value = {"event_id": "evt-new", "html_link": "https://cal/new"}
    db = _session()
    campaign, prospect = _campaign_bundle(db)
    old_slot = datetime.now(UTC) + timedelta(days=2)
    old_slot = old_slot.replace(hour=12, minute=0, second=0, microsecond=0)
    new_slot = old_slot + timedelta(days=1)

    db.add(
        Meeting(
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            title="Reunión · Ana Test",
            scheduled_for=old_slot,
            meeting_status=MeetingStatus.pending.value,
            timezone=campaign.timezone,
            google_calendar_event_id="evt-old",
            creation_method=CREATION_AUTO_NEXUS,
        )
    )
    db.commit()

    book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=new_slot,
        create_google_event=True,
        testing=True,
        creation_method=CREATION_AUTO_NEXUS,
        created_by_user_id=campaign.seller_id,
    )
    db.commit()

    rows = list(db.scalars(select(Meeting).where(Meeting.prospect_id == prospect.id)).all())
    active = [r for r in rows if r.meeting_status != MeetingStatus.canceled.value]
    canceled = [r for r in rows if r.meeting_status == MeetingStatus.canceled.value]
    assert len(active) == 1
    assert len(canceled) == 1
    assert active[0].google_calendar_event_id == "evt-new"
    mock_delete.assert_called_once()
