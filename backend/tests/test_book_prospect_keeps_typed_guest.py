"""Mail tipado (incluso test@gmail.com) debe llegar a Calendar Invitados."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock


def test_is_unreliable_flags_test_local_but_book_must_still_invite():
    from app.services.meeting_booking import _is_unreliable_invite_email

    # CRM placeholder: sí es “unreliable”.
    assert _is_unreliable_invite_email("test@gmail.com") is True
    # Pero un mail tipado en chat NO se puede tirar al crear el evento.


def test_book_prospect_meeting_passes_test_gmail_to_calendar(monkeypatch):
    import app.services.google_calendar_create as gcc
    import app.services.meeting_booking as mb

    seen: dict = {}

    def _fake_create(*_a, **kwargs):
        seen.clear()
        seen.update(kwargs)
        return {
            "event_id": "evt-1",
            "html_link": "https://cal/1",
            "meet_link": "https://meet.google.com/abc-defg-hij",
            "attendee_email": kwargs.get("attendee_email"),
            "start": "2026-11-24T12:00:00-03:00",
            "end": "2026-11-24T12:30:00-03:00",
            "timezone": "America/Argentina/Buenos_Aires",
        }

    monkeypatch.setattr(gcc, "create_calendar_event", _fake_create)
    monkeypatch.setattr(gcc, "delete_day_events_matching_title", lambda **k: 0)

    # Skip availability path.
    monkeypatch.setattr(
        mb,
        "resolve_campaign_calendar_user_id",
        lambda *a, **k: 6,
    )

    created_meeting = SimpleNamespace(
        id=99,
        google_calendar_event_id=None,
        google_calendar_html_link=None,
    )

    def _add(obj):
        if getattr(obj, "__tablename__", None) == "meetings" or obj.__class__.__name__ == "Meeting":
            obj.id = 99
            for k, v in (
                ("google_calendar_event_id", None),
                ("google_calendar_html_link", None),
            ):
                if not hasattr(obj, k):
                    setattr(obj, k, None)

    db = MagicMock()
    db.add.side_effect = _add
    db.flush = MagicMock()
    db.get.return_value = created_meeting

    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=6,
        name="Demo",
        timezone="America/Argentina/Buenos_Aires",
        available_hours=None,
        calendar_link=None,
        product=None,
    )
    prospect = SimpleNamespace(
        id=175,
        name="Mia",
        email="test@gmail.com",
        company_name="Humand",
        campaign_id=1,
        status="replied",
        pipeline_stage=None,
        commercial_state=None,
        sequence_group=None,
        sequence_paused=False,
        meeting_scheduling_state=None,
    )

    # Avoid pipeline / sequence side effects.
    monkeypatch.setattr(mb, "default_meeting_title", lambda *a, **k: "Humand & CostGuard")
    monkeypatch.setattr(
        "app.services.pipeline_sync.sync_pipeline_from_status",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.multichannel_sequence.enforce_meeting_priority_over_sequence",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.multichannel_sequence.apply_real_calendar_booking",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.prospect_commercial_state.apply_commercial_state",
        lambda *a, **k: None,
    )

    out = mb.book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=datetime(2026, 11, 24, 15, 0, tzinfo=UTC),
        create_google_event=True,
        testing=False,
        check_availability=False,
        invite_attendee=True,
        attendee_email="test@gmail.com",
    )

    assert seen.get("attendee_email") == "test@gmail.com", seen
    assert seen.get("require_guest") is True
    assert seen.get("require_meet") is True
    assert out.get("calendar_created") is True or out.get("google_calendar_event_id") == "evt-1"
