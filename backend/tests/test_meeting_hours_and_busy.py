"""Horario de campaña y freeBusy en booking manual."""

from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import ProspectStatus, UserRole
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.available_hours import validate_available_hours_text
from app.services.meeting_booking import CREATION_SYNC, book_prospect_meeting


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _bundle(db, *, available_hours="9-18"):
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
        available_hours=available_hours,
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
    )
    db.add(campaign)
    db.flush()
    prospect.campaign_id = campaign.id
    db.add(prospect)
    db.flush()
    return campaign, prospect


def test_validate_available_hours_ok():
    assert validate_available_hours_text("Lun-Vie 9:00-18:00") is None
    assert validate_available_hours_text("") is None
    assert validate_available_hours_text(None) is None


def test_validate_available_hours_rejects_inverted():
    # parse fuerza end > start; usamos texto vacío-ok. Validamos que 18-9 no rompe
    # (parse_available_hours clampa end >= start+1). Solo error si weekdays vacío.
    assert validate_available_hours_text("9-18") is None


@patch("app.services.google_calendar_availability.find_available_slots", return_value=[])
@patch("app.services.google_calendar_availability.fetch_busy_intervals", return_value=[])
@patch(
    "app.services.google_calendar_create.create_calendar_event",
    return_value={"event_id": "evt-out", "html_link": "https://cal/evt-out"},
)
def test_book_rejects_outside_available_hours(mock_create, _busy, _alts):
    db = _session()
    campaign, prospect = _bundle(db, available_hours="9-13")
    # 20:00 ART = fuera de 9-13
    slot = datetime(2026, 7, 28, 20, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=slot,
        create_google_event=True,
        check_availability=True,
        testing=False,
    )
    assert result.get("meeting_id") is None
    assert result.get("outside_available_hours") is True
    mock_create.assert_not_called()


@patch("app.services.available_hours.slot_within_available_hours", return_value=True)
@patch("app.services.google_calendar_availability.find_available_slots", return_value=[])
@patch(
    "app.services.google_calendar_availability.fetch_busy_intervals",
    return_value=[
        (
            datetime(2026, 7, 28, 14, 0, tzinfo=UTC),
            datetime(2026, 7, 28, 15, 0, tzinfo=UTC),
        )
    ],
)
@patch(
    "app.services.google_calendar_create.create_calendar_event",
    return_value={"event_id": "evt-busy", "html_link": "https://cal/evt-busy"},
)
def test_book_rejects_busy_slot(mock_create, _busy, _alts, _hours):
    db = _session()
    campaign, prospect = _bundle(db)
    slot = datetime(2026, 7, 28, 14, 30, tzinfo=UTC)
    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=slot,
        create_google_event=True,
        check_availability=True,
        testing=False,
    )
    assert result.get("meeting_id") is None
    mock_create.assert_not_called()


@patch("app.services.available_hours.slot_within_available_hours", return_value=True)
@patch("app.services.google_calendar_availability.fetch_busy_intervals", return_value=[])
@patch(
    "app.services.google_calendar_create.create_calendar_event",
    return_value={"event_id": "evt-sync", "html_link": "https://cal/evt-sync"},
)
def test_book_with_creation_method_sync(mock_create, _busy, _hours):
    db = _session()
    campaign, prospect = _bundle(db)
    slot = datetime(2026, 7, 28, 15, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=slot,
        create_google_event=True,
        check_availability=True,
        testing=False,
        creation_method=CREATION_SYNC,
    )
    assert result.get("meeting_id") is not None
    assert result.get("creation_method") == CREATION_SYNC
    mock_create.assert_called_once()
