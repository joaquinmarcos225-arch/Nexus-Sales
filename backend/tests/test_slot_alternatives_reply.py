"""Alternativas de agenda: listar huecos reales, no inventar 'no hay'."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.meeting_booking import build_slot_alternatives_reply


def test_alternatives_reply_lists_other_day_slots():
    tz = "America/Argentina/Buenos_Aires"
    requested = datetime(2026, 7, 31, 15, 0, tzinfo=ZoneInfo(tz))
    other_day = datetime(2026, 8, 3, 11, 0, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    text = build_slot_alternatives_reply(
        prospect_name="Ana",
        alternatives=[other_day],
        timezone=tz,
        requested_slot=requested,
    )
    assert "sí están disponibles" in text
    assert "11:00" in text
    assert "no tengo otro horario disponible" not in text.lower()
    assert "link de agenda" not in text.lower()


def test_alternatives_reply_empty_asks_for_slot_not_fake_link():
    text = build_slot_alternatives_reply(
        prospect_name="Ana",
        alternatives=[],
        timezone="America/Argentina/Buenos_Aires",
        requested_slot=datetime(2026, 7, 31, 15, 0, tzinfo=UTC),
    )
    assert "link de agenda" not in text.lower()
    assert "proponés" in text or "propones" in text


def test_alternatives_reply_same_day_lists_times():
    tz = "America/Argentina/Buenos_Aires"
    requested = datetime(2026, 7, 31, 15, 0, tzinfo=ZoneInfo(tz))
    alt = datetime(2026, 7, 31, 16, 0, tzinfo=ZoneInfo(tz)).astimezone(UTC)
    text = build_slot_alternatives_reply(
        prospect_name="Ana",
        alternatives=[alt],
        timezone=tz,
        requested_slot=requested,
    )
    assert "16:00" in text
    assert "sí a las" in text
