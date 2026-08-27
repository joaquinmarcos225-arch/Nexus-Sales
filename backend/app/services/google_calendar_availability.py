"""Consulta de disponibilidad en Google Calendar."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.services.gmail_drafts import get_valid_google_calendar_connection

logger = logging.getLogger(__name__)

FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_busy_ranges(payload: dict) -> list[tuple[datetime, datetime]]:
    calendars = payload.get("calendars") or {}
    primary = calendars.get("primary") or {}
    errors = primary.get("errors") or []
    if errors:
        msgs = []
        for err in errors:
            if isinstance(err, dict):
                msgs.append(str(err.get("reason") or err.get("domain") or err))
            else:
                msgs.append(str(err))
        raise RuntimeError(
            "Google Calendar freeBusy error: " + "; ".join(msgs[:3])
        )
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
    access, _ = get_valid_google_calendar_connection(db, company_id=company_id, user_id=seller_user_id)
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
    available_hours: str | None = None,
    same_day_only: bool = False,
) -> list[datetime]:
    """Busca huecos libres cerca de `around`. Por defecto prioriza el mismo día pedido."""
    from app.services.available_hours import candidate_hours, parse_available_hours

    tz = ZoneInfo(timezone)
    window = parse_available_hours(available_hours)
    hours = candidate_hours(window)
    anchor = around.astimezone(tz)
    now_local = datetime.now(tz).replace(second=0, microsecond=0)
    window_start = (anchor - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = anchor + timedelta(days=14)
    busy = fetch_busy_intervals(
        db,
        company_id=company_id,
        seller_user_id=seller_user_id,
        time_min=window_start.astimezone(UTC),
        time_max=window_end.astimezone(UTC),
    )

    around_utc = around.astimezone(UTC)
    min_start = now_local + timedelta(hours=1)

    def _collect_day(day_local: datetime) -> list[datetime]:
        if day_local.weekday() not in window.weekdays:
            return []
        found: list[datetime] = []
        day_base = day_local.replace(hour=0, minute=0, second=0, microsecond=0)
        for hour in hours:
            for minute in (0, 30):
                if hour >= window.end_hour:
                    break
                slot_local = day_base.replace(hour=hour, minute=minute)
                if slot_local < min_start:
                    continue
                slot_utc = slot_local.astimezone(UTC)
                if abs((slot_utc - around_utc).total_seconds()) < 60:
                    continue
                end_utc = slot_utc + timedelta(minutes=duration_minutes)
                if slot_is_free(busy, start=slot_utc, end=end_utc):
                    found.append(slot_utc)
        found.sort(key=lambda x: abs((x - around_utc).total_seconds()))
        return found

    results: list[datetime] = []
    same_day = _collect_day(anchor.replace(hour=0, minute=0, second=0, microsecond=0))
    for slot in same_day:
        if slot not in results:
            results.append(slot)
        if len(results) >= count:
            return results[:count]

    if same_day_only:
        return results[:count]

    day = anchor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    while len(results) < count and day < window_end:
        for slot in _collect_day(day):
            if slot not in results:
                results.append(slot)
            if len(results) >= count:
                break
        day += timedelta(days=1)

    return results[:count]


def format_day_label(dt: datetime, timezone: str) -> str:
    local = dt.astimezone(ZoneInfo(timezone))
    days = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    return days[local.weekday()]


def format_slot_local(dt: datetime, timezone: str) -> str:
    local = dt.astimezone(ZoneInfo(timezone))
    days = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    name = days[local.weekday()]
    return f"{name.capitalize()} {local.strftime('%H:%M')}"
