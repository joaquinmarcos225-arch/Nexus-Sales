"""Consulta de disponibilidad en Google Calendar."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.services.gmail_drafts import get_valid_gmail_connection

logger = logging.getLogger(__name__)

FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_busy_ranges(payload: dict) -> list[tuple[datetime, datetime]]:
    calendars = payload.get("calendars") or {}
    primary = calendars.get("primary") or {}
    busy = primary.get("busy") or []
    out: list[tuple[datetime, datetime]] = []
    for block in busy:
        if not isinstance(block, dict):
            continue
        start_raw = block.get("start")
        end_raw = block.get("end")
        if not start_raw or not end_raw:
            continue
        try:
            start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append((_as_utc(start), _as_utc(end)))
    return out


def fetch_busy_intervals(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    time_min: datetime,
    time_max: datetime,
) -> list[tuple[datetime, datetime]]:
    access, _ = get_valid_gmail_connection(db, company_id=company_id, user_id=seller_user_id)
    body = {
        "timeMin": _as_utc(time_min).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeMax": _as_utc(time_max).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": [{"id": "primary"}],
    }
    with httpx.Client(timeout=30.0) as client:
        res = client.post(
            FREEBUSY_URL,
            headers={"Authorization": f"Bearer {access}"},
            json=body,
        )
        if res.status_code == 401:
            raise RuntimeError("Google Calendar no autorizado (401). Reconectá Google.")
        res.raise_for_status()
        data = res.json()
    return _parse_busy_ranges(data if isinstance(data, dict) else {})


def slot_is_free(
    busy: list[tuple[datetime, datetime]],
    *,
    start: datetime,
    end: datetime,
) -> bool:
    s = _as_utc(start)
    e = _as_utc(end)
    for b_start, b_end in busy:
        if s < b_end and e > b_start:
            return False
    return True


def find_available_slots(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    around: datetime,
    duration_minutes: int = 30,
    count: int = 3,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> list[datetime]:
    """Busca próximos huecos libres cerca del horario pedido."""
    tz = ZoneInfo(timezone)
    anchor = around.astimezone(tz)
    window_start = anchor.replace(hour=8, minute=0) - timedelta(days=1)
    window_end = anchor + timedelta(days=14)
    busy = fetch_busy_intervals(
        db,
        company_id=company_id,
        seller_user_id=seller_user_id,
        time_min=window_start.astimezone(UTC),
        time_max=window_end.astimezone(UTC),
    )

    candidates: list[datetime] = []
    day = anchor.replace(hour=9, minute=0, second=0, microsecond=0)
    if day < anchor:
        day += timedelta(days=1)

    while len(candidates) < count + 5 and day < window_end:
        for hour in (9, 11, 15, 16, 17):
            slot_local = day.replace(hour=hour, minute=0)
            if slot_local <= anchor.astimezone(tz):
                continue
            slot_utc = slot_local.astimezone(UTC)
            end_utc = slot_utc + timedelta(minutes=duration_minutes)
            if slot_is_free(busy, start=slot_utc, end=end_utc):
                candidates.append(slot_utc)
            if len(candidates) >= count:
                break
        day += timedelta(days=1)

    candidates.sort(key=lambda x: abs((x - around.astimezone(UTC)).total_seconds()))
    return candidates[:count]


def format_slot_local(dt: datetime, timezone: str) -> str:
    local = dt.astimezone(ZoneInfo(timezone))
    days = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    name = days[local.weekday()]
    return f"{name.capitalize()} {local.strftime('%H:%M')}"
