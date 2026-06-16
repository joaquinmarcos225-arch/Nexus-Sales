"""Ejecución de follow-ups programados en NEXUS_REAL_MODE (borrador o envío Gmail)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import OutreachEmailMode, ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services.gmail_drafts import create_draft_for_user, get_valid_gmail_connection
from app.services.gmail_send import send_email_for_user
from app.services.openai_service import generate_gmail_draft_email
from app.services.outreach_simulation import make_message


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _gmail_style_campaign_ctx(campaign: Campaign) -> dict[str, str]:
    d = followup_engine._campaign_dict(campaign)
    raw = campaign.icp_ai_last_analysis
    if raw is None:
        d["icp_ai_digest"] = ""
    elif isinstance(raw, (dict, list)):
        try:
            d["icp_ai_digest"] = json.dumps(raw, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            d["icp_ai_digest"] = ""
    else:
        d["icp_ai_digest"] = str(raw)[:2000]
    return d


def count_campaign_real_email_outbounds_last_hour(db: Session, campaign_id: int) -> int:
    since = datetime.now(UTC) - timedelta(hours=1)
    n = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.channel == "email",
            OutreachMessage.direction == "outbound",
            OutreachMessage.gmail_message_id.isnot(None),
            OutreachMessage.created_at >= since,
        )
    )
    return int(n or 0)


def deliver_scheduled_followup_via_gmail(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    education: str,
) -> str:
    """
    Genera contenido y crea borrador Gmail o envía según campaña + NEXUS_AUTO_SEND_ENABLED.
    Devuelve: 'draft', 'sent', o 'skipped'.
    """
    if getattr(campaign, "automation_paused", False):
        return "skipped"

    mode = (getattr(campaign, "outreach_email_mode", None) or OutreachEmailMode.draft_only.value).strip()
    to_addr = (prospect.email or "").strip()
    if not to_addr or "@" not in to_addr:
        return "skipped"

    allowed = campaign.allowed_channels or []
    if isinstance(allowed, list) and allowed and "email" not in allowed:
        return "skipped"

    history_rows = followup_engine._messages_desc(db, prospect.id)
    history_payload = followup_engine._payload(history_rows)
    last_inbound = next(
        (m for m in reversed(history_rows) if m.direction == "inbound" and m.sender_type == "prospect"),
        None,
    )
    last_inbound_text = (last_inbound.message if last_inbound else None) or None

    norm_in = (
        ci.normalize_inbound_text_for_classification(last_inbound_text) if last_inbound_text else ""
    )
    booking_priority = bool(norm_in.strip()) and ci.inbound_wants_immediate_booking(norm_in)
    timing_soft = False

    campaign_ctx = _gmail_style_campaign_ctx(campaign)
    product_ctx = followup_engine._product_dict(campaign)
    prospect_ctx = {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
        "email": to_addr,
    }

    subject, body = generate_gmail_draft_email(
        prospect=prospect_ctx,
        campaign=campaign_ctx,
        product=product_ctx,
        tone=campaign.tone,
        education=education,
        conversation_history=history_payload,
        last_prospect_inbound=ci.normalize_inbound_text_for_classification(last_inbound_text)
        if last_inbound_text
        else None,
        prospect_timing_soft=timing_soft,
        prospect_booking_priority=booking_priority,
    )

    uid = int(campaign.seller_id)
    cid = int(campaign.company_id)
    thread_id = (prospect.gmail_thread_id or "").strip() or None

    auto_send = mode == OutreachEmailMode.auto_send.value and _truthy_env("NEXUS_AUTO_SEND_ENABLED")
    hourly_cap = int(os.getenv("NEXUS_AUTO_SEND_HOURLY_CAP", "8"))

    if auto_send and count_campaign_real_email_outbounds_last_hour(db, campaign.id) >= hourly_cap:
        return "skipped"

    if auto_send:
        _, row = get_valid_gmail_connection(db, company_id=cid, user_id=uid)
        from_addr = (row.external_email or "").strip()
        if not from_addr:
            return "skipped"
        out = send_email_for_user(
            db,
            company_id=cid,
            user_id=uid,
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )
        gid = (out.get("gmail_message_id") or "").strip() or None
        tid = (out.get("thread_id") or "").strip() or None
        if tid:
            prospect.gmail_thread_id = tid
        hist_text = f"[Gmail · envío automático Nexus]\nAsunto: {subject}\n\n{body}"
        om = make_message(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="ai",
            message=hist_text,
            channel="email",
            direction="outbound",
            gmail_message_id=gid,
        )
        db.add(om)
        db.flush()
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=body,
        )
        if prospect.status in (ProspectStatus.imported.value, ProspectStatus.compatible.value):
            prospect.status = ProspectStatus.contacted.value
            from app.services import pipeline_sync

            pipeline_sync.sync_pipeline_from_status(prospect)
        if not (prospect.preferred_channel or "").strip():
            prospect.preferred_channel = "email"
        return "sent"

    out = create_draft_for_user(
        db,
        company_id=cid,
        user_id=uid,
        to_addr=to_addr,
        subject=subject,
        body=body,
    )
    tid = (out.get("thread_id") or "").strip()
    if tid:
        prospect.gmail_thread_id = tid
    hist_text = f"[Borrador Gmail · follow-up programado]\nAsunto: {subject}\n\n{body}"
    db.add(
        make_message(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="system",
            message=hist_text,
            channel="email",
            direction="outbound",
        )
    )
    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=body,
    )
    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "email"
    return "draft"
