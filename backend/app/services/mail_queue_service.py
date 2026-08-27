"""Cola Mail: historial enviado + programación diaria de pendientes."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.schemas.mail_queue import (
    MailPendingItemRead,
    MailQueueDayBucket,
    MailQueueItemRead,
    MailQueueRead,
)
from app.services import daily_send_limits as dsl
from app.services import queue_day_schedule as qds

_SUBJECT_RE = re.compile(
    r"(?is)^(?:\[[^\]]+\]\s*)*Asunto:\s*(.+?)(?:\r?\n\r?\n|\r?\n)([\s\S]*)$"
)
_PREFIX_RE = re.compile(r"(?is)^(?:\[[^\]]+\]\s*)+")


def parse_mail_history_text(raw: str | None) -> tuple[str, str]:
    """Extrae (subject, body) del texto guardado en outreach_messages.message."""
    text = (raw or "").strip()
    if not text:
        return "", ""
    m = _SUBJECT_RE.match(text)
    if m:
        return (m.group(1) or "").strip(), (m.group(2) or "").strip()
    cleaned = _PREFIX_RE.sub("", text).strip()
    return "", cleaned


def gmail_web_link_for(message_id: str | None) -> str | None:
    mid = (message_id or "").strip()
    if not mid:
        return None
    return f"https://mail.google.com/mail/u/0/#all/{mid}"


def _touch_log(prospect: Prospect) -> dict[str, dict[str, Any]]:
    raw = getattr(prospect, "sequence_touch_log", None) or "{}"
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _pending_email_item(prospect: Prospect, *, day: int) -> MailPendingItemRead | None:
    entry = _touch_log(prospect).get(str(day), {})
    subject = (entry.get("subject") or "").strip()
    body = (entry.get("message_body") or entry.get("body") or "").strip()
    if not body:
        draft_raw = getattr(prospect, "playbook_draft_json", None) or "{}"
        try:
            drafts = json.loads(str(draft_raw))
            if isinstance(drafts, dict):
                d = drafts.get(str(day)) or drafts.get(day) or {}
                if isinstance(d, dict):
                    subject = subject or (d.get("subject") or "").strip()
                    body = body or (d.get("body") or d.get("message_body") or "").strip()
        except Exception:
            pass
    if not subject and not body:
        return None
    return MailPendingItemRead(
        prospect_id=int(prospect.id),
        prospect_name=(prospect.name or "").strip() or f"Prospecto #{prospect.id}",
        company_name=getattr(prospect, "company_name", None),
        email=(prospect.email or "").strip() or None,
        subject=subject or "(Sin asunto)",
        body=body,
        sequence_day=day,
    )


def _collect_pending_email_items(
    db: Session,
    campaign: Campaign,
    viewer: Any = None,
) -> list[MailPendingItemRead]:
    from app.services.prospect_sequence import next_executable_channel, next_executable_day

    rows = list(db.scalars(select(Prospect).where(Prospect.campaign_id == campaign.id)).all())
    if viewer is not None and rows:
        from app.services.campaign_visibility import filter_prospects_for_viewer

        rows = filter_prospects_for_viewer(viewer, campaign, rows)

    pending: list[MailPendingItemRead] = []
    for p in rows:
        if not getattr(p, "sequence_started_at", None):
            continue
        if next_executable_channel(p, campaign) != "email":
            continue
        day = next_executable_day(p, campaign)
        if day is None:
            continue
        item = _pending_email_item(p, day=day)
        if item is not None:
            pending.append(item)
    pending.sort(key=lambda x: (x.prospect_name or "").lower())
    return pending


def _build_email_schedule_days(
    pending: list[MailPendingItemRead],
    *,
    seller_id: int,
    db: Session,
) -> list[MailQueueDayBucket]:
    email_limit = dsl.limit_for(dsl.KIND_EMAIL)
    email_remaining = dsl.remaining(db, seller_id, dsl.KIND_EMAIL) if seller_id else email_limit
    day_rows = qds.schedule_single_budget(
        pending,
        daily_limit=email_limit,
        remaining_today=email_remaining,
    )
    buckets: list[MailQueueDayBucket] = []
    for day_offset, day_items in day_rows:
        buckets.append(
            MailQueueDayBucket(
                day_offset=day_offset,
                label=qds.day_label(day_offset),
                actionable=day_offset == 0,
                limit=email_limit,
                scheduled=len(day_items),
                items=day_items,
            )
        )
    return buckets


def build_campaign_mail_queue(
    db: Session,
    campaign_id: int,
    viewer: Any = None,
    *,
    limit: int = 100,
) -> MailQueueRead:
    campaign = db.get(Campaign, campaign_id)
    limit = max(1, min(int(limit or 100), 200))

    rows = list(
        db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.channel == "email",
                OutreachMessage.direction == "outbound",
                OutreachMessage.gmail_message_id.isnot(None),
            )
            .options(selectinload(OutreachMessage.prospect))
            .order_by(OutreachMessage.created_at.desc())
            .limit(limit)
        ).all()
    )

    if viewer is not None and campaign is not None and rows:
        from app.services.campaign_visibility import filter_prospects_for_viewer

        prospects = [r.prospect for r in rows if r.prospect is not None]
        # Dedup by id preserving order of first appearance in rows
        seen: set[int] = set()
        unique: list[Prospect] = []
        for p in prospects:
            if p.id in seen:
                continue
            seen.add(p.id)
            unique.append(p)
        allowed_ids = {
            p.id for p in filter_prospects_for_viewer(viewer, campaign, unique)
        }
        rows = [r for r in rows if r.prospect_id in allowed_ids]

    items: list[MailQueueItemRead] = []
    for row in rows:
        gid = (row.gmail_message_id or "").strip()
        if not gid:
            continue
        prospect = row.prospect
        subject, body = parse_mail_history_text(row.message)
        items.append(
            MailQueueItemRead(
                outreach_message_id=row.id,
                prospect_id=row.prospect_id,
                prospect_name=(prospect.name if prospect else None) or f"Prospecto #{row.prospect_id}",
                company_name=getattr(prospect, "company_name", None) if prospect else None,
                email=getattr(prospect, "email", None) if prospect else None,
                subject=subject or "(Sin asunto)",
                body=body or (row.message or "").strip(),
                sent_at=row.created_at,
                gmail_message_id=gid,
                gmail_web_link=gmail_web_link_for(gid),
            )
        )

    pending_items = _collect_pending_email_items(db, campaign, viewer) if campaign else []
    seller_id = int(getattr(campaign, "seller_id", 0) or 0) if campaign else 0
    return MailQueueRead(
        campaign_id=campaign_id,
        items=items,
        total=len(items),
        limit=dsl.limit_for(dsl.KIND_EMAIL),
        remaining_today=dsl.remaining(db, seller_id, dsl.KIND_EMAIL) if seller_id else dsl.limit_for(dsl.KIND_EMAIL),
        pending_total=len(pending_items),
        days=_build_email_schedule_days(pending_items, seller_id=seller_id, db=db) if pending_items else [],
    )
