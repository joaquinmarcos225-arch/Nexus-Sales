"""
Motor de sequía automatizada (simulado).

Futuro cercano:
- Worker/cron (Celery, APScheduler, Cloud Scheduler) ejecuta run_due_followups periódicamente.
- Conectores LinkedIn/Gmail/WhatsApp consumen los mismos registros OutreachMessage sin cambiar el contrato.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.campaign_channels import coerce_allowed_channels

FOLLOWUP_DELAY_DAYS = int(os.getenv("NEXUS_FOLLOWUP_DELAY_DAYS", "3"))
MAX_AUTO_FOLLOWUPS = int(os.getenv("NEXUS_MAX_AUTO_FOLLOWUPS", "5"))


def _now() -> datetime:
    return datetime.now(UTC)


def _effective_followup_days(days: int | None, campaign: Campaign | None) -> int:
    if days is not None:
        return max(1, min(int(days), 90))
    if campaign is not None:
        cd = getattr(campaign, "followup_delay_days", None)
        if cd is not None:
            try:
                v = int(cd)
                if v >= 1:
                    return max(1, min(v, 90))
            except (TypeError, ValueError):
                pass
    return FOLLOWUP_DELAY_DAYS


def effective_max_auto_followups(campaign: Campaign | None) -> int:
    if campaign is None:
        return MAX_AUTO_FOLLOWUPS
    mx = getattr(campaign, "max_auto_followups", None)
    if mx is not None:
        try:
            v = int(mx)
            if v >= 1:
                return max(1, min(v, 50))
        except (TypeError, ValueError):
            pass
    return MAX_AUTO_FOLLOWUPS


def cancel_pending_followup_tasks(db: Session, prospect_id: int) -> None:
    db.execute(
        update(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.task_kind == "scheduled_followup",
            OutreachTask.status == "pending",
        )
        .values(status="cancelled", updated_at=_now())
    )


def cancel_deferred_resume_tasks(db: Session, prospect_id: int) -> None:
    db.execute(
        update(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.task_kind == "deferred_sequence_resume",
            OutreachTask.status == "pending",
        )
        .values(status="cancelled", updated_at=_now())
    )


def schedule_followup_task(
    db: Session,
    *,
    company_id: int,
    campaign_id: int,
    prospect_id: int,
    days: int | None = None,
    campaign: Campaign | None = None,
    title: str | None = None,
) -> OutreachTask:
    d = _effective_followup_days(days, campaign)
    cancel_pending_followup_tasks(db, prospect_id)
    due = _now() + timedelta(days=d)
    t = OutreachTask(
        company_id=company_id,
        campaign_id=campaign_id,
        prospect_id=prospect_id,
        task_kind="scheduled_followup",
        title=title or f"Follow-up automático ({d}d)",
        notes="Generado por motor de sequía. En producción lo dispararía un job programado.",
        due_at=due,
        status="pending",
    )
    db.add(t)
    return t


def create_review_inbound_task(
    db: Session,
    *,
    company_id: int,
    campaign_id: int,
    prospect_id: int,
) -> None:
    t = OutreachTask(
        company_id=company_id,
        campaign_id=campaign_id,
        prospect_id=prospect_id,
        task_kind="review_inbound",
        title="Revisar respuesta del prospecto",
        notes="Nueva respuesta inbound clasificada.",
        due_at=_now(),
        status="pending",
    )
    db.add(t)


def create_awaiting_reply_task(
    db: Session,
    *,
    company_id: int,
    campaign_id: int,
    prospect_id: int,
) -> None:
    t = OutreachTask(
        company_id=company_id,
        campaign_id=campaign_id,
        prospect_id=prospect_id,
        task_kind="awaiting_reply",
        title="Esperando respuesta del prospecto",
        notes="Se envió mensaje; pendiente de réplica.",
        due_at=_now() + timedelta(days=1),
        status="pending",
    )
    db.add(t)


def create_hot_lead_task(
    db: Session,
    *,
    company_id: int,
    campaign_id: int,
    prospect_id: int,
) -> None:
    t = OutreachTask(
        company_id=company_id,
        campaign_id=campaign_id,
        prospect_id=prospect_id,
        task_kind="hot_lead",
        title="Prospecto caliente (alto interés)",
        notes="Interés alto detectado por IA + reglas.",
        due_at=_now(),
        status="pending",
    )
    db.add(t)


def record_ai_outbound(
    db: Session,
    prospect: Prospect,
    *,
    campaign_calendar_link: str | None,
    outbound_text: str,
) -> None:
    prev = int(prospect.outreach_touch_count or 0)
    prospect.outreach_touch_count = prev + 1
    prospect.last_outbound_at = _now()
    if prev >= 1:
        prospect.followup_count = int(getattr(prospect, "followup_count", 0) or 0) + 1
        prospect.last_followup_at = _now()
    if campaign_calendar_link and campaign_calendar_link.strip() in (outbound_text or ""):
        prospect.meeting_nudge_sent_at = _now()


def record_prospect_inbound(db: Session, prospect: Prospect) -> None:
    prospect.last_inbound_at = _now()


def apply_inbound_signals(
    db: Session,
    prospect: Prospect,
    *,
    objection_type: str | None,
    interest_level: str,
) -> None:
    if objection_type:
        prospect.objection_type = objection_type
        prospect.objection_detected_at = _now()
    lvl = (interest_level or "low").lower()
    if lvl not in ("low", "medium", "high"):
        lvl = "low"
    prospect.interest_level = lvl


def count_inbound_prospect_messages(db: Session, prospect_id: int) -> int:
    res = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "inbound",
            OutreachMessage.sender_type == "prospect",
        )
    )
    return int(res or 0)


def _messages_desc(db: Session, prospect_id: int) -> list[OutreachMessage]:
    return list(
        db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect_id)
            .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
        ).all()
    )


def _payload(msgs: list[OutreachMessage]) -> list[dict[str, str]]:
    return [
        {"sender_type": m.sender_type, "direction": m.direction, "message": m.message}
        for m in msgs
    ]


def _campaign_dict(campaign: Campaign) -> dict[str, str]:
    ch = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    prio = " → ".join(ch)
    digest = ""
    raw = getattr(campaign, "icp_ai_last_analysis", None)
    if isinstance(raw, dict):
        digest = str(raw.get("recommendations") or raw.get("notes") or "")[:1200]
    return {
        "name": campaign.name,
        "tone": campaign.tone,
        "target_company_size": campaign.target_company_size or "",
        "target_role": campaign.target_role or "",
        "target_industry": campaign.target_industry or "",
        "target_country": campaign.target_country or "",
        "target_language": campaign.target_language or "",
        "preferred_channel_hint": prio,
        "allowed_channels_csv": ",".join(ch),
        "calendar_link": campaign.calendar_link or "",
        "icp_ai_digest": digest,
        "sender_name": (getattr(campaign, "sender_name", None) or "").strip(),
        "sender_email": (getattr(campaign, "sender_email", None) or "").strip(),
    }


def _product_dict(campaign: Campaign) -> dict[str, str]:
    p = campaign.product
    return {
        "name": p.name if p else "Nexus Sales",
        "value_proposition": p.value_proposition if p and p.value_proposition else "",
        "description": p.description if p and p.description else "",
    }


def _prospect_dict(prospect: Prospect) -> dict[str, str]:
    return {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
    }


def run_due_followups_for_campaign_real(
    db: Session,
    campaign_id: int,
    *,
    education: str,
) -> dict[str, int]:
    """Follow-ups vencidos en NEXUS_REAL_MODE vía Gmail (borrador o envío)."""
    from app.services import real_followup_gmail as rfg

    now = _now()
    tasks = db.scalars(
        select(OutreachTask)
        .where(
            OutreachTask.campaign_id == campaign_id,
            OutreachTask.task_kind == "scheduled_followup",
            OutreachTask.status == "pending",
            OutreachTask.due_at <= now,
        )
        .order_by(OutreachTask.due_at.asc())
    ).all()

    camp = db.scalars(
        select(Campaign).where(Campaign.id == campaign_id).options(selectinload(Campaign.product))
    ).first()
    if camp is None:
        return {"processed": 0, "skipped": 0, "errors": 0}

    processed = skipped = errors = 0
    for task in tasks:
        if not task.prospect_id:
            task.status = "cancelled"
            skipped += 1
            continue
        prospect = db.get(Prospect, task.prospect_id)
        if prospect is None:
            task.status = "cancelled"
            skipped += 1
            continue
        if getattr(prospect, "sequence_group", None) == "postergado":
            task.status = "cancelled"
            skipped += 1
            continue
        if prospect.status != ProspectStatus.contacted.value:
            task.status = "cancelled"
            skipped += 1
            continue
        cap = effective_max_auto_followups(camp)
        if int(prospect.outreach_touch_count or 0) >= cap:
            task.status = "cancelled"
            skipped += 1
            continue

        history = _messages_desc(db, prospect.id)
        if history and history[-1].direction == "inbound" and history[-1].sender_type == "prospect":
            task.status = "cancelled"
            skipped += 1
            continue

        try:
            outcome = rfg.deliver_scheduled_followup_via_gmail(
                db, campaign=camp, prospect=prospect, education=education
            )
            if outcome == "skipped":
                task.status = "cancelled"
                skipped += 1
                continue
            task.status = "done"
            task.updated_at = _now()
            schedule_followup_task(
                db,
                company_id=camp.company_id,
                campaign_id=campaign_id,
                prospect_id=prospect.id,
                campaign=camp,
            )
            processed += 1
        except Exception:
            errors += 1
            continue

    return {"processed": processed, "skipped": skipped, "errors": errors}


def run_due_followups_for_campaign(
    db: Session,
    campaign_id: int,
    *,
    education: str,
) -> dict[str, int]:
    """
    Ejecuta tareas `scheduled_followup` vencidas. Pensado para llamarse desde endpoint
    manual hoy; mañana lo dispara un cron worker.
    """
    from app.services import openai_service
    from app.services import outreach_metrics as om
    from app.services import outreach_simulation as sim

    if om.is_real_mode():
        return run_due_followups_for_campaign_real(db, campaign_id, education=education)

    now = _now()
    tasks = db.scalars(
        select(OutreachTask)
        .where(
            OutreachTask.campaign_id == campaign_id,
            OutreachTask.task_kind == "scheduled_followup",
            OutreachTask.status == "pending",
            OutreachTask.due_at <= now,
        )
        .order_by(OutreachTask.due_at.asc())
    ).all()

    camp = db.scalars(
        select(Campaign).where(Campaign.id == campaign_id).options(selectinload(Campaign.product))
    ).first()
    if camp is None:
        return {"processed": 0, "skipped": 0, "errors": 0}

    processed = skipped = errors = 0
    for task in tasks:
        if not task.prospect_id:
            task.status = "cancelled"
            skipped += 1
            continue
        prospect = db.get(Prospect, task.prospect_id)
        if prospect is None:
            task.status = "cancelled"
            skipped += 1
            continue
        if getattr(prospect, "sequence_group", None) == "postergado":
            task.status = "cancelled"
            skipped += 1
            continue
        if prospect.status != ProspectStatus.contacted.value:
            task.status = "cancelled"
            skipped += 1
            continue
        cap = effective_max_auto_followups(camp)
        if int(prospect.outreach_touch_count or 0) >= cap:
            task.status = "cancelled"
            skipped += 1
            continue

        history = _messages_desc(db, prospect.id)
        if history and history[-1].direction == "inbound" and history[-1].sender_type == "prospect":
            task.status = "cancelled"
            skipped += 1
            continue

        try:
            with db.begin_nested():
                content = openai_service.generate_followup_message(
                    prospect=_prospect_dict(prospect),
                    previous_messages=_payload(history),
                    campaign=_campaign_dict(camp),
                    product=_product_dict(camp),
                    education=education,
                    objection_type=prospect.objection_type,
                    interest_level=prospect.interest_level or "low",
                    outbound_seq_index=int(prospect.outreach_touch_count or 0),
                    allow_soft_meeting_hint=False,
                )
                allowed = coerce_allowed_channels(getattr(camp, "allowed_channels", None))
                ch = sim.choose_channel(prospect, allowed)
                msg = sim.make_message(
                    prospect_id=prospect.id,
                    campaign_id=campaign_id,
                    sender_type="ai",
                    message=content,
                    channel=ch,
                    direction="outbound",
                )
                db.add(msg)
                record_ai_outbound(
                    db,
                    prospect,
                    campaign_calendar_link=camp.calendar_link,
                    outbound_text=content,
                )
                task.status = "done"
                task.updated_at = _now()
                schedule_followup_task(
                    db,
                    company_id=camp.company_id,
                    campaign_id=campaign_id,
                    prospect_id=prospect.id,
                    campaign=camp,
                )
                processed += 1
        except Exception:
            errors += 1
            continue

    return {"processed": processed, "skipped": skipped, "errors": errors}
