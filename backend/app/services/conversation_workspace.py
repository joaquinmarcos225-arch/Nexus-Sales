"""Workspace de conversación para bandeja SDR."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services import prospect_commercial_state as pcs
from app.services.commercial_conversation_agent import conversation_state_label
from app.services.commercial_conversation_agent import strip_auto_reply_marker


def _touch_turns(prospect: Prospect, *, include_testing: bool = True) -> list[dict[str, Any]]:
    raw = getattr(prospect, "sequence_touch_log", None)
    if not raw:
        return []
    try:
        log = json.loads(raw)
    except Exception:
        return []
    if not isinstance(log, dict):
        return []
    turns: list[dict[str, Any]] = []
    for key, entry in sorted(log.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
        if not isinstance(entry, dict):
            continue
        if not include_testing and entry.get("testing"):
            continue
        if not entry.get("response_class") and not entry.get("inbound_message"):
            continue
        turns.append(
            {
                "day": int(key) if str(key).isdigit() else None,
                "inbound_text": entry.get("inbound_message"),
                "response_class": entry.get("response_class"),
                "response_class_label": entry.get("response_class_label"),
                "reply_objective": entry.get("reply_objective"),
                "reply_objective_label": entry.get("reply_objective_label"),
                "classification_confidence": entry.get("classification_confidence"),
                "auto_sent": bool(entry.get("auto_sent")),
                "delivery_mode": entry.get("delivery_mode"),
                "escalation_reason": entry.get("escalation_reason"),
                "inbound_at": entry.get("inbound_at"),
                "outbound_message_id": entry.get("outbound_message_id"),
                "meeting_id": entry.get("meeting_id"),
                "google_calendar_event_id": entry.get("google_calendar_event_id"),
                "google_calendar_html_link": entry.get("google_calendar_html_link"),
                "calendar_confirmed": bool(entry.get("google_calendar_event_id")),
                "calendar_created": entry.get("calendar_created"),
                "meeting_scheduled_for": entry.get("meeting_scheduled_for"),
                "creation_method": entry.get("creation_method"),
                "testing": bool(entry.get("testing")),
            }
        )
    return turns


def build_conversation_workspace(
    db: Session,
    *,
    prospect: Prospect,
    include_testing: bool = True,
) -> dict[str, Any]:
    rows = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect.id)
        .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
    ).all()
    if not include_testing:
        rows = [m for m in rows if not bool(getattr(m, "is_testing", False))]

    messages = [
        {
            "id": m.id,
            "prospect_id": m.prospect_id,
            "campaign_id": m.campaign_id,
            "sender_type": m.sender_type,
            "message": strip_auto_reply_marker(m.message or ""),
            "raw_message": m.message,
            "channel": m.channel,
            "direction": m.direction,
            "is_testing": bool(getattr(m, "is_testing", False)),
            "created_at": m.created_at,
            "is_auto_sent": (m.message or "").strip().startswith("[auto-reply:"),
        }
        for m in rows
    ]

    meeting_rows = db.scalars(
        select(Meeting)
        .where(Meeting.prospect_id == prospect.id)
        .order_by(Meeting.scheduled_for.desc(), Meeting.id.desc())
    ).all()
    meetings = [
        {
            "id": mt.id,
            "title": mt.title,
            "scheduled_for": mt.scheduled_for,
            "meeting_status": mt.meeting_status,
            "duration_minutes": mt.duration_minutes,
            "google_calendar_event_id": mt.google_calendar_event_id,
            "google_calendar_html_link": mt.google_calendar_html_link,
            "calendar_confirmed": bool(mt.google_calendar_event_id),
            "creation_method": getattr(mt, "creation_method", None) or "manual",
            "created_by_user_id": getattr(mt, "created_by_user_id", None),
        }
        for mt in meeting_rows
    ]

    conv_state = getattr(prospect, "conversation_state", None) or "sin_conversacion"
    commercial = pcs.commercial_fields(prospect, db=db, include_testing=include_testing)

    return {
        "prospect_id": prospect.id,
        "prospect_name": prospect.name,
        "prospect_company": prospect.company_name,
        "prospect_email": prospect.email,
        "conversation_state": conv_state,
        "conversation_state_label": conversation_state_label(conv_state),
        **commercial,
        "messages": messages,
        "meetings": meetings,
        "turns": _touch_turns(prospect, include_testing=include_testing),
        "message_count": len(messages),
        "has_active_conversation": bool(messages),
    }
