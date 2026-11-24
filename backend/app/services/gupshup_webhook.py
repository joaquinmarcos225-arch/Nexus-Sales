"""Ingesta de webhooks Gupshup: estados, billing-event e inbound.

Fuente de verdad de cobro: billing-event con deductions.billable=true.
failed/enqueued/submitted no descuentan el paquete.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.company import Company
from app.models.outreach import OutreachMessage
from app.models.whatsapp_dispatch import WhatsAppDispatch, WhatsAppWebhookEvent
from app.services.whatsapp_commercial import maybe_pause_after_billing
from app.services.whatsapp_inbound_sync import (
    register_whatsapp_inbound,
    resolve_prospect_by_whatsapp_digits,
)

logger = logging.getLogger(__name__)

_EVENT_RANK = {
    "enqueued": 1,
    "sent": 2,
    "delivered": 3,
    "read": 4,
    "deleted": 4,
    "failed": 9,
}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _payload_of(body: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(body.get("payload"))


def _remember_event(db: Session, event_key: str, event_type: str, body: dict[str, Any]) -> bool:
    """True si este evento es nuevo. False si ya lo procesamos (idempotente)."""
    key = event_key[:160]
    existing = db.scalar(select(WhatsAppWebhookEvent.id).where(WhatsAppWebhookEvent.event_key == key))
    if existing:
        return False
    db.add(
        WhatsAppWebhookEvent(
            event_key=key,
            event_type=event_type[:32],
            payload_json=json.dumps(body, ensure_ascii=False)[:8000],
        )
    )
    db.flush()
    return True


def _find_dispatch(db: Session, *, gs_id: str | None, wa_id: str | None) -> WhatsAppDispatch | None:
    if gs_id:
        row = db.scalar(
            select(WhatsAppDispatch).where(WhatsAppDispatch.gupshup_message_id == gs_id)
        )
        if row:
            return row
    if wa_id:
        row = db.scalar(
            select(WhatsAppDispatch).where(WhatsAppDispatch.whatsapp_message_id == wa_id)
        )
        if row:
            return row
    return None


def _apply_message_event(db: Session, dispatch: WhatsAppDispatch, event_type: str, inner: dict[str, Any]) -> None:
    rank = _EVENT_RANK.get(event_type, 0)
    current = _EVENT_RANK.get(str(dispatch.last_event or ""), 0)
    if rank and rank >= current:
        dispatch.last_event = event_type
    if event_type in ("sent", "delivered", "read"):
        wa_id = str(inner.get("whatsappMessageId") or inner.get("id") or "").strip()
        if wa_id and "-" not in wa_id:
            dispatch.whatsapp_message_id = wa_id[:128]
            if dispatch.outreach_message_id:
                msg = db.get(OutreachMessage, dispatch.outreach_message_id)
                if msg is not None and not (msg.whatsapp_message_id or "").strip():
                    msg.whatsapp_message_id = wa_id[:128]
    if event_type == "failed":
        reason = str(inner.get("reason") or inner.get("code") or "failed")
        dispatch.failed_reason = reason[:500]
        dispatch.reserved_units = 0
        if dispatch.billable is None:
            dispatch.billable = False


def _apply_billing(db: Session, dispatch: WhatsAppDispatch, deductions: dict[str, Any]) -> None:
    billable = deductions.get("billable")
    if isinstance(billable, str):
        billable = billable.strip().lower() in ("1", "true", "yes")
    dispatch.billable = bool(billable)
    if dispatch.billable:
        dispatch.billed_units = 1
        dispatch.reserved_units = 0
        category = str(deductions.get("type") or deductions.get("category") or "").strip()
        if category:
            dispatch.category = category[:32]
    else:
        dispatch.billed_units = 0
        dispatch.reserved_units = 0
    company = db.get(Company, dispatch.company_id)
    if company is not None:
        maybe_pause_after_billing(db, company)


def ingest_gupshup_webhook(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {"ok": True, "skipped": "not_object"}
    event_type = str(body.get("type") or "").strip().lower()
    payload = _payload_of(body)
    if event_type == "message-event":
        return _ingest_message_event(db, body, payload)
    if event_type == "billing-event":
        return _ingest_billing_event(db, body, payload)
    if event_type == "message":
        return _ingest_inbound(db, body, payload)
    return {"ok": True, "skipped": event_type or "unknown"}


def _ingest_message_event(db: Session, body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    inner = _as_dict(payload.get("payload"))
    status = str(payload.get("type") or inner.get("type") or "").strip().lower()
    gs_id = str(payload.get("gsId") or payload.get("gsid") or "").strip()
    raw_id = str(payload.get("id") or "").strip()
    if not gs_id and status in ("enqueued", "failed"):
        gs_id = raw_id
    key = f"{gs_id or raw_id}:{status or 'message-event'}"
    if not _remember_event(db, key, status or "message-event", body):
        return {"ok": True, "duplicate": True, "type": "message-event"}
    dispatch = _find_dispatch(db, gs_id=gs_id or None, wa_id=raw_id or None)
    if dispatch is None:
        logger.info("gupshup message-event unmatched gsId=%s id=%s status=%s", gs_id, raw_id, status)
        return {"ok": True, "unmatched": True, "type": "message-event"}
    _apply_message_event(db, dispatch, status, {**inner, "id": raw_id, "gsId": gs_id})
    return {"ok": True, "type": "message-event", "status": status, "dispatch_id": dispatch.id}


def _ingest_billing_event(db: Session, body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    refs = _as_dict(payload.get("references"))
    deductions = _as_dict(payload.get("deductions"))
    gs_id = str(refs.get("gsId") or refs.get("gsid") or "").strip()
    wa_id = str(refs.get("id") or "").strip()
    key = f"{gs_id or wa_id}:billing"
    if not _remember_event(db, key, "billing", body):
        return {"ok": True, "duplicate": True, "type": "billing-event"}
    dispatch = _find_dispatch(db, gs_id=gs_id or None, wa_id=wa_id or None)
    if dispatch is None:
        logger.info("gupshup billing-event unmatched gsId=%s id=%s", gs_id, wa_id)
        return {"ok": True, "unmatched": True, "type": "billing-event"}
    _apply_billing(db, dispatch, deductions)
    return {
        "ok": True,
        "type": "billing-event",
        "billable": bool(dispatch.billable),
        "dispatch_id": dispatch.id,
        "billed_units": int(dispatch.billed_units or 0),
    }


def _ingest_inbound(db: Session, body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    sender = _as_dict(payload.get("sender"))
    inner = _as_dict(payload.get("payload"))
    from_digits = str(sender.get("phone") or payload.get("source") or "").strip()
    text = str(inner.get("text") or payload.get("text") or "").strip()
    msg_id = str(payload.get("id") or "").strip()
    if not from_digits or not text:
        return {"ok": True, "skipped": "inbound_empty"}
    key = f"{msg_id or from_digits}:{text[:40]}:inbound"
    if not _remember_event(db, key, "inbound", body):
        return {"ok": True, "duplicate": True, "type": "message"}
    prospect = resolve_prospect_by_whatsapp_digits(db, company_id=None, from_digits=from_digits)
    if prospect is None:
        return {"ok": True, "unmatched": True, "type": "message"}
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None:
        return {"ok": True, "unmatched": True, "type": "message"}
    register_whatsapp_inbound(
        db,
        prospect=prospect,
        campaign=campaign,
        message=text,
        whatsapp_message_id=msg_id or None,
        send_via="gupshup",
    )
    return {"ok": True, "type": "message", "prospect_id": prospect.id}
