from datetime import UTC, datetime

from app.models.enums import MeetingStatus
from app.schemas.meeting import MeetingRead
from app.services.google_calendar_sync import _map_meeting_status


def test_meeting_read_serializes_naive_db_datetime_as_utc():
    row = MeetingRead(
        id=1,
        company_id=1,
        campaign_id=3,
        prospect_id=6,
        title="Reunión · Test",
        description=None,
        scheduled_for=datetime(2026, 6, 26, 18, 30, 0),
        meeting_status="confirmed",
        timezone="America/Argentina/Buenos_Aires",
        suggested_slots=None,
        duration_minutes=15,
        google_calendar_event_id="evt",
        google_calendar_html_link="https://cal",
        creation_method="auto_nexus",
        created_by_user_id=6,
        created_at=datetime(2026, 6, 26, 10, 0, 0),
        updated_at=datetime(2026, 6, 26, 10, 0, 0),
    )
    payload = row.model_dump(mode="json")
    assert str(payload["scheduled_for"]).startswith("2026-06-26T18:30:00")
    assert str(payload["scheduled_for"]).endswith(("+00:00", "Z"))


def test_map_meeting_status_guest_accepted_is_confirmed():
    ev = {
        "status": "confirmed",
        "attendees": [
            {"email": "sdr@test.com", "responseStatus": "accepted", "self": True},
            {"email": "prospect@test.com", "responseStatus": "accepted"},
        ],
    }
    status = _map_meeting_status(ev, seller_emails={"sdr@test.com"})
    assert status == MeetingStatus.confirmed.value


def test_map_meeting_status_guest_needs_action_is_pending():
    ev = {
        "status": "confirmed",
        "attendees": [
            {"email": "sdr@test.com", "responseStatus": "accepted"},
            {"email": "prospect@test.com", "responseStatus": "needsAction"},
        ],
    }
    status = _map_meeting_status(ev, seller_emails={"sdr@test.com"})
    assert status == MeetingStatus.pending.value


def test_map_meeting_status_seller_declined_is_canceled():
    ev = {
        "status": "confirmed",
        "attendees": [
            {"email": "sdr@test.com", "responseStatus": "declined", "self": True},
            {"email": "prospect@test.com", "responseStatus": "needsAction"},
        ],
    }
    status = _map_meeting_status(ev, seller_emails={"sdr@test.com"})
    assert status == MeetingStatus.canceled.value


def test_map_meeting_status_guest_declined_is_canceled():
    ev = {
        "status": "confirmed",
        "attendees": [
            {"email": "sdr@test.com", "responseStatus": "accepted"},
            {"email": "prospect@test.com", "responseStatus": "declined"},
        ],
    }
    status = _map_meeting_status(ev, seller_emails={"sdr@test.com"})
    assert status == MeetingStatus.canceled.value


def test_map_meeting_status_no_responses_is_pending():
    ev = {
        "status": "confirmed",
        "attendees": [
            {"email": "sdr@test.com"},
            {"email": "prospect@test.com"},
        ],
    }
    status = _map_meeting_status(ev, seller_emails={"sdr@test.com"})
    assert status == MeetingStatus.pending.value
