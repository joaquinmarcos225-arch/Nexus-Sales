"""Auto-book desde mensaje inbound con Calendar mockeado."""

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import ProspectStatus, UserRole
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.meeting_booking import attempt_auto_book_from_message


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
        calendar_link="https://cal.test",
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
        campaign_id=campaign.id,
        name="Ana",
        company_name="Acme",
        email="ana@acme.com",
        status=ProspectStatus.interested.value,
        compatibility_score=80,
        interest_probability=50,
    )
    db.add(campaign)
    db.flush()
    prospect.campaign_id = campaign.id
    db.add(prospect)
    db.flush()
    return campaign, prospect


@patch("app.services.available_hours.slot_within_available_hours", return_value=True)
@patch("app.services.meeting_booking._seller_google_calendar_ready", return_value=True)
@patch("app.services.google_calendar_availability.fetch_busy_intervals", return_value=[])
@patch(
    "app.services.google_calendar_create.create_calendar_event",
    return_value={"event_id": "evt-99", "html_link": "https://cal.google/evt-99"},
)
def test_attempt_auto_book_creates_meeting_and_confirmation(
    _mock_evt, _mock_busy, _mock_ready, _mock_hours
):
    db = _session()
    campaign, prospect = _bundle(db)
    result = attempt_auto_book_from_message(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text="Agendame mañana a las 15 hs",
        reply_objective="agendar",
        testing=True,
    )

    assert result is not None
    assert result.get("calendar_created") is True
    assert result.get("confirmation_reply")
    assert "Te agendé" in result["confirmation_reply"]
    assert prospect.commercial_state == "reunion_agendada"
