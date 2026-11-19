"""
Campaña «Postergados» por vendedor: quien dice «escribime en…» sale de su campaña,
espera acá y, en la fecha, recibe un re-contacto + una reactivación corta.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, ProspectStatus
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.models.user import User

logger = logging.getLogger(__name__)

POSTERGADOS_KIND = "postergados"
POSTERGADOS_NAME = "Postergados"
TASK_KIND_REACTIVATION = "reactivation_touch"
# Re-contacto = toque 1; después estos días (desde el re-contacto) para los toques 2 y 3.
REACTIVATION_TOUCH_DAYS = {2: 3, 3: 7}
LAST_REACTIVATION_STEP = max(REACTIVATION_TOUCH_DAYS)

_CLOSED = frozenset(
    {
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
        ProspectStatus.failed.value,
    }
)
_COPY_FIELDS = (
    "calendar_link",
    "timezone",
    "available_hours",
    "tone",
    "allowed_channels",
    "sender_name",
    "sender_email",
    "ai_context",
    "outreach_mode",
    "target_language",
    "outreach_email_mode",
    "inbound_reply_mode",
    "inbound_reply_delay_minutes",
)


def is_postergados_campaign(campaign: Any) -> bool:
    return (getattr(campaign, "kind", None) or "") == POSTERGADOS_KIND


def is_system_container_campaign(campaign: Any) -> bool:
    """Contenedores que no buscan prospectos ni se «terminan»: base, individuales y postergados."""
    from app.services.manual_sequence_kickoff import is_individual_container_campaign
    from app.services.prospect_base_campaign import is_prospect_base_campaign

    return (
        is_postergados_campaign(campaign)
        or is_individual_container_campaign(campaign)
        or is_prospect_base_campaign(campaign)
    )


def _campaign_name(db: Session, *, seller: User | None, product_name: str | None, multi_product: bool) -> str:
    first = ((getattr(seller, "first_name", None) or getattr(seller, "name", None) or "").strip().split() or [""])[0]
    name = f"{POSTERGADOS_NAME} — {first}" if first else POSTERGADOS_NAME
    if multi_product and product_name:
        name = f"{name} · {product_name}"
    return name[:255]


def get_or_create_postergados_campaign(db: Session, *, origin: Campaign, seller_id: int) -> Campaign:
    """Una por vendedor (y por producto si vende más de uno), con la configuración de envío de origen."""
    from app.services.real_initial_outreach import ensure_outreach_sequence_running

    existing = db.scalars(
        select(Campaign).where(
            Campaign.company_id == origin.company_id,
            Campaign.kind == POSTERGADOS_KIND,
            Campaign.seller_id == seller_id,
        )
    ).all()
    match = next((c for c in existing if int(c.product_id) == int(origin.product_id)), None)
    if match is not None:
        for field in ("calendar_link", "sender_name", "sender_email", "ai_context"):
            if not getattr(match, field, None) and getattr(origin, field, None):
                setattr(match, field, getattr(origin, field))
        # Si el SDR la pausó a mano, se respeta.
        if not match.automation_paused and match.status != CampaignStatus.running.value:
            match.status = CampaignStatus.running.value
            ensure_outreach_sequence_running(db, match, force=True)
        return match

    seller = db.get(User, int(seller_id))
    product = getattr(origin, "product", None)
    row = Campaign(
        company_id=origin.company_id,
        seller_id=int(seller_id),
        product_id=origin.product_id,
        name=_campaign_name(
            db,
            seller=seller,
            product_name=getattr(product, "name", None),
            multi_product=bool(existing),
        ),
        kind=POSTERGADOS_KIND,
        status=CampaignStatus.running.value,
        automation_paused=False,
        prospect_count=0,
        estimated_meetings_min=0,
        estimated_meetings_max=0,
        estimated_cost_min=0,
        estimated_cost_max=0,
        estimated_avg_cost_per_meeting=0.0,
        post_sequence_followup_enabled=False,
        target_country=origin.target_country,
        target_company_hq=getattr(origin, "target_company_hq", None),
    )
    for field in _COPY_FIELDS:
        value = getattr(origin, field, None)
        if value is not None:
            setattr(row, field, value)
    row.calendar_link = row.calendar_link or ""
    row.timezone = row.timezone or "America/Argentina/Buenos_Aires"
    row.available_hours = row.available_hours or "Lun-Vie 09:00-18:00"
    row.tone = row.tone or "profesional y directo"
    if not row.allowed_channels:
        row.allowed_channels = ["email", "linkedin", "whatsapp"]
    db.add(row)
    db.flush()
    ensure_outreach_sequence_running(db, row, force=True)
    return row


def move_to_postergados(db: Session, prospect: Prospect, origin: Campaign) -> Campaign:
    """Pasa al prospecto a la campaña Postergados de su vendedor. No consume créditos."""
    from app.services import multichannel_sequence as mseq

    if is_postergados_campaign(origin):
        return origin
    seller_id = int(prospect.owner_user_id or origin.seller_id)
    target = get_or_create_postergados_campaign(db, origin=origin, seller_id=seller_id)
    prospect.origin_campaign_id = origin.id
    prospect.campaign_id = target.id
    if getattr(prospect, "campaign", None) is not None:
        prospect.campaign = target
    db.execute(
        update(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect.id,
            OutreachTask.status == "pending",
            OutreachTask.task_kind != "inbound_auto_reply",
        )
        .values(campaign_id=target.id)
    )
    name = prospect.name or prospect.email or f"#{prospect.id}"
    mseq._append_log(origin, f"{name} pidió que le escriban más adelante — pasó a «{target.name}».", kind="defer")
    mseq._append_log(target, f"{name} llegó desde «{origin.name}».", kind="defer")
    db.flush()
    return target


def _snap_for(prospect: Prospect, campaign: Campaign, when: datetime) -> datetime:
    from app.services.prospect_timezone import prospect_timezone
    from app.services.send_window import snap_to_send_window

    return snap_to_send_window(when, timezone=prospect_timezone(prospect, campaign))


def schedule_reactivation_touch(
    db: Session, *, campaign: Campaign, prospect: Prospect, step: int, after: datetime | None = None
) -> OutreachTask | None:
    days = REACTIVATION_TOUCH_DAYS.get(step)
    if days is None:
        return None
    base = after or datetime.now(UTC)
    task = OutreachTask(
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        task_kind=TASK_KIND_REACTIVATION,
        title=f"Reactivación {step}/{LAST_REACTIVATION_STEP}",
        notes=json.dumps({"step": step}),
        due_at=_snap_for(prospect, campaign, base + timedelta(days=days)),
        status="pending",
    )
    db.add(task)
    return task


def cancel_reactivation_tasks(db: Session, prospect_id: int) -> int:
    result = db.execute(
        update(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.task_kind == TASK_KIND_REACTIVATION,
            OutreachTask.status == "pending",
        )
        .values(status="cancelled")
    )
    return int(result.rowcount or 0)


def _step_of(task: OutreachTask) -> int:
    try:
        return int(json.loads(task.notes or "{}").get("step") or 0)
    except (TypeError, ValueError):
        return 0


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _replied_since(prospect: Prospect, since: datetime | None) -> bool:
    last_in = _aware(getattr(prospect, "last_inbound_at", None))
    since = _aware(since)
    return bool(last_in and since and last_in >= since)


def _compose_touch(db: Session, *, campaign: Campaign, prospect: Prospect, step: int) -> str:
    from app.services import followup_engine, openai_service
    from app.services.ai_instruction_context import campaign_education_blob

    try:
        history = followup_engine._messages_desc(db, prospect.id)
        body = openai_service.generate_followup_message(
            prospect=followup_engine._prospect_dict(prospect),
            previous_messages=followup_engine._payload(history),
            campaign=followup_engine._campaign_dict(campaign),
            product=followup_engine._product_dict(campaign),
            education=campaign_education_blob(db, campaign),
            objection_type=prospect.objection_type,
            interest_level=prospect.interest_level or "low",
            outbound_seq_index=int(prospect.outreach_touch_count or 0),
            allow_soft_meeting_hint=True,
            is_final_goodbye=step >= LAST_REACTIVATION_STEP,
        )
        if (body or "").strip():
            return body.strip()
    except Exception:
        logger.warning("reactivation compose fallback prospect_id=%s", prospect.id, exc_info=True)
    first = (prospect.name or "").strip().split(" ")[0]
    hola = f"Hola {first}" if first else "Hola"
    if step >= LAST_REACTIVATION_STEP:
        return (
            f"{hola}, te escribo por última vez por este tema para no insistir de más. "
            "Si en algún momento querés retomarlo, respondé este mensaje y lo coordinamos."
        )
    return f"{hola}, ¿pudiste ver mi mensaje anterior? Si te sirve, coordinamos una llamada corta esta semana."


def _deliver(db: Session, *, campaign: Campaign, prospect: Prospect, body: str) -> str:
    from app.services.deferred_recontact import recontact_channel

    channel = recontact_channel(db, prospect)
    if channel == "email":
        from app.services.real_followup_gmail import deliver_email_via_gmail

        company = (prospect.company_name or "").strip() or "tu equipo"
        try:
            return deliver_email_via_gmail(
                db,
                campaign=campaign,
                prospect=prospect,
                subject=f"Retomando — {company}",
                body=body,
                draft_label="reactivación postergado",
            )
        except Exception:
            logger.exception("reactivation email failed prospect_id=%s", prospect.id)
            return "skipped"
    if channel == "whatsapp":
        from app.services import whatsapp_assisted_service as was

        was.mark_draft_suggested(db, prospect, campaign, body)
        return "queued"
    if channel == "linkedin":
        from app.services import linkedin_assisted_service as las

        las.mark_draft_suggested(db, prospect, campaign, body)
        return "queued"
    return "skipped"


def process_due_reactivation_tasks(db: Session, campaign: Campaign) -> int:
    """Toques 2 y 3 de la reactivación. Se cortan solos si el prospecto respondió."""
    from app.services import multichannel_sequence as mseq
    from app.services.deferred_recontact import recontact_channel
    from app.services.prospect_timezone import prospect_timezone
    from app.services.send_window import is_within_send_window

    now = datetime.now(UTC)
    tasks = db.scalars(
        select(OutreachTask).where(
            OutreachTask.campaign_id == campaign.id,
            OutreachTask.task_kind == TASK_KIND_REACTIVATION,
            OutreachTask.status == "pending",
            OutreachTask.due_at <= now,
        )
    ).all()
    sent = 0
    for task in tasks:
        prospect = db.get(Prospect, task.prospect_id) if task.prospect_id else None
        if (
            prospect is None
            or prospect.campaign_id != campaign.id
            or (prospect.status or "") in _CLOSED
            or getattr(prospect, "sequence_group", None)
            in (mseq.SEQUENCE_GROUP_POSTERGADO, mseq.SEQUENCE_GROUP_ENCAJONADO, mseq.SEQUENCE_GROUP_REUNIONES)
            or _replied_since(prospect, task.created_at)
        ):
            task.status = "cancelled"
            continue
        if recontact_channel(db, prospect) == "email" and not is_within_send_window(
            now, timezone=prospect_timezone(prospect, campaign)
        ):
            continue
        step = _step_of(task)
        body = _compose_touch(db, campaign=campaign, prospect=prospect, step=step)
        outcome = _deliver(db, campaign=campaign, prospect=prospect, body=body)
        task.updated_at = now
        if outcome == "skipped":
            task.status = "cancelled"
            continue
        task.status = "done"
        sent += 1
        if step < LAST_REACTIVATION_STEP:
            nxt = step + 1
            schedule_reactivation_touch(
                db,
                campaign=campaign,
                prospect=prospect,
                step=nxt,
                after=now - timedelta(days=REACTIVATION_TOUCH_DAYS[step]),
            )
        mseq._append_log(
            campaign,
            f"Reactivación {step}/{LAST_REACTIVATION_STEP} · {prospect.name}: "
            + {"sent": "enviado por mail", "draft": "borrador en Gmail", "queued": "mensaje en la bandeja"}.get(
                outcome, outcome
            ),
            kind="reactivation",
        )
    return sent
