"""Parseo de horarios propuestos por el prospecto (español)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.conversation_intelligence import fold_accents, normalize_inbound_text_for_classification

_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}


def _next_weekday(anchor: datetime, weekday: int) -> datetime:
    days_ahead = (weekday - anchor.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return anchor + timedelta(days=days_ahead)


def _parse_hour(norm: str) -> int | None:
    patterns = (
        r"\ba las\s+(\d{1,2})(?:\s*(?::\s*(\d{2}))?\s*(hs|h|am|pm)?)?\b",
        r"\b(\d{1,2})\s*(?::\s*(\d{2}))?\s*(hs|h|am|pm)\b",
        r"\b(\d{1,2})\s*(?::\s*(\d{2}))?\s*(hs|h|am|pm)?\b",
    )
    m = None
    for pattern in patterns:
        m = re.search(pattern, norm)
        if m:
            break
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0) if m.lastindex and m.group(2) else 0
    suffix = (m.group(3) or "").lower() if m.lastindex and m.lastindex >= 3 and m.group(3) else ""
    if suffix == "pm" and hour < 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if 7 <= hour <= 9 and "tarde" in norm and hour < 12:
        hour += 12
    if hour < 7 or hour > 21:
        return None
    return hour * 60 + minute


def prospect_proposed_meeting_slot(text: str | None) -> bool:
    """True si el mensaje incluye día u hora concreta para reunión."""
    return parse_meeting_slot(text) is not None


def parse_meeting_slot(
    text: str | None,
    *,
    now: datetime | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> datetime | None:
    """
    Interpreta frases como 'martes 15 hs', 'jueves a las 10', 'mañana por la tarde'.
    Devuelve datetime aware en la zona indicada.
    """
    norm = fold_accents(normalize_inbound_text_for_classification(text or ""))
    if not norm:
        return None

    tz = ZoneInfo(timezone)
    ref = (now or datetime.now(UTC)).astimezone(tz)
    base = ref.replace(second=0, microsecond=0)

    target = base
    if "pasado manana" in norm or "pasado mañana" in (text or "").lower():
        target = base + timedelta(days=2)
    elif "manana" in norm or "mañana" in (text or "").lower():
        target = base + timedelta(days=1)
    else:
        for name, wd in _WEEKDAYS.items():
            if re.search(rf"\b{name}\b", norm):
                target = _next_weekday(base, wd)
                break

    minutes = _parse_hour(norm)
    if minutes is None:
        if "por la tarde" in norm or "tarde" in norm:
            minutes = 15 * 60
        elif "por la manana" in norm or "mañana por la" in (text or "").lower():
            minutes = 10 * 60
        else:
            return None

    hour, minute = divmod(minutes, 60)
    candidate = target.replace(hour=hour, minute=minute)
    if candidate <= base + timedelta(hours=1):
        if "proxima semana" in norm or "semana que viene" in norm:
            candidate += timedelta(days=7)
        elif candidate <= base:
            candidate += timedelta(days=7)
    return candidate.astimezone(UTC)
