"""Parseo de horarios disponibles de campaña (texto libre → ventana local)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class AvailableHoursWindow:
    start_hour: int = 9
    end_hour: int = 18
    weekdays: frozenset[int] = frozenset(range(0, 5))  # lun–vie


def parse_available_hours(text: str | None) -> AvailableHoursWindow:
    """
    Interpreta strings como:
    - "Lun–Vie 9:00–18:00"
    - "9-18"
  - "Solo mañanas (9–13)"
    """
    raw = (text or "").strip().lower()
    start_h, end_h = 9, 18
    weekdays = set(range(0, 5))

    if "mañana" in raw or "manana" in raw:
        end_h = 13
    elif "tarde" in raw:
        start_h = 14

    m = re.search(r"(\d{1,2})(?::\d{2})?\s*[-–]\s*(\d{1,2})", raw)
    if m:
        start_h = int(m.group(1))
        end_h = int(m.group(2))

    if "sáb" in raw or "sab" in raw:
        weekdays.add(5)
    if "dom" in raw:
        weekdays.add(6)
    if "lun" in raw and "dom" not in raw and "sáb" not in raw and "sab" not in raw:
        weekdays = set(range(0, 5))

    start_h = max(0, min(start_h, 23))
    end_h = max(start_h + 1, min(end_h, 24))
    return AvailableHoursWindow(start_hour=start_h, end_hour=end_h, weekdays=frozenset(weekdays))


def validate_available_hours_text(text: str | None) -> str | None:
    """
    Devuelve mensaje de error si el texto es inválido, o None si está ok / vacío.
    Vacío = defaults Lun–Vie 9–18.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    window = parse_available_hours(raw)
    if window.end_hour <= window.start_hour:
        return "Horarios disponibles inválidos: la hora de fin debe ser mayor que la de inicio."
    if not window.weekdays:
        return "Horarios disponibles inválidos: indicá al menos un día (ej. Lun-Vie 9:00-18:00)."
    return None


def candidate_hours(window: AvailableHoursWindow) -> tuple[int, ...]:
    return tuple(h for h in range(window.start_hour, window.end_hour))


def slot_within_available_hours(
    slot: datetime,
    *,
    timezone: str,
    available_hours: str | None,
) -> bool:
    window = parse_available_hours(available_hours)
    local = slot.astimezone(ZoneInfo(timezone))
    if local.weekday() not in window.weekdays:
        return False
    hour = local.hour + (local.minute / 60.0)
    return window.start_hour <= hour < window.end_hour
