"""Primer contacto real por Gmail (borrador o envío) para campañas activas."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, OutreachEmailMode, ProspectStatus
from app.models.outreach import OutreachMessage, OutreachSequence
from app.models.prospect import Prospect
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services import followup_engine, pipeline_sync
from app.services import multichannel_sequence as mseq
from app.services.openai_service import generate_gmail_draft_email
from app.services.outreach_simulation import make_message
from app.services.gmail_drafts import create_draft_for_user, get_valid_gmail_connection
from app.services.gmail_send import send_email_for_user
from app.services.real_followup_gmail import (
    _gmail_style_campaign_ctx,
    _truthy_env,
    count_campaign_real_email_outbounds_last_hour,
)

_SKIP_GROUPS = frozenset(
    {
        mseq.SEQUENCE_GROUP_ENCAJONADO,
        mseq.SEQUENCE_GROUP_POSTERGADO,
        mseq.SEQUENCE_GROUP_REUNIONES,
    }
)


def prospect_has_any_outbound(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "outbound",
        )
    )
    return int(n or 0) > 0


_TERMINAL_STATUSES = frozenset(
    {
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
        ProspectStatus.failed.value,
    }
)


def _eligible_prospects(db: Session, campaign_id: int) -> list[Prospect]:
    rows = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign_id,
            Prospect.status.in_(
                (
                    ProspectStatus.imported.value,
                    ProspectStatus.compatible.value,
                    ProspectStatus.not_compatible.value,
                )
            ),
        )
    ).all()
    out: list[Prospect] = []
    for p in rows:
        if (p.status or "") in _TERMINAL_STATUSES:
            continue
        if getattr(p, "sequence_paused", False):
            continue
        grp = getattr(p, "sequence_group", None)
        if grp in _SKIP_GROUPS:
            continue
        em = (p.email or "").strip()
        if not em or "@" not in em:
            continue
        if prospect_has_any_outbound(db, p.id):
            continue
        out.append(p)
    return out


def _anchor_sequence_state(db: Session, prospect: Prospect, *, now: datetime) -> None:
    if prospect.sequence_started_at is None:
        prospect.sequence_started_at = now
        prospect.sequence_group = mseq.SEQUENCE_GROUP_CONTACTADO
        prospect.sequence_state = getattr(prospect, "sequence_state", None) or mseq.STATE_SIN
        prospect.sequence_paused = False
        fired = mseq._fired_list(prospect)
        if 1 not in fired:
            mseq._set_fired(prospect, fired + [1])


def deliver_initial_outreach_via_gmail(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    education: str,
) -> str:
    """
    Primer email: genera contenido, borrador o envío según campaña.
    Devuelve: 'draft', 'sent', o 'skipped'.
    """
    if getattr(campaign, "automation_paused", False):
        return "skipped"
    if prospect_has_any_outbound(db, prospect.id):
        return "skipped"

    mode = (getattr(campaign, "outreach_email_mode", None) or OutreachEmailMode.draft_only.value).strip()
    to_addr = (prospect.email or "").strip()
    if not to_addr or "@" not in to_addr:
        return "skipped"

    allowed = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    if allowed and "email" not in allowed:
        return "skipped"

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
        conversation_history=[],
        last_prospect_inbound=None,
        prospect_timing_soft=False,
        prospect_booking_priority=False,
    )

    uid = int(campaign.seller_id)
    cid = int(campaign.company_id)
    now = datetime.now(UTC)
    _anchor_sequence_state(db, prospect, now=now)

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
            thread_id=None,
        )
        gid = (out.get("gmail_message_id") or "").strip() or None
        tid = (out.get("thread_id") or "").strip() or None
        if tid:
            prospect.gmail_thread_id = tid
        hist_text = f"[Gmail · primer contacto automático Nexus]\nAsunto: {subject}\n\n{body}"
        db.add(
            make_message(
                prospect_id=prospect.id,
                campaign_id=campaign.id,
                sender_type="ai",
                message=hist_text,
                channel="email",
                direction="outbound",
                gmail_message_id=gid,
            )
        )
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=body,
        )
        prospect.status = ProspectStatus.contacted.value
        pipeline_sync.sync_pipeline_from_status(prospect)
        if (campaign.calendar_link or "") in body:
            prospect.sequence_state = mseq.STATE_LINK
        if not (prospect.preferred_channel or "").strip():
            prospect.preferred_channel = "email"
        followup_engine.schedule_followup_task(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            campaign=campaign,
            title="Seguimiento tras primer contacto",
        )
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
    hist_text = f"[Borrador Gmail · primer contacto automático]\nAsunto: {subject}\n\n{body}"
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
    prospect.status = ProspectStatus.contacted.value
    pipeline_sync.sync_pipeline_from_status(prospect)
    if (campaign.calendar_link or "") in body:
        prospect.sequence_state = mseq.STATE_LINK
    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "email"
    followup_engine.schedule_followup_task(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        campaign=campaign,
        title="Seguimiento tras primer contacto",
    )
    return "draft"


def ensure_outreach_sequence_running(
    db: Session, campaign: Campaign, *, force: bool = False
) -> OutreachSequence:
    seq = db.scalars(
        select(OutreachSequence).where(OutreachSequence.campaign_id == campaign.id)
    ).first()
    if seq is None:
        seq = OutreachSequence(campaign_id=campaign.id, is_running=False, current_step=0)
        db.add(seq)
        db.flush()
    active = campaign.status == CampaignStatus.running.value and not getattr(
        campaign, "automation_paused", False
    )
    if force or (not seq.is_running and active):
        seq.is_running = True
        seq.current_step = int(seq.current_step or 0) + 1
    return seq


def process_campaign_initial_outreach(
    db: Session,
    campaign: Campaign,
    education: str,
    *,
    max_batch: int | None = None,
) -> dict[str, Any]:
    """Procesa hasta `max_batch` prospectos sin outbound en la campaña."""
    if getattr(campaign, "automation_paused", False):
        return {"drafts": 0, "sent": 0, "skipped": 0, "errors": 0}

    batch = max_batch
    if batch is None:
        batch = int(os.getenv("NEXUS_INITIAL_OUTREACH_BATCH_SIZE", "5"))
    batch = max(1, min(batch, 50))

    drafts = sent = skipped = errors = 0
    error_messages: list[str] = []
    prospects = _eligible_prospects(db, campaign.id)[:batch]

    for prospect in prospects:
        try:
            outcome = deliver_initial_outreach_via_gmail(
                db, campaign=campaign, prospect=prospect, education=education
            )
            if outcome == "draft":
                drafts += 1
            elif outcome == "sent":
                sent += 1
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            error_messages.append(
                f"prospect={prospect.id} ({prospect.name}): {type(exc).__name__}: {exc}"[:400]
            )

    touched = drafts + sent
    if touched:
        mseq._append_log(
            campaign,
            f"Nexus: {touched} primeros contactos por Gmail "
            f"({drafts} borradores, {sent} enviados).",
            kind="sequence",
        )

    if not prospects and touched == 0 and errors == 0:
        eligible_n = len(_eligible_prospects(db, campaign.id))
        if eligible_n == 0:
            error_messages.append(
                "No hay prospectos listos (email válido, sin mensaje previo, estado importado/compatible)."
            )

    return {
        "drafts": drafts,
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "error_messages": error_messages[:24],
        "day1_sent": touched,
        "used_gmail": True,
    }
