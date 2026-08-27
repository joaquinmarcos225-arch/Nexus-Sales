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
from app.services.outreach_simulation import make_message
from app.services.sdr_outreach_compose import (
    generate_playbook_touch_for_prospect,
    persist_day1_playbook_draft,
)
from app.services.gmail_drafts import create_draft_for_user, get_valid_gmail_connection
from app.services.gmail_send import send_email_for_user
from app.services.email_deliverability import deliverable_email_skip_reason, is_real_deliverable_email
from app.services.real_followup_gmail import (
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
        if not is_real_deliverable_email(em):
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
    skip_reason = deliverable_email_skip_reason(to_addr)
    if skip_reason:
        return "skipped"

    allowed = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    if allowed and "email" not in allowed:
        return "skipped"

    from app.services.campaign_sequence_channels import (
        effective_channel_for_day,
        effective_playbook_step,
    )

    if effective_channel_for_day(campaign, 1) != "email":
        return "skipped"

    from app.services import daily_send_limits as dsl

    seller_id = int(campaign.seller_id or 0)
    owner_id = int(prospect.owner_user_id or 0)
    # Preferí Gmail del owner (p. ej. director que tomó el prospecto); si no, el seller.
    gmail_candidates: list[int] = []
    for uid in (owner_id, seller_id):
        if uid > 0 and uid not in gmail_candidates:
            gmail_candidates.append(uid)
    if not gmail_candidates:
        return "skipped"

    step = effective_playbook_step(campaign, 1)
    subject, body = generate_playbook_touch_for_prospect(
        db,
        campaign=campaign,
        prospect=prospect,
        education=education,
        channel="email",
        prior_touches=[],
    )
    if not (subject or "").strip():
        subject = "Seguimiento"
    persist_day1_playbook_draft(
        prospect,
        subject=subject,
        body=body,
        objective=(step.objective if step else "Primer contacto"),
    )

    cid = int(campaign.company_id)
    now = datetime.now(UTC)
    _anchor_sequence_state(db, prospect, now=now)

    auto_send = mode == OutreachEmailMode.auto_send.value and _truthy_env("NEXUS_AUTO_SEND_ENABLED")
    # Día 1 de campaña: sin tope horario artificial — el límite diario protege la cuenta.
    hourly_cap = int(os.getenv("NEXUS_AUTO_SEND_HOURLY_CAP", "500"))

    if auto_send and count_campaign_real_email_outbounds_last_hour(db, campaign.id) >= hourly_cap:
        return "skipped"

    sender_id: int | None = None
    row = None
    for candidate in gmail_candidates:
        try:
            _, row = get_valid_gmail_connection(db, company_id=cid, user_id=candidate)
            sender_id = candidate
            break
        except Exception:
            continue
    if sender_id is None or row is None:
        return "skipped"

    if not dsl.can_send(db, sender_id, dsl.KIND_EMAIL):
        return "skipped"

    uid = sender_id

    if auto_send:
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
        from app.services.prospect_sequence import compute_next_touch

        next_at, _ = compute_next_touch(prospect, campaign)
        prospect.next_touch_at = next_at
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
    from app.services.prospect_sequence import compute_next_touch

    next_at, _ = compute_next_touch(prospect, campaign)
    prospect.next_touch_at = next_at
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
    """
    Día 1: contacta a TODOS los elegibles de la campaña en este tick
    (mismo día de activación), hasta agotar cola o el límite diario de email.
    """
    if getattr(campaign, "automation_paused", False):
        return {"drafts": 0, "sent": 0, "skipped": 0, "errors": 0}

    batch = max_batch
    if batch is None:
        # Default alto: blast del Día 1, no drip de 3–5.
        batch = int(os.getenv("NEXUS_INITIAL_OUTREACH_BATCH_SIZE", "500"))
    batch = max(1, min(batch, 500))

    drafts = sent = skipped = errors = 0
    error_messages: list[str] = []
    prospects = _eligible_prospects(db, campaign.id)[:batch]
    hit_daily_cap = False

    for prospect in prospects:
        if hit_daily_cap:
            skipped += 1
            continue
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
                # Si el seller ya no puede mandar más hoy, no quemar el resto del batch.
                from app.services import daily_send_limits as dsl

                seller_id = int(campaign.seller_id or 0)
                owner_id = int(prospect.owner_user_id or 0)
                uid = owner_id or seller_id
                if uid and not dsl.can_send(db, uid, dsl.KIND_EMAIL):
                    hit_daily_cap = True
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
        "daily_cap_hit": hit_daily_cap,
    }
