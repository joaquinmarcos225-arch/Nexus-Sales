"""Crear borradores en Gmail con el token OAuth del SDR (sin enviar)."""

from __future__ import annotations

import base64
import logging
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.services import google_oauth
from app.services.oauth_tokens import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

GMAIL_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
GMAIL_PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"


def _rfc822_raw(*, to_addr: str, subject: str, body: str) -> str:
    from app.services.outbound_text_normalize import normalize_outbound_email_body

    msg = EmailMessage()
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(normalize_outbound_email_body(body))
    raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")


def _get_gmail_row(db: Session, company_id: int, user_id: int) -> ConnectedAccount | None:
    return db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.gmail.value,
        )
    ).first()


def _persist_new_access(db: Session, company_id: int, user_id: int, new_access: str) -> None:
    """Guarda access nuevo y rehabilita Gmail+Calendar (nunca deja status=error si el refresh funcionó)."""
    enc = encrypt_secret(new_access)
    for prov in (IntegrationProvider.gmail, IntegrationProvider.google_calendar):
        row = db.scalars(
            select(ConnectedAccount).where(
                ConnectedAccount.company_id == company_id,
                ConnectedAccount.user_id == user_id,
                ConnectedAccount.provider == prov.value,
            )
        ).first()
        if row is not None:
            row.access_token_encrypted = enc
            row.status = IntegrationStatus.connected.value


def _heal_google_rows_connected(db: Session, company_id: int, user_id: int) -> None:
    for prov in (IntegrationProvider.gmail, IntegrationProvider.google_calendar):
        row = db.scalars(
            select(ConnectedAccount).where(
                ConnectedAccount.company_id == company_id,
                ConnectedAccount.user_id == user_id,
                ConnectedAccount.provider == prov.value,
            )
        ).first()
        if row is not None and row.status != IntegrationStatus.connected.value:
            # Solo sanar si hay token usable (access o refresh).
            if decrypt_secret(row.access_token_encrypted) or decrypt_secret(
                row.refresh_token_encrypted
            ):
                row.status = IntegrationStatus.connected.value


def _refresh_access_token(db: Session, company_id: int, user_id: int, refresh_plain: str) -> str:
    data = {
        "client_id": google_oauth.oauth_client_id(),
        "client_secret": google_oauth.oauth_client_secret(),
        "refresh_token": refresh_plain,
        "grant_type": "refresh_token",
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(google_oauth.GOOGLE_TOKEN_URL, data=data)
            res.raise_for_status()
            payload = res.json()
    except httpx.HTTPStatusError as exc:
        body = (exc.response.text or "")[:240] if exc.response is not None else ""
        status = exc.response.status_code if exc.response is not None else "?"
        logger.warning(
            "Gmail OAuth token refresh HTTP %s company_id=%s user_id=%s body=%s",
            status,
            company_id,
            user_id,
            body,
        )
        revoked = status in (400, 401) and (
            "invalid_grant" in body.lower() or "revoked" in body.lower()
        )
        if revoked:
            raise RuntimeError(
                "Google revocó el acceso (invalid_grant). Hay que volver a conectar Google."
            ) from exc
        raise RuntimeError(
            f"Google OAuth refresh falló temporalmente (HTTP {status}). Reintentá más tarde."
        ) from exc
    except httpx.RequestError as exc:
        logger.warning(
            "Gmail OAuth token refresh network error company_id=%s user_id=%s: %s",
            company_id,
            user_id,
            exc,
        )
        raise RuntimeError("Gmail OAuth refresh: error de red") from exc
    access = payload.get("access_token")
    if not access or not isinstance(access, str):
        raise RuntimeError("Google no devolvió access_token al refrescar")
    _persist_new_access(db, company_id, user_id, access)
    db.commit()
    return access


def _get_calendar_row(db: Session, company_id: int, user_id: int) -> ConnectedAccount | None:
    return db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.google_calendar.value,
        )
    ).first()


def _get_refresh_token(db: Session, company_id: int, user_id: int) -> str | None:
    for getter in (_get_gmail_row, _get_calendar_row):
        row = getter(db, company_id, user_id)
        if row is None:
            continue
        refresh = decrypt_secret(row.refresh_token_encrypted)
        if refresh:
            return refresh
    return None


def _row_has_usable_oauth(row: ConnectedAccount | None) -> bool:
    if row is None:
        return False
    # Bastan blobs cifrados: no hace falta desencriptar para decidir "recuperable".
    if row.refresh_token_encrypted or row.access_token_encrypted:
        return True
    return False


def _resolve_google_oauth_row(
    db: Session,
    company_id: int,
    user_id: int,
    *,
    for_calendar: bool,
) -> ConnectedAccount:
    """
    El refresh_token manda: si existe (aunque status=error), la fila sigue siendo usable.
    """
    gmail = _get_gmail_row(db, company_id, user_id)
    calendar = _get_calendar_row(db, company_id, user_id)

    def _prefer(*rows: ConnectedAccount | None) -> ConnectedAccount | None:
        connected = [
            r
            for r in rows
            if r is not None
            and r.status == IntegrationStatus.connected.value
            and _row_has_usable_oauth(r)
        ]
        if connected:
            return connected[0]
        recoverable = [r for r in rows if _row_has_usable_oauth(r)]
        return recoverable[0] if recoverable else None

    preferred = (calendar, gmail) if for_calendar else (gmail, calendar)
    row = _prefer(*preferred)
    if row is None:
        label = "Google Calendar" if for_calendar else "Gmail"
        raise ValueError(
            f"{label} no está conectado para este usuario. Conectá Google en Integraciones.",
        )
    return row


def _probe_google_token(token: str, *, for_calendar: bool) -> bool:
    with httpx.Client(timeout=20.0) as client:
        if for_calendar:
            res = client.get(
                CALENDAR_LIST_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"maxResults": 1},
            )
        else:
            res = client.get(GMAIL_PROFILE_URL, headers={"Authorization": f"Bearer {token}"})
    return res.status_code != 401


def get_valid_google_oauth_connection(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    for_calendar: bool = False,
) -> tuple[str, ConnectedAccount]:
    """
    Access token vigente de Google OAuth.
    Si hay refresh_token, recupera la conexión aunque status haya quedado en error.
    """
    row = _resolve_google_oauth_row(db, company_id, user_id, for_calendar=for_calendar)
    access = decrypt_secret(row.access_token_encrypted)
    refresh = _get_refresh_token(db, company_id, user_id)

    if not access:
        if not refresh:
            raise ValueError("No hay access/refresh token de Google. Reconectá Google en Integraciones.")
        access = _refresh_access_token(db, company_id, user_id, refresh)
        row = _resolve_google_oauth_row(db, company_id, user_id, for_calendar=for_calendar)
        return access, row

    alive = _probe_google_token(access, for_calendar=for_calendar)
    if alive:
        if row.status != IntegrationStatus.connected.value:
            _heal_google_rows_connected(db, company_id, user_id)
            db.commit()
        return access, row

    if refresh:
        access = _refresh_access_token(db, company_id, user_id, refresh)
        row = _resolve_google_oauth_row(db, company_id, user_id, for_calendar=for_calendar)
        return access, row

    label = "Google Calendar" if for_calendar else "Gmail"
    raise RuntimeError(
        f"{label} rechazó el token (401) y no hay refresh token guardado. "
        "Reconectá Google en Integraciones.",
    )


def get_valid_gmail_connection(
    db: Session,
    *,
    company_id: int,
    user_id: int,
) -> tuple[str, ConnectedAccount]:
    """
    Access token vigente + fila ConnectedAccount (Gmail).
    Refresca el token si el actual está vencido (401 al perfil).
    """
    return get_valid_google_oauth_connection(
        db,
        company_id=company_id,
        user_id=user_id,
        for_calendar=False,
    )


def get_valid_google_calendar_connection(
    db: Session,
    *,
    company_id: int,
    user_id: int,
) -> tuple[str, ConnectedAccount]:
    """Token OAuth válido para operaciones de Google Calendar."""
    return get_valid_google_oauth_connection(
        db,
        company_id=company_id,
        user_id=user_id,
        for_calendar=True,
    )


def _post_draft(access_token: str, *, to_addr: str, subject: str, body: str) -> dict[str, Any]:
    raw = _rfc822_raw(to_addr=to_addr, subject=subject, body=body)
    payload = {"message": {"raw": raw}}
    with httpx.Client(timeout=45.0) as client:
        res = client.post(
            GMAIL_DRAFTS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )
        if res.status_code == 401:
            return {"_unauthorized": True, "raw": res.text}
        res.raise_for_status()
        return res.json()


def create_draft_for_user(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    to_addr: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """
    Crea un borrador real en Gmail.
    Devuelve dict con draft_id, message_id, gmail_web_link, thread_id.
    """
    access, _row = get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)

    result = _post_draft(access, to_addr=to_addr.strip(), subject=subject, body=body)
    if result.get("_unauthorized"):
        refresh = decrypt_secret(_row.refresh_token_encrypted)
        if refresh:
            access = _refresh_access_token(db, company_id, user_id, refresh)
            result = _post_draft(access, to_addr=to_addr.strip(), subject=subject, body=body)

    if result.get("_unauthorized"):
        refresh = decrypt_secret(_row.refresh_token_encrypted)
        if not refresh:
            raise RuntimeError(
                "Gmail rechazó el token (401) y no hay refresh token guardado. Reconectá Google en Conexiones (con prompt de consentimiento).",
            )
        raise RuntimeError("Gmail rechazó el token (401). Reconectá Google en Conexiones.")

    draft_id = result.get("id") or ""
    message = result.get("message") or {}
    message_id = message.get("id") or ""
    thread_id = message.get("threadId") or ""
    link = None
    if message_id:
        link = f"https://mail.google.com/mail/u/0/#all/{message_id}"
    return {
        "draft_id": str(draft_id),
        "message_id": str(message_id) if message_id else None,
        "thread_id": str(thread_id) if thread_id else None,
        "gmail_web_link": link,
    }
