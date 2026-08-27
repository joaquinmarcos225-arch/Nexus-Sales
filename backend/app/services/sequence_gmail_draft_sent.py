"""Detecta borradores Gmail de secuencia enviados por el SDR (sin botón manual)."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.models.user import User
from app.services.gmail_threads import (
    fetch_thread_full,
    gmail_get,
    norm_email,
    parse_address_email,
)
from app.services.prospect_sequence import (
    PLAYBOOK_DAYS,
    TOUCH_GENERADO,
    _playbook_step,
    _touch_log,
    mark_sequence_gmail_touch_sent,
)
from app.services.sequence_touch_gmail import sequence_email_touch_uses_gmail

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _header_map(payload: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").strip()
        if not name:
            continue
        out[name.lower()] = (h.get("value") or "").strip()
    return out


def gmail_draft_status(
    client: httpx.Client,
    access: str,
    draft_id: str,
) -> str:
    """Devuelve exists | missing | auth_error."""
    did = urllib.parse.quote(str(draft_id).strip(), safe="")
    if not did:
        return "missing"
    url = f"{GMAIL_API}/drafts/{did}"
    res = client.get(url, headers={"Authorization": f"Bearer {access}"}, timeout=45.0)
    if res.status_code == 404:
        return "missing"
    if res.status_code == 401:
        return "auth_error"
    if res.status_code >= 400:
        logger.warning("gmail draft lookup HTTP %s draft_id=%s", res.status_code, draft_id[:16])
        return "missing"
    return "exists"


def _subject_matches(expected: str, actual: str) -> bool:
    exp = (expected or "").strip().lower()
    act = (actual or "").strip().lower()
    if not exp:
        return bool(act)
    if exp == act:
        return True
    return exp in act or act in exp


def _message_is_user_outbound_to_prospect(
    msg: dict,
    *,
    user_email: str,
    prospect_email: str,
    subject: str,
) -> bool:
    labels = set(msg.get("labelIds") or [])
    if "DRAFT" in labels:
        return False
    headers = _header_map(msg.get("payload") or {})
    from_e = parse_address_email(headers.get("from", ""))
    to_raw = headers.get("to", "")
    to_e = parse_address_email(to_raw)
    ue = norm_email(user_email)
    pe = norm_email(prospect_email)
    if from_e != ue:
        return False
    if pe and pe not in norm_email(to_raw):
        if to_e != pe:
            return False
    if not _subject_matches(subject, headers.get("subject", "")):
        return False
    return "SENT" in labels or "INBOX" in labels


def thread_has_matching_outbound(
    client: httpx.Client,
    access: str,
    *,
    thread_id: str,
    user_email: str,
    prospect_email: str,
    subject: str,
) -> bool:
    data = fetch_thread_full(client, access, thread_id)
    if not data:
        return False
    for msg in data.get("messages") or []:
        if _message_is_user_outbound_to_prospect(
            msg,
            user_email=user_email,
            prospect_email=prospect_email,
            subject=subject,
        ):
            return True
    return False


def search_sent_to_prospect(
    client: httpx.Client,
    access: str,
    *,
    user_email: str,
    prospect_email: str,
    subject: str,
) -> bool:
    pe = norm_email(prospect_email)
    if not pe:
        return False
    subj = (subject or "").strip().replace('"', "")
    queries = [
        f'in:sent to:{pe} subject:"{subj[:80]}" newer_than:30d',
        f'in:sent to:{pe} newer_than:14d',
    ]
    for q in queries:
        data = gmail_get(client, access, "/messages", params={"q": q, "maxResults": "8"})
        if data.get("_unauthorized"):
            return False
        for ref in data.get("messages") or []:
            mid = ref.get("id")
            if not mid:
                continue
            meta = gmail_get(
                client,
                access,
                f"/messages/{urllib.parse.quote(str(mid), safe='')}",
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject"]},
            )
            if meta.get("_unauthorized"):
                return False
            if _message_is_user_outbound_to_prospect(
                meta,
                user_email=user_email,
                prospect_email=prospect_email,
                subject=subject,
            ):
                return True
    return False


def draft_touch_was_sent(
    client: httpx.Client,
    access: str,
    *,
    user_email: str,
    prospect: Prospect,
    subject: str,
    thread_id: str | None,
) -> bool:
    pe = norm_email(prospect.email)
    tid = (thread_id or prospect.gmail_thread_id or "").strip()
    if tid and thread_has_matching_outbound(
        client,
        access,
        thread_id=tid,
        user_email=user_email,
        prospect_email=pe,
        subject=subject,
    ):
        return True
    return search_sent_to_prospect(
        client,
        access,
        user_email=user_email,
        prospect_email=pe,
        subject=subject,
    )


def reconcile_prospect_gmail_draft_sents(
    db: Session,
    *,
    user: User,
    campaign: Campaign,
    prospect: Prospect,
    user_email: str,
    client: httpx.Client,
    access: str,
) -> list[int]:
    """Marca toques Gmail pendientes si el borrador ya fue enviado en Gmail."""
    log = _touch_log(prospect)
    marked: list[int] = []

    for day in PLAYBOOK_DAYS:
        entry = log.get(str(day), {})
        if entry.get("status") != TOUCH_GENERADO:
            continue
        step = _playbook_step(day)
        if step is None or not sequence_email_touch_uses_gmail(day=day, channel=step.channel):
            continue
        draft_id = str(entry.get("gmail_draft_id") or "").strip()
        if not draft_id:
            continue

        status = gmail_draft_status(client, access, draft_id)
        if status == "exists":
            continue
        if status == "auth_error":
            break

        subject = str(entry.get("subject") or "").strip()
        if not draft_touch_was_sent(
            client,
            access,
            user_email=user_email,
            prospect=prospect,
            subject=subject,
            thread_id=str(entry.get("gmail_thread_id") or "").strip() or None,
        ):
            continue

        try:
            mark_sequence_gmail_touch_sent(
                db,
                user=user,
                prospect=prospect,
                day=day,
                auto_detected=True,
            )
            marked.append(day)
            logger.info(
                "gmail_draft_auto_sent prospect_id=%s day=%s draft_id=%s",
                prospect.id,
                day,
                draft_id[:16],
            )
        except Exception as exc:
            logger.warning(
                "gmail_draft_auto_sent failed prospect_id=%s day=%s: %s",
                prospect.id,
                day,
                exc,
            )

    return marked


def reconcile_campaign_gmail_draft_sents(
    db: Session,
    *,
    user: User,
    campaign: Campaign,
    prospects: list[Prospect],
    user_email: str,
    client: httpx.Client,
    access: str,
) -> dict[str, Any]:
    total = 0
    by_prospect: dict[int, list[int]] = {}
    for prospect in prospects:
        days = reconcile_prospect_gmail_draft_sents(
            db,
            user=user,
            campaign=campaign,
            prospect=prospect,
            user_email=user_email,
            client=client,
            access=access,
        )
        if days:
            by_prospect[prospect.id] = days
            total += len(days)
    return {"gmail_draft_sents_detected": total, "by_prospect": by_prospect}
