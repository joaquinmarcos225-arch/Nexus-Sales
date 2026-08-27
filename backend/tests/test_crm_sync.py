"""Sync CRM: inbound, meeting, idempotencia y retry."""

from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.crm_sync_event import CrmSyncEvent
from app.models.enums import ProspectStatus, UserRole
from app.models.outreach import OutreachMessage
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.crm import sync as crm_sync


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_prospect(db, *, email: str = "lead@test.com") -> Prospect:
    company = Company(name="Acme", plan="starter", employee_count=5)
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
        name="Ana Lopez",
        email=email,
        company_name="Test Co",
        role="CEO",
        status=ProspectStatus.contacted.value,
        compatibility_score=80,
        interest_probability=50,
    )
    db.add(prospect)
    db.flush()
    return prospect


def _outbound(db, prospect: Prospect) -> None:
    db.add(
        OutreachMessage(
            prospect_id=prospect.id,
            campaign_id=prospect.campaign_id,
            sender_type="sdr",
            direction="outbound",
            channel="email",
            message="Hola",
        )
    )
    db.flush()


@patch("app.services.crm.sync.cc.hubspot_active", return_value=True)
@patch("app.services.crm.sync.cc.salesforce_active", return_value=False)
@patch("app.services.crm.sync.cc.get_hubspot_access_token", return_value="pat-test")
@patch("app.services.crm.sync.hubspot.upsert_contact", return_value="hs-1")
@patch("app.services.crm.sync.hubspot.create_note_for_contact", return_value=True)
def test_sync_inbound_creates_event_once(
    _note,
    _upsert,
    _token,
    _sf_on,
    _hs_on,
):
    db = _session()
    prospect = _seed_prospect(db)
    _outbound(db, prospect)

    crm_sync.sync_inbound_reply(
        db,
        prospect=prospect,
        channel="email",
        message_id="msg-abc",
        message_body="Me interesa",
    )
    crm_sync.sync_inbound_reply(
        db,
        prospect=prospect,
        channel="email",
        message_id="msg-abc",
        message_body="Me interesa",
    )

    rows = db.scalars(select(CrmSyncEvent).where(CrmSyncEvent.prospect_id == prospect.id)).all()
    assert len(rows) == 1
    assert rows[0].event_key == "inbound:email:msg-abc"
    assert rows[0].hubspot_synced is True


@patch("app.services.crm.sync.cc.hubspot_active", return_value=True)
@patch("app.services.crm.sync.cc.salesforce_active", return_value=False)
def test_sync_inbound_skips_without_prior_outbound(_sf_on, _hs_on):
    db = _session()
    prospect = _seed_prospect(db)

    crm_sync.sync_inbound_reply(
        db,
        prospect=prospect,
        channel="email",
        message_id="msg-x",
        message_body="Hola",
    )

    rows = db.scalars(select(CrmSyncEvent)).all()
    assert rows == []


@patch("app.services.crm.sync.cc.hubspot_active", return_value=True)
@patch("app.services.crm.sync.cc.salesforce_active", return_value=False)
@patch("app.services.crm.sync.cc.get_hubspot_access_token", return_value="pat-test")
@patch("app.services.crm.sync.hubspot.upsert_contact", return_value="hs-1")
@patch("app.services.crm.sync.hubspot.create_note_for_contact", return_value=True)
def test_sync_meeting_booked(_note, _upsert, _token, _sf_on, _hs_on):
    db = _session()
    prospect = _seed_prospect(db)
    when = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)

    crm_sync.sync_meeting_booked(
        db,
        prospect=prospect,
        meeting_id=42,
        scheduled_for=when,
        title="Demo Nexus",
    )

    row = db.scalars(select(CrmSyncEvent).where(CrmSyncEvent.prospect_id == prospect.id)).one()
    assert row.event_key == "meeting:42"
    assert row.hubspot_synced is True


@patch("app.services.crm.sync.cc.hubspot_active", return_value=True)
@patch("app.services.crm.sync.cc.salesforce_active", return_value=False)
@patch("app.services.crm.sync.cc.get_hubspot_access_token", return_value="pat-test")
@patch("app.services.crm.sync.hubspot.upsert_contact", return_value="hs-1")
def test_retry_resolves_failed_touch(_upsert, _token, _sf_on, _hs_on):
    db = _session()
    prospect = _seed_prospect(db)
    event = CrmSyncEvent(
        company_id=prospect.company_id,
        prospect_id=prospect.id,
        event_key="touch:1:email",
        hubspot_synced=False,
        salesforce_synced=False,
        hubspot_error="HubSpot rechazó la nota",
    )
    db.add(event)
    db.flush()

    with patch("app.services.crm.sync.hubspot.create_note_for_contact", return_value=True):
        stats = crm_sync.retry_pending_for_company(db, prospect.company_id)
    db.flush()

    row = db.get(CrmSyncEvent, event.id)
    assert stats["retried"] >= 1
    assert row is not None
    assert row.hubspot_synced is True
    assert row.hubspot_error is None


def test_event_key_helpers():
    assert crm_sync.touch_event_key(day=4, channel="linkedin") == "touch:4:linkedin"
    assert crm_sync.inbound_event_key(channel="email", message_id="g-1") == "inbound:email:g-1"
    assert crm_sync.meeting_event_key(meeting_id=7) == "meeting:7"


@patch("app.services.crm.sync.cc.hubspot_active", return_value=True)
@patch("app.services.crm.sync.cc.salesforce_active", return_value=False)
def test_company_sync_status_ignores_inactive_salesforce_pending(_sf_on, _hs_on):
    db = _session()
    prospect = _seed_prospect(db)
    db.add(
        CrmSyncEvent(
            company_id=prospect.company_id,
            prospect_id=prospect.id,
            event_key="touch:1:email",
            hubspot_synced=True,
            salesforce_synced=False,
        )
    )
    db.flush()

    data = crm_sync.company_sync_status(db, prospect.company_id)
    assert data["pending_count"] == 0
    assert data["failed_recent"] == []


@patch("app.services.crm.sync.cc.hubspot_active", return_value=True)
@patch("app.services.crm.sync.cc.salesforce_active", return_value=False)
def test_company_sync_status_counts_pending(_sf_on, _hs_on):
    db = _session()
    prospect = _seed_prospect(db)
    db.add(
        CrmSyncEvent(
            company_id=prospect.company_id,
            prospect_id=prospect.id,
            event_key="touch:1:email",
            hubspot_synced=False,
            salesforce_synced=False,
            hubspot_error="timeout",
        )
    )
    db.flush()

    data = crm_sync.company_sync_status(db, prospect.company_id)
    assert data["hubspot_active"] is True
    assert data["pending_count"] >= 1
    assert len(data["failed_recent"]) >= 1
