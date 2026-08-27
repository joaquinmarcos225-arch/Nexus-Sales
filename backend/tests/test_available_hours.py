from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services.available_hours import (
    candidate_hours,
    parse_available_hours,
    slot_within_available_hours,
)


def test_parse_standard_weekday_window():
    w = parse_available_hours("Lun–Vie 9:00–18:00")
    assert w.start_hour == 9
    assert w.end_hour == 18
    assert 0 in w.weekdays and 4 in w.weekdays


def test_parse_mornings_only():
    w = parse_available_hours("Solo mañanas (9–13)")
    assert w.start_hour == 9
    assert w.end_hour == 13


def test_slot_within_available_hours():
    tz = "America/Argentina/Buenos_Aires"
    slot = datetime(2026, 6, 24, 18, 0, tzinfo=ZoneInfo(tz))  # Wed 15:00 ART ≈ need local
    local = datetime(2026, 6, 24, 15, 0, tzinfo=ZoneInfo(tz))
    assert slot_within_available_hours(local.astimezone(UTC), timezone=tz, available_hours="9-18")
    late = datetime(2026, 6, 24, 20, 0, tzinfo=ZoneInfo(tz))
    assert not slot_within_available_hours(late.astimezone(UTC), timezone=tz, available_hours="9-18")


def test_candidate_hours():
    w = parse_available_hours("9-12")
    assert candidate_hours(w) == (9, 10, 11)
