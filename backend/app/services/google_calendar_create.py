"""Crear eventos en Google Calendar (outbound) para reuniones Nexus."""

from __future__ import annotations

import logging
import uuid
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


def meet_link_from_event(data: dict[str, Any] | None) -> str | None:
    """hangoutLink a veces viene vacío: el Meet real está en conferenceData.entryPoints."""
    if not isinstance(data, dict):
        return None
    hangout = str(data.get("hangoutLink") or "").strip()
    if hangout:
        return hangout
    conf = data.get("conferenceData") if isinstance(data.get("conferenceData"), dict) else {}
    for ep in conf.get("entryPoints") or []:
        if not isinstance(ep, dict):
            continue
        uri = str(ep.get("uri") or "").strip()
        kind = str(ep.get("entryPointType") or "").strip()
        if uri and (kind == "hangoutsMeet" or "meet.google.com" in uri.lower()):
            return uri
    return None


def _norm_guest_email(raw: str | None) -> str | None:
    s = (raw or "").strip().lower()
    if s and "@" in s and " " not in s:
        return s
    return None


def _event_has_guest(data: dict[str, Any] | None, email: str | None) -> bool:
    want = _norm_guest_email(email)
    if not want or not isinstance(data, dict):
        return False
    for att in data.get("attendees") or []:
        if not isinstance(att, dict):
            continue
        if str(att.get("email") or "").strip().lower() == want:
            return True
    return False


def _seller_guest_emails(
    db: Session | None,
    *,
    seller_user_id: int,
    gmail_row: Any = None,
    host_email: str | None = None,
) -> list[str]:
    """Mail del usuario Nexus (+ Gmail conectado) para invitarlo al evento."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        em = _norm_guest_email(raw)
        if em and em not in seen:
            seen.add(em)
            out.append(em)

    add(host_email)
    add(getattr(gmail_row, "external_email", None) if gmail_row is not None else None)
    if db is not None and seller_user_id:
        try:
            from app.models.user import User

            user = db.get(User, int(seller_user_id))
            add(getattr(user, "email", None) if user is not None else None)
        except Exception:
            pass
    return out


def _attendee_bodies(
    *,
    prospect_email: str | None,
    prospect_name: str | None,
    host_emails: list[str] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    prospect = _norm_guest_email(prospect_email)
    if prospect:
        att: dict[str, Any] = {"email": prospect, "responseStatus": "needsAction"}
        name = (prospect_name or "").strip()
        if name:
            att["displayName"] = name[:80]
        items.append(att)
        seen.add(prospect)
    for raw in host_emails or []:
        host = _norm_guest_email(raw)
        if not host or host in seen:
            continue
        items.append({"email": host, "responseStatus": "accepted"})
        seen.add(host)
    return items


def _conference_create_body() -> dict[str, Any]:
    return {
        "createRequest": {
            "requestId": uuid.uuid4().hex,
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    }


def get_attendee_response(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    event_id: str,
    attendee_email: str,
) -> str | None:
    """Respuesta del invitado a la invitación: accepted / declined / tentative / needsAction."""
    eid = (event_id or "").strip()
    want = (attendee_email or "").strip().lower()
    if not eid or not want:
        return None
    access, _ = get_valid_google_calendar_connection(db, company_id=company_id, user_id=seller_user_id)
    with httpx.Client(timeout=30.0) as client:
        res = client.get(_event_url(eid), headers={"Authorization": f"Bearer {access}"})
    if res.status_code in (404, 410):
        return None
    res.raise_for_status()
    for att in res.json().get("attendees") or []:
        if str(att.get("email") or "").strip().lower() == want:
            return str(att.get("responseStatus") or "") or None
    return None


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


def delete_day_events_matching_title(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    title: str,
    around: datetime,
    timezone: str = "America/Argentina/Buenos_Aires",
    keep_event_id: str | None = None,
) -> int:
    """
    Borra eventos confirmados del mismo día con el mismo título, excepto keep_event_id.
    Evita huérfanos (p. ej. 10:30 + 11) cuando el Meeting en DB se canceló/rollbackeó.
    """
    want = (title or "").strip()
    if not want:
        return 0
    keep = (keep_event_id or "").strip() or None
    access, _ = get_valid_google_calendar_connection(
        db, company_id=company_id, user_id=seller_user_id
    )
    tz_name = (timezone or "America/Argentina/Buenos_Aires").strip()
    local = to_campaign_local(around, tz_name)
    day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    params = {
        "timeMin": day0.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "timeMax": day1.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "singleEvents": "true",
        "orderBy": "startTime",
    }
    deleted = 0
    with httpx.Client(timeout=45.0) as client:
        res = client.get(
            EVENTS_INSERT_URL,
            headers={"Authorization": f"Bearer {access}"},
            params=params,
        )
        if res.status_code == 401:
            raise RuntimeError("Google Calendar rechazó el token (401) al listar eventos.")
        res.raise_for_status()
        for ev in res.json().get("items") or []:
            if not isinstance(ev, dict):
                continue
            if str(ev.get("status") or "").lower() == "cancelled":
                continue
            if (ev.get("summary") or "").strip() != want:
                continue
            eid = str(ev.get("id") or "").strip()
            if not eid or (keep and eid == keep):
                continue
            try:
                delete_calendar_event(
                    db,
                    company_id=company_id,
                    seller_user_id=seller_user_id,
                    event_id=eid,
                )
                deleted += 1
            except Exception as exc:
                logger.warning(
                    "google_calendar_orphan_delete_failed event_id=%s err=%s",
                    eid[:24],
                    str(exc)[:160],
                )
    if deleted:
        logger.info(
            "google_calendar_orphan_sweep title=%s deleted=%s keep=%s",
            want[:60],
            deleted,
            (keep or "")[:24],
        )
    return deleted


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
    attendee_name: str | None = None,
    host_email: str | None = None,
    timezone: str = "America/Argentina/Buenos_Aires",
    require_guest: bool = False,
    require_meet: bool = True,
) -> dict[str, Any]:
    """
    Crea un evento en el calendario primary del SDR.
    Devuelve {event_id, html_link, start, end, meet_link} o lanza si no hay conexión Google.

    Reglas duras de producto:
    - Meet siempre (conferenceData + patch si hace falta).
    - Si hay mail del prospecto / require_guest: debe quedar en Invitados (attendees).

    Importante: manda dateTime en hora local de `timezone` (sin Z), para que Google
    no desplace el horario al combinar UTC + timeZone.
    """
    access, gmail_row = get_valid_google_calendar_connection(
        db, company_id=company_id, user_id=seller_user_id
    )
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
        "conferenceData": _conference_create_body(),
    }
    guest = _norm_guest_email(attendee_email) or ""
    if require_guest and not guest:
        raise RuntimeError(
            "Calendar requiere el mail del prospecto en Invitados; no se recibió attendee_email."
        )
    guest_name = (attendee_name or "").strip() or None
    host_emails = _seller_guest_emails(
        db,
        seller_user_id=seller_user_id,
        gmail_row=gmail_row,
        host_email=host_email,
    )
    attendees = _attendee_bodies(
        prospect_email=guest or None,
        prospect_name=guest_name,
        host_emails=host_emails,
    )
    if guest and not any(
        str(a.get("email") or "").strip().lower() == guest for a in attendees
    ):
        # Defensa: el prospecto SIEMPRE primero en Invitados.
        attendees = [
            {
                "email": guest,
                "responseStatus": "needsAction",
                **({"displayName": guest_name[:80]} if guest_name else {}),
            }
        ] + attendees
    if attendees:
        body["attendees"] = attendees
        desc = (body.get("description") or "").strip()
        if guest:
            invite_line = f"Invitado: {guest}"
            if invite_line.lower() not in desc.lower():
                desc = f"{invite_line}\n{desc}".strip()
        body["description"] = desc[:4000] or None

    params: dict[str, Any] = {"conferenceDataVersion": 1}
    # Con invitado: siempre notificar (sendUpdates=all).
    if guest or attendees:
        params["sendUpdates"] = "all"

    headers = {"Authorization": f"Bearer {access}"}
    with httpx.Client(timeout=45.0) as client:
        res = client.post(
            EVENTS_INSERT_URL,
            headers=headers,
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

        meet_link = meet_link_from_event(data)
        if not meet_link:
            try:
                got = client.get(
                    _event_url(event_id),
                    headers=headers,
                    params={"conferenceDataVersion": 1},
                )
                if got.status_code == 200:
                    data = got.json()
                    meet_link = meet_link_from_event(data)
                    html_link = str(data.get("htmlLink") or html_link).strip()
            except Exception:
                logger.warning(
                    "google_calendar_meet_get_failed event_id=%s", event_id[:24]
                )
        if not meet_link:
            try:
                patched = client.patch(
                    _event_url(event_id),
                    headers=headers,
                    params={"conferenceDataVersion": 1, "sendUpdates": "all" if attendees else "none"},
                    json={"conferenceData": _conference_create_body()},
                )
                if patched.status_code < 300:
                    data = patched.json()
                    meet_link = meet_link_from_event(data)
                    html_link = str(data.get("htmlLink") or html_link).strip()
            except Exception:
                logger.warning(
                    "google_calendar_meet_patch_failed event_id=%s", event_id[:24]
                )
        # Releer evento fresco antes de validar invitados/Meet.
        try:
            got = client.get(
                _event_url(event_id),
                headers=headers,
                params={"conferenceDataVersion": 1},
            )
            if got.status_code == 200:
                data = got.json()
                meet_link = meet_link_from_event(data) or meet_link
                html_link = str(data.get("htmlLink") or html_link).strip()
        except Exception:
            pass

        missing = [
            a["email"]
            for a in attendees
            if a.get("email") and not _event_has_guest(data, a.get("email"))
        ]
        # Prioridad: el mail del prospecto debe estar sí o sí.
        if guest and not _event_has_guest(data, guest) and guest not in missing:
            missing.insert(0, guest)
        if missing:
            try:
                patched = client.patch(
                    _event_url(event_id),
                    headers=headers,
                    params={"sendUpdates": "all", "conferenceDataVersion": 1},
                    json={"attendees": attendees},
                )
                if patched.status_code < 300:
                    data = patched.json()
                    html_link = str(data.get("htmlLink") or html_link).strip()
                    meet_link = meet_link_from_event(data) or meet_link
                    logger.info(
                        "google_calendar_guest_patched event_id=%s guests=%s",
                        event_id[:24],
                        ",".join(missing)[:80],
                    )
            except Exception:
                logger.warning(
                    "google_calendar_guest_patch_failed event_id=%s guests=%s",
                    event_id[:24],
                    ",".join(missing)[:80],
                )

        if guest and not _event_has_guest(data, guest):
            raise RuntimeError(
                f"Calendar creó el evento sin el prospecto en Invitados ({guest})."
            )
        if require_meet and not meet_link:
            raise RuntimeError(
                "Calendar creó el evento sin link de Meet; reintentá o reconectá Calendar."
            )

    logger.info(
        "google_calendar_create event_id=%s seller_user_id=%s attendee=%s meet=%s start_local=%s tz=%s",
        event_id[:24],
        seller_user_id,
        guest[:40],
        "yes" if meet_link else "no",
        format_google_local_datetime(start_local),
        tz_name,
    )
    return {
        "event_id": event_id,
        "html_link": html_link or None,
        "meet_link": meet_link,
        "start": start_local.isoformat(),
        "end": end_local.isoformat(),
        "timezone": tz_name,
        "attendee_email": guest or None,
    }


def update_calendar_event_attendee(
    db: Session,
    *,
    company_id: int,
    seller_user_id: int,
    event_id: str,
    attendee_email: str,
) -> dict[str, Any]:
    """Reemplaza el invitado del evento (el prospecto pasó otro mail) y le manda la invitación."""
    eid = (event_id or "").strip()
    if not eid:
        raise ValueError("event_id vacío")
    access, gmail_row = get_valid_google_calendar_connection(
        db, company_id=company_id, user_id=seller_user_id
    )
    attendees = _attendee_bodies(
        prospect_email=attendee_email,
        prospect_name=None,
        host_emails=_seller_guest_emails(
            db, seller_user_id=seller_user_id, gmail_row=gmail_row
        ),
    )
    with httpx.Client(timeout=45.0) as client:
        res = client.patch(
            _event_url(eid),
            headers={"Authorization": f"Bearer {access}"},
            params={"sendUpdates": "all", "conferenceDataVersion": 1},
            json={"attendees": attendees or [{"email": attendee_email.strip()}]},
        )
        if res.status_code == 401:
            raise RuntimeError(
                "Google Calendar rechazó el token (401). Reconectá Google en Conexiones."
            )
        res.raise_for_status()
        data = res.json()
    logger.info("google_calendar_update_attendee event_id=%s seller_user_id=%s", eid[:24], seller_user_id)
    return {"event_id": eid, "html_link": str(data.get("htmlLink") or "").strip() or None}


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
