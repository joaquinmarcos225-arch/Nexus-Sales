from datetime import UTC, datetime

from app.services.meeting_booking import build_meeting_confirmation_reply


def test_reschedule_confirmation_no_product_pitch():
    slot = datetime(2026, 6, 27, 14, 0, tzinfo=UTC)
    body = build_meeting_confirmation_reply(
        prospect_name="Ana Lagos",
        scheduled_for=slot,
        html_link="https://calendar.google.com/event/abc",
        timezone="America/Argentina/Buenos_Aires",
        is_reschedule=True,
    )
    assert "moví la reunión" in body.lower()
    assert "Plataforma" not in body
    assert "integra prospectos" not in body.lower()
