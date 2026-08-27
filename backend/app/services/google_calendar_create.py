"""Crear eventos en Google Calendar (outbound) para reuniones Nexus."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session

from app.services.gmail_drafts import get_valid_google_calendar_connection

logger = logging.getLogger(__name__)

EVENTS_INSERT_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _event_url(event_id: str) -> str:
    return f"{EVENTS_INSERT_URL}/{event_id}"


def delete_calendar_event(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    event_id: str,
) -> bool:
    """Elimina un evento del calendario primary. Devuelve True si se eliminó o ya no existía."""
    eid = (event_id or "").strip()
    if not eid:
        return False
    access, _ = get_valid_google_calendar_connection(db, company_id=company_id, user_id=seller_user_id)
    with httpx.Client(timeout=30.0) as client:
        res = client.delete(
            _event_url(eid),
            headers={"Authorization": f"Bearer {access}"},
        )
    if res.status_code in (200, 204, 404, 410):
        logger.info(
            "google_calendar_delete event_id=%s seller_user_id=%s status=%s",
            eid[:24],
            seller_user_id,
            res.status_code,
        )
        return True
    if res.status_code == 401:
        raise RuntimeError("Google Calendar rechazó el token (401) al eliminar evento.")
    res.raise_for_status()
    return True


def to_campaign_local(dt: datetime, timezone: str) -> datetime:
    """Convierte un instante a la zona de la campaña (aware)."""
    tz = ZoneInfo((timezone or "America/Argentina/Buenos_Aires").strip())
    if dt.tzinfo is None:
        # Sin tz: se interpreta como hora local de la campaña.
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def format_google_local_datetime(dt: datetime) -> str:
    """
    dateTime local sin offset (Google aplica timeZone del body).
    Ej: 2026-07-28T15:00:00
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def create_calendar_event(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    title: str,
    description: str | None,
    start_at: datetime,
    duration_minutes: int = 30,
    attendee_email: str | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
) -> dict[str, Any]:
    """
    Crea un evento en el calendario primary del SDR.
    Devuelve {event_id, html_link, start, end} o lanza si no hay conexión Google.

    Importante: manda dateTime en hora local de `timezone` (sin Z), para que Google
    no desplace el horario al combinar UTC + timeZone.
    """
    access, _ = get_valid_google_calendar_connection(db, company_id=company_id, user_id=seller_user_id)
    tz_name = (timezone or "America/Argentina/Buenos_Aires").strip()
    start_local = to_campaign_local(start_at, tz_name)
    end_local = start_local + timedelta(minutes=max(15, min(int(duration_minutes), 240)))

    body: dict[str, Any] = {
        "summary": (title or "Reunión comercial").strip()[:255],
        "description": (description or "").strip()[:4000] or None,
        "start": {
            "dateTime": format_google_local_datetime(start_local),
            "timeZone": tz_name,
        },
        "end": {
            "dateTime": format_google_local_datetime(end_local),
            "timeZone": tz_name,
        },
        "reminders": {"useDefault": True},
    }
    if attendee_email and "@" in attendee_email:
        body["attendees"] = [{"email": attendee_email.strip()}]

    params = {"sendUpdates": "all"} if attendee_email else {}

    with httpx.Client(timeout=45.0) as client:
        res = client.post(
            EVENTS_INSERT_URL,
            headers={"Authorization": f"Bearer {access}"},
            params=params,
            json=body,
        )
        if res.status_code == 401:
            raise RuntimeError(
                "Google Calendar rechazó el token (401). Reconectá Google en Conexiones."
            )
        res.raise_for_status()
        data = res.json()

    event_id = str(data.get("id") or "").strip()
    html_link = str(data.get("htmlLink") or "").strip()
    if not event_id:
        raise RuntimeError("Google Calendar no devolvió id de evento")

    logger.info(
        "google_calendar_create event_id=%s seller_user_id=%s attendee=%s start_local=%s tz=%s",
        event_id[:24],
        seller_user_id,
        (attendee_email or "")[:40],
        format_google_local_datetime(start_local),
        tz_name,
    )
    return {
        "event_id": event_id,
        "html_link": html_link or None,
        "start": start_local.isoformat(),
        "end": end_local.isoformat(),
        "timezone": tz_name,
    }


def update_calendar_event_duration(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    event_id: str,
    start_at: datetime,
    duration_minutes: int,
    timezone: str = "America/Argentina/Buenos_Aires",
    title: str | None = None,
) -> dict[str, Any]:
    """
    Actualiza solo el fin del evento (misma hora de inicio, nueva duración).
    Usa PATCH para no pisar attendees/descripcion innecesariamente.
    """
    eid = (event_id or "").strip()
    if not eid:
        raise ValueError("event_id vacío")
    access, _ = get_valid_google_calendar_connection(db, company_id=company_id, user_id=seller_user_id)
    tz_name = (timezone or "America/Argentina/Buenos_Aires").strip()
    start_local = to_campaign_local(start_at, tz_name)
    duration = max(15, min(int(duration_minutes), 240))
    end_local = start_local + timedelta(minutes=duration)

    body: dict[str, Any] = {
        "start": {
            "dateTime": format_google_local_datetime(start_local),
            "timeZone": tz_name,
        },
        "end": {
            "dateTime": format_google_local_datetime(end_local),
            "timeZone": tz_name,
        },
    }
    if title and str(title).strip():
        body["summary"] = str(title).strip()[:255]

    with httpx.Client(timeout=45.0) as client:
        res = client.patch(
            _event_url(eid),
            headers={"Authorization": f"Bearer {access}"},
            params={"sendUpdates": "all"},
            json=body,
        )
        if res.status_code == 401:
            raise RuntimeError(
                "Google Calendar rechazó el token (401). Reconectá Google en Conexiones."
            )
        res.raise_for_status()
        data = res.json()

    html_link = str(data.get("htmlLink") or "").strip() or None
    logger.info(
        "google_calendar_update_duration event_id=%s duration_min=%s seller_user_id=%s",
        eid[:24],
        duration,
        seller_user_id,
    )
    return {
        "event_id": eid,
        "html_link": html_link,
        "start": start_local.isoformat(),
        "end": end_local.isoformat(),
        "timezone": tz_name,
        "duration_minutes": duration,
    }
