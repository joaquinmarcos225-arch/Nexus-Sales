"""Envío real de email vía Gmail API (users.messages.send), con threading RFC5322."""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services import gmail_drafts as gmail_drafts_mod
from app.services.gmail_drafts import get_valid_gmail_connection
from app.services.gmail_threads import fetch_thread_full
from app.services.oauth_tokens import decrypt_secret

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _headers_from_payload(payload: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").strip()
        if not name:
            continue
        out[name.lower()] = (h.get("value") or "").strip()
    return out


def _walk_parts_for_headers(part: dict, acc: dict[str, str]) -> None:
    for h in part.get("headers") or []:
        name = (h.get("name") or "").strip()
        if not name:
            continue
        acc.setdefault(name.lower(), (h.get("value") or "").strip())
    for sub in part.get("parts") or []:
        _walk_parts_for_headers(sub, acc)


def _headers_from_message(msg: dict) -> dict[str, str]:
    payload = msg.get("payload") or {}
    acc: dict[str, str] = {}
    _walk_parts_for_headers(payload, acc)
    if not acc:
        return _headers_from_payload(payload)
    return acc


def _normalize_msg_id(value: str | None) -> str | None:
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    if v.startswith("<") and v.endswith(">"):
        return v
    return f"<{v}>"


def _last_thread_message_headers(
    client: httpx.Client, access: str, thread_id: str
) -> tuple[str | None, str | None]:
    """
    Devuelve (Message-Id del último mensaje del hilo, References acumuladas sugeridas)
    para construir In-Reply-To / References en el envío.
    """
    data = fetch_thread_full(client, access, thread_id)
    if data is None:
        return None, None
    messages = list(data.get("messages") or [])
    if not messages:
        return None, None
    messages.sort(key=lambda m: int(m.get("internalDate") or 0))
    last = messages[-1]
    hdrs = _headers_from_message(last)
    mid_raw = hdrs.get("message-id")
    mid = _normalize_msg_id(mid_raw)
    refs_raw = (hdrs.get("references") or "").strip()
    refs_parts = [r.strip() for r in re.split(r"\s+", refs_raw) if r.strip()]
    if mid:
        if mid not in refs_parts:
            refs_parts.append(mid)
    references = " ".join(refs_parts) if refs_parts else None
    return mid, references


def _rfc822_raw(
    *,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    in_reply_to: str | None,
    references: str | None,
) -> str:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr.strip()
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body, subtype="plain", charset="utf-8")
    raw_bytes = msg.as_bytes()
    return base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")


def _post_send(access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=60.0) as client:
        res = client.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )
        if res.status_code == 401:
            return {"_unauthorized": True, "raw": res.text}
        res.raise_for_status()
        return res.json()


def send_email_for_user(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """
    Envía un mensaje MIME desde la cuenta Gmail OAuth del usuario.
    Si `thread_id` está presente, intenta mantener el hilo (threadId + In-Reply-To + References).
    """
    access, row = get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)

    in_reply_to: str | None = None
    references: str | None = None
    if thread_id and thread_id.strip():
        with httpx.Client(timeout=60.0) as client:
            in_reply_to, references = _last_thread_message_headers(client, access, thread_id.strip())

    raw = _rfc822_raw(
        from_addr=from_addr.strip(),
        to_addr=to_addr,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        references=references,
    )
    api_body: dict[str, Any] = {"raw": raw}
    tid = (thread_id or "").strip()
    if tid:
        api_body["threadId"] = tid

    result = _post_send(access, api_body)
    if result.get("_unauthorized"):
        refresh = decrypt_secret(row.refresh_token_encrypted)
        if refresh:
            access = gmail_drafts_mod._refresh_access_token(db, company_id, user_id, refresh)
            result = _post_send(access, api_body)

    if result.get("_unauthorized"):
        raise RuntimeError("Gmail rechazó el token (401) al enviar. Reconectá Google en Conexiones.")

    mid = result.get("id") or ""
    out_tid = result.get("threadId") or tid or ""
    link = f"https://mail.google.com/mail/u/0/#all/{mid}" if mid else None
    return {
        "gmail_message_id": str(mid) if mid else None,
        "thread_id": str(out_tid) if out_tid else None,
        "gmail_web_link": link,
    }
