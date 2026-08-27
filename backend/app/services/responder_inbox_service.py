"""Bandeja unificada Responder — email + LinkedIn + WhatsApp con borrador pendiente."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.services.inbound_auto_reply import TASK_KIND_INBOUND_AUTO_REPLY


def _last_inbound(
    db: Session,
    prospect_id: int,
    *,
    channel: str | None = None,
) -> OutreachMessage | None:
    q = (
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
        )
        .order_by(OutreachMessage.created_at.desc())
        .limit(1)
    )
    if channel:
        q = q.where(OutreachMessage.channel == channel)
    return db.scalars(q).first()


def _has_pending_email_auto_reply(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(OutreachTask.id)
        .where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.kind == TASK_KIND_INBOUND_AUTO_REPLY,
            OutreachTask.status == "pending",
        )
        .limit(1)
    )
    return n is not None


def _email_reply_snippet(db: Session, prospect: Prospect) -> tuple[str, str | None]:
    """(inbound_preview, draft_hint) — draft puede ser None si está en Gmail."""
    msg = _last_inbound(db, prospect.id, channel="email")
    inbound = (msg.message or "").strip() if msg else ""
    if len(inbound) > 280:
        inbound = inbound[:277] + "…"
    draft = None
    if _has_pending_email_auto_reply(db, prospect.id):
        draft = "Borrador en Gmail (pendiente de envío o revisión)"
    return inbound, draft


def build_responder_inbox(
    db: Session,
    *,
    company_id: int,
    seller_id: int | None = None,
) -> dict[str, Any]:
    from app.services.linkedin_assisted_service import (
        _task_action,
        reply_visible_in_queue,
    )

    q = (
        select(Prospect, Campaign)
        .join(Campaign, Prospect.campaign_id == Campaign.id)
        .where(Campaign.company_id == company_id)
    )
    if seller_id is not None:
        q = q.where(Campaign.seller_id == seller_id)

    rows = db.execute(q).all()
    items: list[dict[str, Any]] = []

    for prospect, campaign in rows:
        if not prospect.last_inbound_at:
            continue

        base = {
            "prospect_id": prospect.id,
            "prospect_name": prospect.name,
            "company_name": prospect.company_name,
            "campaign_id": campaign.id,
            "campaign_name": campaign.name,
            "last_inbound_at": prospect.last_inbound_at.isoformat()
            if isinstance(prospect.last_inbound_at, datetime)
            else prospect.last_inbound_at,
            "sequence_paused": bool(getattr(prospect, "sequence_paused", False)),
        }

        action, is_reply = _task_action(db, prospect)
        li_draft = (prospect.linkedin_assisted_draft or "").strip()
        if is_reply and li_draft and reply_visible_in_queue(prospect):
            inbound = _last_inbound(db, prospect.id, channel="linkedin")
            items.append(
                {
                    **base,
                    "channel": "linkedin",
                    "draft": li_draft,
                    "inbound_preview": (inbound.message or "").strip()[:280] if inbound else "",
                    "focus_url": f"/campanas/{campaign.id}?focus=linkedin",
                }
            )
            continue

        wa_draft = (prospect.whatsapp_assisted_draft or "").strip()
        wa_inbound = _last_inbound(db, prospect.id, channel="whatsapp")
        if wa_draft and wa_inbound:
            items.append(
                {
                    **base,
                    "channel": "whatsapp",
                    "draft": wa_draft,
                    "inbound_preview": (wa_inbound.message or "").strip()[:280],
                    "focus_url": f"/campanas/{campaign.id}?focus=whatsapp",
                }
            )
            continue

        email_inbound = _last_inbound(db, prospect.id, channel="email")
        if email_inbound and (
            _has_pending_email_auto_reply(db, prospect.id)
            or bool(getattr(prospect, "sequence_paused", False))
        ):
            inbound_preview, draft_hint = _email_reply_snippet(db, prospect)
            if inbound_preview or draft_hint:
                items.append(
                    {
                        **base,
                        "channel": "email",
                        "draft": draft_hint or "Revisá la respuesta en Gmail",
                        "inbound_preview": inbound_preview,
                        "focus_url": f"/campanas/{campaign.id}?focus=email",
                    }
                )

    def _sort_key(row: dict[str, Any]) -> tuple:
        raw = row.get("last_inbound_at") or ""
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            ts = datetime.min.replace(tzinfo=UTC)
        ch_order = {"linkedin": 0, "whatsapp": 1, "email": 2}
        return (-ts.timestamp(), ch_order.get(row.get("channel") or "", 9))

    items.sort(key=_sort_key)

    by_channel = {"linkedin": 0, "whatsapp": 0, "email": 0}
    for it in items:
        ch = str(it.get("channel") or "")
        if ch in by_channel:
            by_channel[ch] += 1

    return {
        "items": items,
        "total": len(items),
        "by_channel": by_channel,
    }
