"""Crear eventos en Google Calendar (outbound) para reuniones Nexus."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.gmail_drafts import get_valid_gmail_connection

logger = logging.getLogger(__name__)

EVENTS_INSERT_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


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
    """
    access, _ = get_valid_gmail_connection(db, company_id=company_id, user_id=seller_user_id)
    start = _as_utc(start_at)
    end = start + timedelta(minutes=max(15, min(int(duration_minutes), 240)))

    body: dict[str, Any] = {
        "summary": (title or "Reunión comercial").strip()[:255],
        "description": (description or "").strip()[:4000] or None,
        "start": {
            "dateTime": start.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end.isoformat(),
            "timeZone": timezone,
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
        "google_calendar_create event_id=%s seller_user_id=%s attendee=%s",
        event_id[:24],
        seller_user_id,
        (attendee_email or "")[:40],
    )
    return {
        "event_id": event_id,
        "html_link": html_link or None,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
