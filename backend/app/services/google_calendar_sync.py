"""
Sincronización Google Calendar ↔ prospectos Nexus.

Flujo: calendarList → events.list (primary + calendarios con reader+) → events.get → match attendee.email ↔ prospect.email.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import MeetingStatus, PipelineStage, ProspectStatus
from app.models.meeting import Meeting
from app.models.prospect import Prospect
from app.models.user import User
from app.services import multichannel_sequence as mseq
from app.services import pipeline_sync
from app.services.gmail_drafts import get_valid_google_calendar_connection
from app.services.meeting_booking import CREATION_SYNC
from app.services.outreach_simulation import make_message

logger = logging.getLogger(__name__)

CALENDAR_ID_PRIMARY = "primary"
CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
_EMAIL_IN_TEXT = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _resolve_sync_clock(
    client_now_utc: datetime | None,
) -> tuple[datetime, datetime, str, str | None]:
    """
    now_for_window: ancla para timeMin/timeMax.
    server_now: reloj del proceso (referencia debug).
    anchor: "client" | "server"
    client_iso: ISO del instante usado si anchor es client, si no None.
    """
    server_now = datetime.now(UTC)
    if client_now_utc is None:
        return server_now, server_now, "server", None
    cn = client_now_utc
    if cn.tzinfo is None:
        cn = cn.replace(tzinfo=UTC)
    else:
        cn = cn.astimezone(UTC)
    return cn, server_now, "client", cn.isoformat()


def _events_list_url(calendar_id: str) -> str:
    enc = urllib.parse.quote((calendar_id or "primary").strip() or "primary", safe="")
    return f"https://www.googleapis.com/calendar/v3/calendars/{enc}/events"


def normalize_email_for_match(raw: str | None) -> str:
    em = (raw or "").strip().lower()
    if not em or "@" not in em:
        return em
    local, _, domain = em.partition("@")
    domain = domain.strip()
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
    return f"{local}@{domain}"


def _parse_event_start(ev: dict) -> datetime | None:
    st = ev.get("start") or {}
    raw = st.get("dateTime") or st.get("date")
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if "T" not in s:
        s = f"{s}T12:00:00+00:00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_event_end(ev: dict) -> datetime | None:
    st = ev.get("end") or {}
    raw = st.get("dateTime") or st.get("date")
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if "T" not in s:
        s = f"{s}T23:59:59+00:00"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _duration_minutes(ev: dict) -> int:
    a = _parse_event_start(ev)
    b = _parse_event_end(ev)
    if a is None or b is None:
        return 30
    d = int((b - a).total_seconds() // 60)
    return max(15, min(d, 240)) if d > 0 else 30


def _fetch_events_with_debug(
    access: str,
    *,
    calendar_id: str,
    time_min: datetime,
    time_max: datetime,
    capture_raw_json: bool = True,
) -> tuple[list[dict], dict[str, Any]]:
    """GET events.list paginado para un calendarId concreto."""
    list_url = _events_list_url(calendar_id)
    t0 = time_min.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = time_max.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base_params: dict[str, str] = {
        "timeMin": t0,
        "timeMax": t1,
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": "250",
        "maxAttendees": "50",
    }
    items: list[dict] = []
    page_token: str | None = None
    pages_debug: list[dict[str, Any]] = []
    page_idx = 0
    with httpx.Client(timeout=60.0) as client:
        while True:
            page_idx += 1
            params = dict(base_params)
            if page_token:
                params["pageToken"] = page_token
            res = client.get(
                list_url,
                headers={"Authorization": f"Bearer {access}"},
                params=params,
            )
            page_info: dict[str, Any] = {
                "page": page_idx,
                "status_code": res.status_code,
                "request_method": "GET",
                "request_url": str(res.request.url),
                "params_sent": dict(sorted(params.items())),
                "calendar_id_queried": calendar_id,
            }
            if res.status_code == 401:
                page_info["body_preview"] = (res.text or "")[:4000]
                pages_debug.append(page_info)
                raise RuntimeError(
                    "Google Calendar respondió 401. Reconectá Google en Conexiones (Calendar).",
                )
            try:
                data = res.json()
            except Exception:
                page_info["json_parse_error"] = True
                page_info["body_preview"] = (res.text or "")[:8000]
                pages_debug.append(page_info)
                res.raise_for_status()
                raise

            if not isinstance(data, dict):
                page_info["unexpected_json_type"] = type(data).__name__
                page_info["body_preview"] = (res.text or "")[:8000]
                pages_debug.append(page_info)
                res.raise_for_status()
                msg = "Google Calendar events.list devolvió JSON inesperado"
                raise RuntimeError(msg)

            raw_json = json.dumps(data, ensure_ascii=False)
            page_info["response_top_level_keys"] = list(data.keys())
            page_info["items_count_in_page"] = len(data.get("items") or [])
            npt = (data.get("nextPageToken") or "").strip()
            page_info["next_page_token_present"] = bool(npt)
            page_info["calendar_summary_field"] = data.get("summary")
            page_info["calendar_time_zone_field"] = data.get("timeZone")
            page_info["access_role_field"] = data.get("accessRole")
            page_info["updated_field"] = data.get("updated")
            if capture_raw_json:
                limit = 48_000
                if len(raw_json) > limit:
                    page_info["raw_response_json"] = raw_json[:limit] + "\n…[truncado por tamaño]…"
                else:
                    page_info["raw_response_json"] = raw_json
            pages_debug.append(page_info)

            res.raise_for_status()

            items.extend(list(data.get("items") or []))
            page_token = npt or None
            if not page_token:
                break

    debug: dict[str, Any] = {
        "calendar_id": calendar_id,
        "calendar_id_note": (
            "Literal 'primary' = calendario principal del dueño del token; "
            "otros valores = id de calendarList (p. ej. email o grupo)."
        ),
        "list_endpoint": list_url,
        "server_now_utc_iso": datetime.now(UTC).isoformat(),
        "time_min_utc_rfc3339": t0,
        "time_max_utc_rfc3339": t1,
        "timezone_for_query": "UTC (sufijo Z en timeMin/timeMax; no se envía parámetro timeZone a events.list)",
        "base_query_params": dict(sorted(base_params.items())),
        "total_pages": len(pages_debug),
        "total_items_collected": len(items),
        "pages": pages_debug,
    }
    return items, debug


def _fetch_calendar_list_all(
    access: str, *, capture_raw_json: bool = True
) -> tuple[list[dict], dict[str, Any]]:
    """GET users/me/calendarList con paginación; todos los calendarios visibles con el token."""
    items_all: list[dict] = []
    list_pages: list[dict[str, Any]] = []
    page_token: str | None = None
    with httpx.Client(timeout=90.0) as client:
        while True:
            params: dict[str, str] = {"maxResults": "250"}
            if page_token:
                params["pageToken"] = page_token
            res = client.get(
                CALENDAR_LIST_URL,
                headers={"Authorization": f"Bearer {access}"},
                params=params,
            )
            page_info: dict[str, Any] = {
                "status_code": res.status_code,
                "request_method": "GET",
                "request_url": str(res.request.url),
                "params_sent": dict(sorted(params.items())),
            }
            if res.status_code == 401:
                page_info["body_preview"] = (res.text or "")[:4000]
                list_pages.append(page_info)
                raise RuntimeError(
                    "Google Calendar calendarList respondió 401. Reconectá Google en Conexiones.",
                )
            if res.status_code != 200:
                page_info["body_preview"] = (res.text or "")[:8000]
                list_pages.append(page_info)
                return [], {
                    "error": f"calendarList HTTP {res.status_code}",
                    "list_pages": list_pages,
                    "calendars_all": [],
                    "total_calendars": 0,
                }
            data = res.json()
            if not isinstance(data, dict):
                page_info["unexpected_json_type"] = type(data).__name__
                page_info["body_preview"] = (res.text or "")[:4000]
                list_pages.append(page_info)
                return [], {
                    "error": "calendarList JSON inesperado",
                    "list_pages": list_pages,
                    "calendars_all": [],
                    "total_calendars": 0,
                }

            batch = list(data.get("items") or [])
            items_all.extend(batch)
            npt = (data.get("nextPageToken") or "").strip() or None
            page_info["items_in_page"] = len(batch)
            page_info["next_page_token_present"] = bool(npt)
            raw_json = json.dumps(data, ensure_ascii=False)
            if capture_raw_json:
                lim = 28_000
                if len(raw_json) > lim:
                    page_info["raw_response_json"] = raw_json[:lim] + "\n…[truncado]…"
                else:
                    page_info["raw_response_json"] = raw_json
            list_pages.append(page_info)
            page_token = npt or None
            if not page_token:
                break

    calendars_all: list[dict[str, Any]] = [
        {
            "id": it.get("id"),
            "summary": it.get("summary"),
            "primary": it.get("primary"),
            "accessRole": it.get("accessRole"),
            "timeZone": it.get("timeZone"),
        }
        for it in items_all
    ]
    return items_all, {
        "url": CALENDAR_LIST_URL,
        "total_calendars": len(items_all),
        "list_pages_count": len(list_pages),
        "list_pages": list_pages,
        "calendars_all": calendars_all,
    }


def _calendar_ids_for_events_list(cal_items: list[dict]) -> list[str]:
    """IDs para events.list: siempre `primary` primero, luego entradas con rol de lectura de eventos."""
    ordered: list[str] = []
    seen: set[str] = set()

    def add(cid: str) -> None:
        c = (cid or "").strip()
        if not c or c in seen:
            return
        seen.add(c)
        ordered.append(c)

    add(CALENDAR_ID_PRIMARY)
    skip_roles = {"freebusyreader"}
    for it in cal_items:
        role = (it.get("accessRole") or "").strip().lower()
        if role in skip_roles:
            continue
        add((it.get("id") or "").strip())
    return ordered


def _fetch_events_merged_from_accessible_calendars(
    access: str,
    *,
    time_min: datetime,
    time_max: datetime,
    include_debug: bool,
) -> tuple[list[dict], dict[str, Any], dict[str, Any]]:
    """
    calendarList → events.list por cada calendario con acceso ≠ freeBusyReader.
    Une eventos por id (dedup). Cada ítem lleva _nexus_calendar_id para events.get.
    """
    cal_items, cal_bundle = _fetch_calendar_list_all(
        access,
        capture_raw_json=include_debug,
    )
    ids = _calendar_ids_for_events_list(cal_items)
    if cal_bundle.get("error") and not cal_items:
        ids = [CALENDAR_ID_PRIMARY]

    merged: list[dict] = []
    seen_eid: set[str] = set()
    per_calendar: list[dict[str, Any]] = []
    primary_debug: dict[str, Any] | None = None

    max_calendars_with_full_raw = 5 if include_debug else 0
    for idx, cid in enumerate(ids):
        capture_raw = include_debug and idx < max_calendars_with_full_raw
        items, edbg = _fetch_events_with_debug(
            access,
            calendar_id=cid,
            time_min=time_min,
            time_max=time_max,
            capture_raw_json=capture_raw,
        )
        if cid == CALENDAR_ID_PRIMARY:
            primary_debug = edbg

        meta = next(
            (c for c in cal_bundle.get("calendars_all", []) if c.get("id") == cid),
            None,
        )
        per_calendar.append(
            {
                "calendar_id": cid,
                "summary": (meta or {}).get("summary"),
                "primary": (meta or {}).get("primary"),
                "accessRole": (meta or {}).get("accessRole"),
                "timeZone": (meta or {}).get("timeZone"),
                "items_fetched_in_window": len(items),
                "list_pages_count": edbg.get("total_pages"),
                "events_list_detail_included_full_raw": capture_raw,
                "events_list_debug": edbg if include_debug else None,
            }
        )

        for ev in items:
            eid = (ev.get("id") or "").strip()
            if not eid or eid in seen_eid:
                continue
            seen_eid.add(eid)
            ev2 = dict(ev)
            ev2["_nexus_calendar_id"] = cid
            merged.append(ev2)

    t0 = time_min.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = time_max.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    pdbg = dict(primary_debug or {})
    events_list_debug: dict[str, Any] = {
        **pdbg,
        "timezone_note": (
            "timeMin/timeMax se envían en RFC3339 con sufijo Z (UTC absoluto). "
            "Google filtra por ese rango; el campo timeZone de cada calendario describe cómo "
            "se muestran los eventos, no cambia el filtro UTC de la query."
        ),
        "sync_mode": "events.list en primary + todos los calendarios calendarList con acceso reader+ (sin freeBusyReader)",
        "calendar_list_total": cal_bundle.get("total_calendars"),
        "calendars_all": cal_bundle.get("calendars_all", []),
        "calendar_list_fetch_pages": cal_bundle.get("list_pages", []),
        "calendar_list_error": cal_bundle.get("error"),
        "calendars_queried_for_events": ids,
        "per_calendar_events": per_calendar,
        "merged_unique_events_count": len(merged),
        "time_window_repeated_utc": {"timeMin": t0, "timeMax": t1},
    }

    return merged, events_list_debug, cal_bundle


def fetch_calendar_event(
    access: str,
    event_id: str,
    *,
    calendar_id: str = CALENDAR_ID_PRIMARY,
) -> tuple[dict | None, int | None]:
    """GET events.get → (evento, http_status)."""
    eid = urllib.parse.quote(event_id, safe="")
    base = _events_list_url(calendar_id)
    url = f"{base}/{eid}"
    with httpx.Client(timeout=30.0) as client:
        res = client.get(url, headers={"Authorization": f"Bearer {access}"})
        status = res.status_code
        if status != 200:
            return None, status
        data = res.json()
    if isinstance(data, dict) and data.get("id"):
        return data, status
    return None, status


def _organizer_raw_and_norm(ev: dict) -> tuple[str, str]:
    org = ev.get("organizer") or {}
    raw = (org.get("email") or "").strip()
    return raw, normalize_email_for_match(raw)


def _attendee_rows(ev: dict) -> list[dict[str, Any]]:
    """Cada asistente: raw, normalized, flags."""
    rows: list[dict[str, Any]] = []
    for a in ev.get("attendees") or []:
        raw = (a.get("email") or "").strip()
        norm = normalize_email_for_match(raw)
        if not norm:
            continue
        if "resource.calendar.google.com" in norm:
            continue
        if (a.get("resource") is True) or (a.get("type") or "").lower() == "resource":
            continue
        rows.append(
            {
                "email_raw": raw or None,
                "email_normalized": norm,
                "response_status": (a.get("responseStatus") or "").strip() or None,
                "self": bool(a.get("self")),
            }
        )
    return rows


def _map_meeting_status(ev: dict, *, seller_emails: set[str] | None = None) -> str:
    st = (ev.get("status") or "").lower()
    if st == "cancelled":
        return MeetingStatus.canceled.value

    sellers = seller_emails or set()
    seller_responses: list[str] = []
    guest_responses: list[str] = []
    for attendee in ev.get("attendees") or []:
        if not isinstance(attendee, dict):
            continue
        if bool(attendee.get("resource")):
            continue
        email = normalize_email_for_match(str(attendee.get("email") or ""))
        if not email:
            continue
        response = (attendee.get("responseStatus") or "").strip().lower()
        if not response:
            continue
        if email in sellers:
            seller_responses.append(response)
        else:
            guest_responses.append(response)

    if seller_responses and any(r == "declined" for r in seller_responses):
        return MeetingStatus.canceled.value

    if guest_responses:
        if any(r == "declined" for r in guest_responses):
            return MeetingStatus.canceled.value
        if any(r in ("accepted", "tentative") for r in guest_responses):
            return MeetingStatus.confirmed.value
        return MeetingStatus.pending.value

    if seller_responses and any(r in ("accepted", "tentative") for r in seller_responses):
        return MeetingStatus.confirmed.value

    return MeetingStatus.pending.value


def _is_future_booking(start: datetime, *, now: datetime, slack_minutes: int = 5) -> bool:
    return start >= (now - timedelta(minutes=slack_minutes))


def _emails_in_description(ev: dict) -> list[str]:
    desc = (ev.get("description") or "") + " " + (ev.get("location") or "")
    found = _EMAIL_IN_TEXT.findall(desc)
    return [normalize_email_for_match(x) for x in found if "@" in (x or "")]


def _build_match_trace(
    ev: dict,
    *,
    email_map: dict[str, tuple[Prospect, Campaign]],
    seller_emails: set[str],
) -> dict[str, Any]:
    """Traza completa: attendees vs prospect.email (solo diagnóstico)."""
    org_raw, org_norm = _organizer_raw_and_norm(ev)
    att_rows = _attendee_rows(ev)
    all_norm = [r["email_normalized"] for r in att_rows]
    guest_norm = [e for e in all_norm if e not in seller_emails]

    comparisons: list[dict[str, Any]] = []
    match_hit: tuple[Prospect, Campaign, str] | None = None

    for r in att_rows:
        norm = r["email_normalized"]
        is_seller = norm in seller_emails
        pid: int | None = None
        matched = False
        if not is_seller:
            hit = email_map.get(norm)
            if hit is not None:
                matched = True
                pid = hit[0].id
                if match_hit is None:
                    match_hit = (*hit, "attendee")
        comparisons.append(
            {
                "email_raw": r.get("email_raw"),
                "email_normalized": norm,
                "is_seller": is_seller,
                "matched_prospect_id": pid,
                "match": matched,
                "response_status": r.get("response_status"),
            }
        )

    organizer_in_index = bool(org_norm and org_norm not in seller_emails and org_norm in email_map)
    if match_hit is None and organizer_in_index and org_norm:
        match_hit = (*email_map[org_norm], "organizer")

    if match_hit is None:
        for em in _emails_in_description(ev):
            if em in seller_emails:
                continue
            hit = email_map.get(em)
            if hit is not None:
                match_hit = (*hit, "description")
                break

    return {
        "organizer_raw": org_raw or None,
        "organizer_normalized": org_norm or None,
        "all_attendees_normalized": all_norm,
        "guest_attendees_normalized": guest_norm,
        "attendee_comparisons": comparisons,
        "organizer_in_index": organizer_in_index,
        "emails_in_description": _emails_in_description(ev)[:6],
        "match_hit": match_hit,
    }


def _timeline_calendar_message(
    *,
    ev: dict,
    event_id: str,
    start: datetime,
    organizer: str,
    attendees: list[str],
    html_link: str | None,
    matched_email: str,
    match_via: str,
) -> str:
    title = (ev.get("summary") or "Reunión").strip()[:220]
    lines = [
        "[Google Calendar · reunión real detectada]",
        "Reunión detectada desde Google Calendar",
        f"Evento ID: {event_id}",
        f"Match: {match_via} · email: {matched_email}",
        f"Inicio (UTC): {start.strftime('%Y-%m-%d %H:%M')}",
        f"Título: {title}",
        f"Organizador: {organizer or '—'}",
        f"Asistentes: {', '.join(attendees[:16]) if attendees else '—'}",
        "Proveedor: Google Calendar",
    ]
    if html_link:
        lines.append(f"Abrir: {html_link}")
    return "\n".join(lines)


def _log_trace(trace: dict[str, Any]) -> None:
    logger.info(
        "[calendar_sync] event_id=%s calendar_id=%s matched=%s via=%s "
        "prospect_id=%s organizer=%s attendees=%s future=%s cancelled=%s "
        "meeting_created=%s meeting_updated=%s pipeline=%s skip=%s",
        trace.get("event_id"),
        trace.get("source_calendar_id"),
        trace.get("matched"),
        trace.get("match_via"),
        trace.get("matched_prospect_id"),
        trace.get("organizer_normalized"),
        trace.get("guest_attendees_normalized"),
        trace.get("is_future"),
        trace.get("is_cancelled"),
        trace.get("meeting_created"),
        trace.get("meeting_updated"),
        trace.get("pipeline_updated"),
        trace.get("pipeline_skip_reason") or trace.get("skip_reason"),
    )


def sync_calendar_events_for_seller(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    campaign_id: int | None = None,
    days_back: int = 90,
    days_forward: int = 180,
    include_debug: bool = True,
    debug_max_events: int = 50,
    client_now_utc: datetime | None = None,
) -> dict[str, Any]:
    access, gmail_row = get_valid_google_calendar_connection(db, company_id=company_id, user_id=user_id)
    seller_user = db.get(User, user_id)
    seller_emails: set[str] = set()
    for raw in (gmail_row.external_email, getattr(seller_user, "email", None)):
        em = normalize_email_for_match(raw)
        if em:
            seller_emails.add(em)

    calendar_account = (gmail_row.external_email or getattr(seller_user, "email", None) or "").strip()
    now, server_now, time_window_anchor, client_now_utc_received = _resolve_sync_clock(client_now_utc)
    t_min = now - timedelta(days=max(1, min(days_back, 365)))
    t_max = now + timedelta(days=max(1, min(days_forward, 730)))

    q = (
        select(Campaign)
        .where(Campaign.company_id == company_id, Campaign.seller_id == user_id)
        .options(selectinload(Campaign.product))
    )
    if campaign_id is not None:
        q = q.where(Campaign.id == campaign_id)
    campaigns = list(db.scalars(q).all())
    if not campaigns:
        return {
            "events_seen": 0,
            "events_enriched": 0,
            "prospects_with_email": 0,
            "matched": 0,
            "created": 0,
            "updated": 0,
            "pipeline_updated": 0,
            "timelines_logged": 0,
            "calendar_account": calendar_account or None,
            "seller_emails": sorted(seller_emails),
            "time_window_start_utc": t_min.isoformat(),
            "time_window_end_utc": t_max.isoformat(),
            "errors": ["Sin campañas para este vendedor."],
            "prospect_email_index": [],
            "debug": [],
            "events_list_debug": None,
            "calendar_list_debug": None,
            "time_window_anchor": time_window_anchor,
            "server_now_utc_used": server_now.isoformat(),
            "client_now_utc_received": client_now_utc_received,
        }

    camp_ids = [c.id for c in campaigns]
    prospects = db.scalars(
        select(Prospect).where(
            Prospect.company_id == company_id,
            Prospect.campaign_id.in_(camp_ids),
        )
    ).all()

    email_map: dict[str, tuple[Prospect, Campaign]] = {}
    email_collisions: list[dict[str, Any]] = []
    prospect_index: list[dict[str, Any]] = []
    for p in prospects:
        raw_em = (p.email or "").strip()
        em = normalize_email_for_match(raw_em)
        if not em:
            continue
        camp = next((c for c in campaigns if c.id == p.campaign_id), None)
        if camp is None:
            continue
        if em in email_map:
            prev_p, prev_c = email_map[em]
            email_collisions.append(
                {
                    "email_normalized": em,
                    "kept_prospect_id": prev_p.id,
                    "kept_campaign_id": prev_c.id,
                    "dropped_prospect_id": p.id,
                    "dropped_campaign_id": camp.id,
                }
            )
            logger.warning(
                "[calendar_sync] email_collision normalized=%s kept_prospect=%s dropped_prospect=%s",
                em,
                prev_p.id,
                p.id,
            )
        else:
            email_map[em] = (p, camp)
        prospect_index.append(
            {
                "prospect_id": p.id,
                "name": p.name,
                "email_raw": raw_em,
                "email_normalized": em,
                "campaign_id": p.campaign_id,
            }
        )

    events_list, events_list_debug, calendar_list_debug = _fetch_events_merged_from_accessible_calendars(
        access,
        time_min=t_min,
        time_max=t_max,
        include_debug=include_debug,
    )
    if include_debug and isinstance(events_list_debug, dict):
        events_list_debug["time_window_anchor"] = time_window_anchor
        events_list_debug["server_now_utc"] = server_now.isoformat()
        events_list_debug["client_now_utc_used_for_window"] = client_now_utc_received
        events_list_debug["window_now_utc"] = now.isoformat()
    created = updated = matched = timelines_logged = pipeline_updated = events_enriched = 0
    reconciled_groups = 0
    errors: list[str] = []
    all_traces: list[dict[str, Any]] = []

    logger.info(
        "[calendar_sync] tick started company_id=%s user_id=%s campaigns=%s prospects_indexed=%s "
        "window=%s..%s seller_emails=%s collisions=%s",
        company_id,
        user_id,
        camp_ids,
        len(email_map),
        t_min.isoformat(),
        t_max.isoformat(),
        sorted(seller_emails),
        len(email_collisions),
    )

    for ev_raw in events_list:
        eid = (ev_raw.get("id") or "").strip()
        if not eid:
            continue

        cal_src = (ev_raw.get("_nexus_calendar_id") or CALENDAR_ID_PRIMARY).strip() or CALENDAR_ID_PRIMARY

        ev = {k: v for k, v in ev_raw.items() if k != "_nexus_calendar_id"}
        list_att_count = len(ev.get("attendees") or [])
        ev_full, get_status = fetch_calendar_event(access, eid, calendar_id=cal_src)
        get_ok = isinstance(ev_full, dict) and bool(ev_full.get("id"))
        if get_ok:
            ev = ev_full
            events_enriched += 1

        start = _parse_event_start(ev)
        trace: dict[str, Any] = {
            "event_id": eid,
            "source_calendar_id": cal_src,
            "summary": (ev.get("summary") or "")[:120] or None,
            "start_utc": start.isoformat() if start else None,
            "google_status": (ev.get("status") or "").strip() or None,
            "list_attendees_count": list_att_count,
            "get_http_status": get_status,
            "get_ok": get_ok,
            "matched": False,
            "meeting_created": False,
            "meeting_updated": False,
            "pipeline_updated": False,
        }

        if start is None:
            trace["skip_reason"] = "sin_fecha_inicio"
            all_traces.append(trace)
            continue

        mt = _build_match_trace(ev, email_map=email_map, seller_emails=seller_emails)
        trace.update(
            {
                "organizer_raw": mt["organizer_raw"],
                "organizer_normalized": mt["organizer_normalized"],
                "all_attendees_normalized": mt["all_attendees_normalized"],
                "guest_attendees_normalized": mt["guest_attendees_normalized"],
                "attendee_comparisons": mt["attendee_comparisons"],
                "organizer_in_index": mt["organizer_in_index"],
                "emails_in_description": mt.get("emails_in_description"),
            }
        )

        match_hit = mt["match_hit"]
        if match_hit is None:
            if not get_ok and not mt["guest_attendees_normalized"]:
                trace["skip_reason"] = "events_get_fallo_y_sin_invitados"
            elif not mt["guest_attendees_normalized"] and not mt["organizer_in_index"]:
                trace["skip_reason"] = "sin_invitados_externos"
                if mt.get("emails_in_description"):
                    trace["skip_reason"] = "sin_match; emails_en_descripcion_no_usados"
            else:
                trace["skip_reason"] = "sin_match_attendee_vs_prospect_index"
            all_traces.append(trace)
            if include_debug:
                _log_trace(trace)
            continue

        prospect, campaign, match_via = match_hit
        matched_email = normalize_email_for_match(prospect.email)
        matched += 1
        trace["matched"] = True
        trace["match_via"] = match_via
        trace["matched_prospect_id"] = prospect.id
        trace["matched_email_normalized"] = matched_email

        gstatus = _map_meeting_status(ev, seller_emails=seller_emails)
        is_cancelled = gstatus == MeetingStatus.canceled.value
        title = (ev.get("summary") or "Reunión").strip()[:255] or "Reunión"
        desc = (ev.get("description") or "").strip() or None
        html_link = (ev.get("htmlLink") or "").strip() or None
        dur = _duration_minutes(ev)
        tz = (campaign.timezone or "UTC").strip() or "UTC"
        future_ok = _is_future_booking(start, now=now)
        org_norm = mt["organizer_normalized"] or ""
        guest_list = mt["guest_attendees_normalized"]
        trace["is_future"] = future_ok
        trace["is_cancelled"] = is_cancelled
        trace["matched_campaign_id"] = campaign.id
        trace["meeting_status_mapped"] = gstatus

        try:
            existing = db.scalars(
                select(Meeting).where(
                    Meeting.company_id == company_id,
                    Meeting.google_calendar_event_id == eid,
                )
            ).first()
            is_new_meeting_row = existing is None

            if existing:
                existing.campaign_id = campaign.id
                existing.prospect_id = prospect.id
                existing.title = title
                existing.description = desc
                existing.scheduled_for = start
                existing.meeting_status = gstatus
                existing.timezone = tz[:128]
                existing.duration_minutes = dur
                existing.google_calendar_html_link = html_link
                updated += 1
                trace["meeting_updated"] = True
            else:
                db.add(
                    Meeting(
                        company_id=company_id,
                        campaign_id=campaign.id,
                        prospect_id=prospect.id,
                        title=title,
                        description=desc,
                        scheduled_for=start,
                        meeting_status=gstatus,
                        timezone=tz[:128],
                        suggested_slots=None,
                        duration_minutes=dur,
                        google_calendar_event_id=eid,
                        google_calendar_html_link=html_link,
                        creation_method=CREATION_SYNC,
                        created_by_user_id=seller_user.id if seller_user else None,
                    )
                )
                created += 1
                trace["meeting_created"] = True
                logger.info(
                    "[calendar_sync] meeting created event_id=%s prospect_id=%s campaign_id=%s start=%s",
                    eid,
                    prospect.id,
                    campaign.id,
                    start.isoformat(),
                )

            if existing and trace.get("meeting_updated"):
                logger.info(
                    "[calendar_sync] meeting updated event_id=%s prospect_id=%s campaign_id=%s",
                    eid,
                    prospect.id,
                    campaign.id,
                )

            if gstatus == MeetingStatus.canceled.value:
                trace["pipeline_skip_reason"] = "evento_cancelado_en_google"
            elif not future_ok:
                trace["pipeline_skip_reason"] = f"evento_en_pasado ({start.isoformat()})"
            else:
                applied = mseq.enforce_meeting_priority_over_sequence(db, prospect, campaign)
                pipeline_updated += 1 if applied else 0
                trace["pipeline_updated"] = bool(applied)
                trace["sequence_group_after"] = getattr(prospect, "sequence_group", None)
                trace["sequence_paused_after"] = bool(getattr(prospect, "sequence_paused", False))
                logger.info(
                    "[calendar_sync] enforce_meeting_priority prospect_id=%s applied=%s "
                    "group=%s paused=%s",
                    prospect.id,
                    applied,
                    prospect.sequence_group,
                    prospect.sequence_paused,
                )

                if is_new_meeting_row:
                    db.add(
                        make_message(
                            prospect_id=prospect.id,
                            campaign_id=campaign.id,
                            sender_type="system",
                            message=_timeline_calendar_message(
                                ev=ev,
                                event_id=eid,
                                start=start,
                                organizer=org_norm,
                                attendees=guest_list,
                                html_link=html_link,
                                matched_email=matched_email,
                                match_via=match_via,
                            ),
                            channel="calendar",
                            direction="outbound",
                        )
                    )
                    timelines_logged += 1
                    log = getattr(campaign, "outreach_activity_log", None) or []
                    if isinstance(log, list):
                        entry = {
                            "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                            "kind": "calendar",
                            "message": (
                                f"Reunión detectada desde Google Calendar · {prospect.name} — "
                                f"{title[:80]} · match {match_via} ({matched_email})"
                            ),
                        }
                        campaign.outreach_activity_log = [*log[-199:], entry]
        except Exception as exc:  # noqa: BLE001
            trace["error"] = str(exc)[:400]
            errors.append(f"event {eid[:16]}…: {exc}")

        all_traces.append(trace)
        if include_debug:
            _log_trace(trace)

    # Priorizar en respuesta: matched, luego con invitados, luego resto
    def _sort_key(t: dict[str, Any]) -> tuple[int, str]:
        pri = 0 if t.get("matched") else (1 if t.get("guest_attendees_normalized") else 2)
        return (pri, t.get("start_utc") or "")

    for camp in campaigns:
        n = mseq.reconcile_meeting_operational_groups_for_campaign(db, camp)
        if n:
            reconciled_groups += n
            logger.info(
                "[calendar_sync] reconcile_meeting_operational_groups campaign_id=%s fixed=%s",
                camp.id,
                n,
            )

    logger.info(
        "[calendar_sync] tick finished company_id=%s user_id=%s events_seen=%s matched=%s "
        "meetings_created=%s meetings_updated=%s pipeline_updated=%s reconciled_groups=%s",
        company_id,
        user_id,
        len(events_list),
        matched,
        created,
        updated,
        pipeline_updated,
        reconciled_groups,
    )

    all_traces.sort(key=_sort_key)
    debug_out = all_traces[:debug_max_events] if include_debug else []

    return {
        "events_seen": len(events_list),
        "events_enriched": events_enriched,
        "prospects_with_email": len(email_map),
        "matched": matched,
        "created": created,
        "updated": updated,
        "pipeline_updated": pipeline_updated,
        "reconciled_operational_groups": reconciled_groups,
        "email_collisions": email_collisions[:24],
        "timelines_logged": timelines_logged,
        "calendar_account": calendar_account or None,
        "seller_emails": sorted(seller_emails),
        "time_window_start_utc": t_min.isoformat(),
        "time_window_end_utc": t_max.isoformat(),
        "errors": errors[:12],
        "prospect_email_index": prospect_index,
        "debug": debug_out,
        "events_list_debug": events_list_debug if include_debug else None,
        "calendar_list_debug": calendar_list_debug if include_debug else None,
        "time_window_anchor": time_window_anchor,
        "server_now_utc_used": server_now.isoformat(),
        "client_now_utc_received": client_now_utc_received,
    }
