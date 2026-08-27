"""Verificación en vivo de Gmail y Google Calendar (token + permisos API)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.services import google_oauth
from app.services.gmail_drafts import (
    GMAIL_PROFILE_URL,
    _get_gmail_row,
    _get_refresh_token,
    _heal_google_rows_connected,
    _refresh_access_token,
    _row_has_usable_oauth,
    get_valid_gmail_connection,
    get_valid_google_calendar_connection,
)
from app.services.google_calendar_availability import FREEBUSY_URL
from app.services.oauth_tokens import decrypt_secret

logger = logging.getLogger(__name__)

CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

STATUS_NOT_CONNECTED = "not_connected"
STATUS_FUNCTIONAL = "functional"
STATUS_RECONNECT_REQUIRED = "reconnect_required"
STATUS_SCOPE_MISSING = "scope_missing"
STATUS_ERROR = "error"


def _has_refresh(row: ConnectedAccount | None) -> bool:
    if row is None:
        return False
    return bool(row.refresh_token_encrypted)


def _row_snapshot(row: ConnectedAccount | None) -> dict[str, Any]:
    if row is None:
        return {
            "connected": False,
            "status": IntegrationStatus.not_connected.value,
            "external_email": None,
            "connected_at": None,
            "updated_at": None,
            "has_refresh_token": False,
        }
    try:
        st = IntegrationStatus(row.status)
    except ValueError:
        st = IntegrationStatus.error
    # Refresh vivo = conexión recuperable: la UI no debe tratarlo como "desconectado".
    recoverable = _row_has_usable_oauth(row)
    return {
        "connected": st == IntegrationStatus.connected or recoverable,
        "status": (
            IntegrationStatus.connected.value
            if recoverable and st != IntegrationStatus.connected
            else st.value
        ),
        "external_email": row.external_email,
        "connected_at": row.connected_at,
        "updated_at": row.updated_at,
        "has_refresh_token": _has_refresh(row),
    }


def _base_provider_payload(row: ConnectedAccount | None) -> dict[str, Any]:
    return {
        **_row_snapshot(row),
        "effective_status": STATUS_NOT_CONNECTED,
        "requires_reconnect": False,
        "api_reachable": False,
        "api_error": None,
        "http_status": None,
        "scopes_granted": list(google_oauth.DEFAULT_SCOPES),
        "verification_summary": None,
    }


def _calendar_payload(row: ConnectedAccount | None) -> dict[str, Any]:
    return {
        **_base_provider_payload(row),
        "can_read_availability": False,
        "can_create_events": False,
        "create_event_verified": False,
    }


def _resolve_effective_status(
    *,
    stored_connected: bool,
    api_reachable: bool,
    requires_reconnect: bool,
    can_create_events: bool,
    http_status: int | None,
    deep: bool = True,
) -> str:
    if not stored_connected:
        return STATUS_NOT_CONNECTED
    if requires_reconnect or http_status == 401:
        return STATUS_RECONNECT_REQUIRED
    if http_status == 403:
        return STATUS_SCOPE_MISSING
    if api_reachable and (can_create_events or not deep):
        return STATUS_FUNCTIONAL
    if api_reachable:
        return STATUS_ERROR
    return STATUS_RECONNECT_REQUIRED


def _mark_provider_token_invalid(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    provider: IntegrationProvider,
) -> None:
    """
    Solo marcar error cuando el refresh está realmente muerto (invalid_grant).
    Si todavía hay refresh, NO desconectar: la automatización puede recuperarse.
    """
    row = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == provider.value,
        )
    ).first()
    if row is None:
        return
    if _has_refresh(row):
        logger.warning(
            "Google %s HTTP invalid pero hay refresh_token — no se marca error "
            "(company_id=%s user_id=%s)",
            provider.value,
            company_id,
            user_id,
        )
        return
    if row.status == IntegrationStatus.connected.value:
        row.status = IntegrationStatus.error.value


def _is_hard_revoke_error(err: str | None) -> bool:
    if not err:
        return False
    low = err.lower()
    return "invalid_grant" in low or "revoc" in low


def _get_access_with_refresh(
    db: Session,
    *,
    company_id: int,
    user_id: int,
) -> tuple[str | None, str | None]:
    """Obtiene access token; intenta refresh si hace falta."""
    for getter in (
        lambda: get_valid_gmail_connection(db, company_id=company_id, user_id=user_id),
        lambda: get_valid_google_calendar_connection(db, company_id=company_id, user_id=user_id),
    ):
        try:
            access, _row = getter()
            return access, None
        except Exception:
            continue
    refresh = _get_refresh_token(db, company_id, user_id)
    if refresh:
        try:
            access = _refresh_access_token(db, company_id, user_id, refresh)
            return access, None
        except Exception as refresh_exc:
            return None, str(refresh_exc)[:300]
    return None, "No se pudo obtener un token válido de Google."


def _verify_freebusy(client: httpx.Client, access: str) -> tuple[bool, int | None, str | None]:
    now = datetime.now(UTC)
    body = {
        "timeMin": now.isoformat(),
        "timeMax": (now + timedelta(hours=2)).isoformat(),
        "items": [{"id": "primary"}],
    }
    res = client.post(
        FREEBUSY_URL,
        headers={"Authorization": f"Bearer {access}"},
        json=body,
    )
    if res.status_code == 200:
        return True, res.status_code, None
    if res.status_code == 401:
        return False, res.status_code, "Token vencido o revocado (HTTP 401). Reconectá Google Calendar."
    if res.status_code == 403:
        return False, res.status_code, "Sin permiso para leer disponibilidad (calendar.freebusy)."
    return False, res.status_code, f"FreeBusy API HTTP {res.status_code}"


def _verify_create_event(client: httpx.Client, access: str) -> tuple[bool, int | None, str | None]:
    start = datetime.now(UTC) + timedelta(days=7)
    end = start + timedelta(minutes=5)
    body = {
        "summary": "[Nexus] Verificación de permisos",
        "description": "Evento temporal de verificación — se elimina automáticamente.",
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
    }
    res = client.post(
        CALENDAR_EVENTS_URL,
        headers={"Authorization": f"Bearer {access}"},
        params={"sendUpdates": "none"},
        json=body,
    )
    if res.status_code in (401, 403):
        if res.status_code == 401:
            return False, res.status_code, "Token vencido o revocado (HTTP 401). Reconectá Google Calendar."
        return False, res.status_code, "Sin permiso para crear eventos (calendar.events)."
    if res.status_code not in (200, 201):
        return False, res.status_code, f"Calendar events API HTTP {res.status_code}"
    event_id = (res.json() or {}).get("id")
    if event_id:
        try:
            client.delete(
                f"{CALENDAR_EVENTS_URL}/{event_id}",
                headers={"Authorization": f"Bearer {access}"},
            )
        except Exception:
            logger.warning("verify_create_event cleanup failed event_id=%s", event_id)
    return True, res.status_code, None


def verify_google_integrations(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    deep: bool = True,
) -> dict[str, Any]:
    gmail_row = _get_gmail_row(db, company_id, user_id)
    cal_row = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.google_calendar.value,
        )
    ).first()

    oauth_configured = google_oauth.oauth_is_configured()

    gmail = _base_provider_payload(gmail_row)
    calendar = _calendar_payload(cal_row)

    if not gmail["connected"] and not calendar["connected"]:
        return {
            "oauth_configured": oauth_configured,
            "gmail": gmail,
            "google_calendar": calendar,
        }

    access, token_err = _get_access_with_refresh(db, company_id=company_id, user_id=user_id)
    if not access:
        err = token_err or "No se pudo obtener un token válido de Google."
        hard_revoke = _is_hard_revoke_error(err)
        has_refresh = bool(_get_refresh_token(db, company_id, user_id))
        # Solo "reconnect" si Google revocó el refresh de verdad.
        needs_reconnect = hard_revoke or not has_refresh
        if gmail["connected"]:
            gmail["api_error"] = err
            gmail["requires_reconnect"] = needs_reconnect
            gmail["effective_status"] = (
                STATUS_RECONNECT_REQUIRED if needs_reconnect else STATUS_ERROR
            )
            if hard_revoke and not has_refresh:
                _mark_provider_token_invalid(
                    db, company_id=company_id, user_id=user_id, provider=IntegrationProvider.gmail
                )
        if calendar["connected"]:
            calendar["api_error"] = err
            calendar["requires_reconnect"] = needs_reconnect
            calendar["effective_status"] = (
                STATUS_RECONNECT_REQUIRED if needs_reconnect else STATUS_ERROR
            )
            if hard_revoke and not has_refresh:
                _mark_provider_token_invalid(
                    db,
                    company_id=company_id,
                    user_id=user_id,
                    provider=IntegrationProvider.google_calendar,
                )
        if hard_revoke and not has_refresh:
            db.commit()
        return {
            "oauth_configured": oauth_configured,
            "gmail": gmail,
            "google_calendar": calendar,
        }

    # Access OK → rehabilitar filas que hayan quedado en error por un 401 viejo.
    _heal_google_rows_connected(db, company_id, user_id)
    db.commit()
    # Refrescar snapshot post-heal
    gmail_row = _get_gmail_row(db, company_id, user_id)
    cal_row = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.google_calendar.value,
        )
    ).first()
    gmail = {**gmail, **_row_snapshot(gmail_row)}
    calendar = {**calendar, **_row_snapshot(cal_row)}

    with httpx.Client(timeout=25.0) as client:
        if gmail["connected"]:
            try:
                res = client.get(
                    GMAIL_PROFILE_URL,
                    headers={"Authorization": f"Bearer {access}"},
                )
                gmail["http_status"] = res.status_code
                if res.status_code == 200:
                    gmail["api_reachable"] = True
                    profile_email = (res.json() or {}).get("emailAddress")
                    if profile_email:
                        gmail["external_email"] = str(profile_email)
                    gmail["verification_summary"] = "Gmail accesible."
                elif res.status_code == 401:
                    # No marcar error si hay refresh: el próximo ciclo lo recupera.
                    gmail["api_error"] = "Access token vencido (Gmail HTTP 401); se reintentará con refresh."
                    gmail["requires_reconnect"] = not bool(_get_refresh_token(db, company_id, user_id))
                    if gmail["requires_reconnect"]:
                        _mark_provider_token_invalid(
                            db,
                            company_id=company_id,
                            user_id=user_id,
                            provider=IntegrationProvider.gmail,
                        )
                else:
                    gmail["api_error"] = f"Gmail API HTTP {res.status_code}"
            except Exception as exc:
                gmail["api_error"] = str(exc)[:300]

            gmail["effective_status"] = _resolve_effective_status(
                stored_connected=True,
                api_reachable=gmail["api_reachable"],
                requires_reconnect=gmail["requires_reconnect"],
                can_create_events=gmail["api_reachable"],
                http_status=gmail["http_status"],
            )

        if calendar["connected"]:
            try:
                res = client.get(
                    CALENDAR_LIST_URL,
                    headers={"Authorization": f"Bearer {access}"},
                    params={"maxResults": 1},
                )
                calendar["http_status"] = res.status_code
                if res.status_code == 200:
                    calendar["api_reachable"] = True
                elif res.status_code == 401:
                    calendar["api_error"] = (
                        "Access token vencido (Calendar HTTP 401); se reintentará con refresh."
                    )
                    calendar["requires_reconnect"] = not bool(
                        _get_refresh_token(db, company_id, user_id)
                    )
                    if calendar["requires_reconnect"]:
                        _mark_provider_token_invalid(
                            db,
                            company_id=company_id,
                            user_id=user_id,
                            provider=IntegrationProvider.google_calendar,
                        )
                elif res.status_code == 403:
                    calendar["api_error"] = "Sin permiso calendar. Reconectá con los scopes correctos."
                else:
                    calendar["api_error"] = f"Calendar API HTTP {res.status_code}"
            except Exception as exc:
                calendar["api_error"] = str(exc)[:300]

            if calendar["api_reachable"] and deep and not calendar["requires_reconnect"]:
                can_busy, busy_status, busy_err = _verify_freebusy(client, access)
                calendar["can_read_availability"] = can_busy
                if busy_err and not calendar["api_error"]:
                    calendar["api_error"] = busy_err
                if busy_status == 401 and not _get_refresh_token(db, company_id, user_id):
                    calendar["requires_reconnect"] = True

                created, create_status, create_err = _verify_create_event(client, access)
                calendar["create_event_verified"] = created
                calendar["can_create_events"] = created
                if create_err and not calendar["api_error"]:
                    calendar["api_error"] = create_err
                if create_status == 401 and not _get_refresh_token(db, company_id, user_id):
                    calendar["requires_reconnect"] = True

                if can_busy and created:
                    calendar["verification_summary"] = (
                        "Calendar funcional: disponibilidad OK y creación de eventos verificada."
                    )
                elif can_busy:
                    calendar["verification_summary"] = (
                        "Calendar parcial: disponibilidad OK, creación de eventos no verificada."
                    )
                elif created:
                    calendar["verification_summary"] = (
                        "Calendar parcial: creación OK, lectura de disponibilidad no verificada."
                    )
                elif calendar["api_reachable"]:
                    calendar["verification_summary"] = (
                        "Calendar accesible pero freebusy/crear eventos fallaron. Revisá permisos."
                    )

            calendar["effective_status"] = _resolve_effective_status(
                stored_connected=True,
                api_reachable=calendar["api_reachable"],
                requires_reconnect=calendar["requires_reconnect"],
                can_create_events=calendar["can_create_events"],
                http_status=calendar["http_status"],
                deep=deep,
            )

    db.commit()

    return {
        "oauth_configured": oauth_configured,
        "gmail": gmail,
        "google_calendar": calendar,
    }
