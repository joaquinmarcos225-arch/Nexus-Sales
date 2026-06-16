"""Preparación de calendario sin integración externa (slots sugeridos, timezone, duración)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def build_placeholder_slots(
    *,
    anchor: datetime,
    duration_minutes: int = 30,
    count: int = 3,
) -> list[dict[str, str | int]]:
    """Genera ventanas sugeridas para la UI / futuro Google Calendar."""
    out: list[dict[str, str | int]] = []
    for i in range(count):
        start = anchor + timedelta(days=i, hours=2 * i)
        end = start + timedelta(minutes=duration_minutes)
        out.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "duration_minutes": duration_minutes,
            }
        )
    return out


def default_scheduled_anchor(hours_ahead: int = 48) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours_ahead)
