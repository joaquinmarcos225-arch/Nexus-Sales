"""
Motor de secuencia multicanal — simulación demo + grupos operativos.

Los toques siguen `app/core/sequence_playbook.py` (7 toques + reactivación día 42).
En `NEXUS_REAL_MODE=1`, `process_due_milestones` no corre; la ejecución real es vía playbook SDR.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import MeetingStatus, ProspectStatus
from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.services import followup_engine, openai_service, outreach_simulation as sim
from app.services import outreach_metrics as om
from app.services import pipeline_sync
from app.schemas.campaign_channels import coerce_allowed_channels

from app.core.sequence_playbook import (
    COOLDOWN_START_DAY,
    PLAYBOOK_DAYS,
    PLAYBOOK_LAST_TOUCH_DAY,
    PLAYBOOK_LINKEDIN_DAYS,
    REACTIVATION_DAY,
    normalize_fired_milestones,
    normalize_milestone_day,
    resolve_touch_channel,
)

SEQUENCE_GROUP_CONTACTADO = "contactado"
SEQUENCE_GROUP_PROXIMO_FU = "proximo_follow_up"
SEQUENCE_GROUP_FOLLOW_UPS = "follow_ups"
SEQUENCE_GROUP_DESCANSO = "descanso"
SEQUENCE_GROUP_ENCAJONADO = "encajonado"
SEQUENCE_GROUP_POSTERGADO = "postergado"
SEQUENCE_GROUP_REUNIONES = "reuniones"

STATE_SIN = "sin_respuesta"
STATE_CON = "con_respuesta"
STATE_LINK = "link_enviado"
STATE_AGENDADO = "agendado"


def _fired_list(p: Prospect) -> list[int]:
    raw = getattr(p, "sequence_fired_milestones", None) or "[]"
    if isinstance(raw, list):
        parsed = [int(x) for x in raw if str(x).isdigit()]
    else:
        try:
            parsed = [int(x) for x in json.loads(str(raw)) if str(x).isdigit()]
        except Exception:
            parsed = []
    return normalize_fired_milestones(parsed)


def _set_fired(p: Prospect, days: list[int]) -> None:
    p.sequence_fired_milestones = json.dumps(sorted(set(days)))


def _append_log(campaign: Campaign, line: str, *, kind: str = "info") -> None:
    log = getattr(campaign, "outreach_activity_log", None)
    if not isinstance(log, list):
        log = []
    entry = {
        "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "kind": kind,
        "message": line,
    }
    log = [*log[-199:], entry]
    campaign.outreach_activity_log = log


def _campaign_payload(campaign: Campaign) -> dict[str, str]:
    from app.services.campaign_market import normalize_outreach_mode
    from app.services.campaign_outreach_context import company_brand_name

    ch = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    brand = company_brand_name(campaign)
    return {
        "id": str(getattr(campaign, "id", "") or ""),
        "name": campaign.name,
        "tone": campaign.tone,
        "outreach_mode": normalize_outreach_mode(getattr(campaign, "outreach_mode", None)),
        "target_role": campaign.target_role or "",
        "target_industry": campaign.target_industry or "",
        "target_country": campaign.target_country or "",
        "target_interests": getattr(campaign, "target_interests", None) or "",
        "preferred_channel_hint": " → ".join(ch),
        "allowed_channels_csv": ",".join(ch),
        "calendar_link": campaign.calendar_link or "",
        "brand_name": brand,
        "company_name": brand,
        "seller_company_name": brand,
    }


def _product_payload(campaign: Campaign) -> dict[str, str]:
    p = campaign.product
    return {
        "name": (p.name if p else "") or "",
        "value_proposition": p.value_proposition if p and p.value_proposition else "",
        "description": p.description if p and p.description else "",
    }


def _prospect_payload(prospect: Prospect) -> dict[str, str]:
    return {
        "id": str(getattr(prospect, "id", "") or ""),
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
    }


def _day_index_one_based(started_at: datetime | None, now: datetime | None = None) -> int:
    if started_at is None:
        return 0
    now = now or datetime.now(UTC)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    delta = (now.date() - started_at.date()).days
    return max(1, delta + 1)


def _channel_day1(allowed: list[str]) -> str:
    if "email" in allowed:
        return "email"
    return allowed[0] if allowed else "email"


def _channel_day4(prospect: Prospect, allowed: list[str]) -> str:
    if prospect.linkedin_url and "linkedin" in allowed:
        return "linkedin"
    if prospect.phone and "whatsapp" in allowed:
        return "whatsapp"
    if "email" in allowed:
        return "email"
    return allowed[0] if allowed else "email"


def _channel_whatsapp_or_mail(prospect: Prospect, allowed: list[str]) -> str:
    if prospect.phone and "whatsapp" in allowed:
        return "whatsapp"
    if "email" in allowed:
        return "email"
    return allowed[0] if allowed else "email"


def _touch_body(
    *,
    day: int,
    channel: str,
    prospect: Prospect,
    campaign: Campaign,
    education: str,
) -> str:
    return openai_service.generate_sequence_touch_message(
        day=day,
        channel=channel,
        prospect=_prospect_payload(prospect),
        campaign=_campaign_payload(campaign),
        product=_product_payload(campaign),
        tone=campaign.tone,
        education_blob=education,
    )


def _update_group_for_prospect(p: Prospect, day: int, has_pending_fu: bool) -> None:
    if getattr(p, "sequence_group", None) == SEQUENCE_GROUP_REUNIONES:
        return
    if getattr(p, "sequence_group", None) == SEQUENCE_GROUP_ENCAJONADO:
        return
    if getattr(p, "sequence_group", None) == SEQUENCE_GROUP_POSTERGADO:
        return
    if getattr(p, "sequence_paused", False):
        p.sequence_group = SEQUENCE_GROUP_CONTACTADO
        return
    if day >= COOLDOWN_START_DAY and day < REACTIVATION_DAY:
        p.sequence_group = SEQUENCE_GROUP_DESCANSO
        return
    if has_pending_fu:
        p.sequence_group = SEQUENCE_GROUP_PROXIMO_FU
    elif int(getattr(p, "followup_count", 0) or 0) > 0:
        p.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
    else:
        p.sequence_group = SEQUENCE_GROUP_CONTACTADO


def bootstrap_on_start(
    db: Session,
    campaign: Campaign,
    *,
    channels_allowed: list[str],
    education_blob: str,
) -> dict[str, Any]:
    """Al iniciar outreach: ancla secuencia y dispara día 1 (email) donde corresponda."""
    from app.services.campaign_activation import seller_has_gmail
    from app.services.real_initial_outreach import process_campaign_initial_outreach

    use_gmail = bool(campaign.seller_id) and seller_has_gmail(
        db, int(campaign.company_id), int(campaign.seller_id)
    )
    if use_gmail or om.is_real_mode():
        if not use_gmail and om.is_real_mode():
            return {
                "day1_sent": 0,
                "drafts": 0,
                "sent": 0,
                "skipped": 0,
                "errors": 1,
                "error_messages": [
                    "Conectá Gmail del vendedor asignado para enviar el primer contacto real."
                ],
                "used_gmail": False,
            }
        if use_gmail:
            batch = int(os.getenv("NEXUS_INITIAL_OUTREACH_BATCH_SIZE", "500"))
            batch = max(1, min(batch, 500))
            out = process_campaign_initial_outreach(
                db, campaign, education_blob, max_batch=batch
            )
            out["used_gmail"] = True
            return out
    now = datetime.now(UTC)

    from app.core import sequence_templates as _seqt

    _seq_plan = getattr(campaign, "sequence_plan", None)
    _use_ia = _seqt.plan_is_ia(_seq_plan)
    _channel_plan_day1 = _seqt.plan_channel_map(_seq_plan)

    def _day1_channel(prospect: Prospect) -> str | None:
        kw = dict(
            email=prospect.email,
            linkedin_url=prospect.linkedin_url,
            phone=prospect.phone,
            whatsapp_number=getattr(prospect, "whatsapp_number", None),
            allowed_channels=channels_allowed,
        )
        if _use_ia:
            if not _seqt.prospect_has_min_channels(**kw):
                return None
            return _seqt.resolve_ia_touch_channel(1, prior_channels=[], **kw)
        if _channel_plan_day1:
            from app.core.sequence_playbook import resolve_touch_channel as _rtc

            return _rtc(1, channel_plan=_channel_plan_day1, **kw)
        return _channel_day1(channels_allowed)

    eligible = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign.id,
            Prospect.status.in_(
                [
                    ProspectStatus.compatible.value,
                    ProspectStatus.imported.value,
                    ProspectStatus.contacted.value,
                ]
            ),
        )
    ).all()

    day1 = 0
    for prospect in eligible:
        if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_ENCAJONADO:
            continue
        if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_POSTERGADO:
            continue
        if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_REUNIONES:
            continue
        history = db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect.id)
            .order_by(OutreachMessage.created_at.asc())
        ).all()
        if prospect.sequence_started_at is None:
            prospect.sequence_started_at = now
            prospect.sequence_group = SEQUENCE_GROUP_CONTACTADO
            prospect.sequence_state = getattr(prospect, "sequence_state", None) or STATE_SIN
            prospect.sequence_paused = False
            _set_fired(prospect, [])

        fired = _fired_list(prospect)
        if 1 in fired:
            continue
        if history:
            _set_fired(prospect, fired + [1])
            continue

        ch = _day1_channel(prospect)
        if not ch:
            continue
        from app.services import daily_send_limits as _dsl

        _kind = {"email": _dsl.KIND_EMAIL, "whatsapp": _dsl.KIND_WHATSAPP}.get(ch)
        if _kind and not _dsl.can_send(db, int(campaign.seller_id or 0), _kind):
            continue
        if ch == "whatsapp" and not _dsl.whatsapp_qualified(db, prospect):
            continue
        body = _touch_body(day=1, channel=ch, prospect=prospect, campaign=campaign, education=education_blob)
        db.add(
            sim.make_message(
                prospect_id=prospect.id,
                campaign_id=campaign.id,
                sender_type="ai",
                message=body,
                channel=ch,
                direction="outbound",
            )
        )
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=body,
        )
        if (campaign.calendar_link or "") in body:
            prospect.sequence_state = STATE_LINK
        prospect.status = ProspectStatus.contacted.value
        pipeline_sync.sync_pipeline_from_status(prospect)
        _set_fired(prospect, fired + [1])
        day1 += 1

    if day1:
        _append_log(campaign, f"Nexus inició secuencia ({len(PLAYBOOK_DAYS)} toques): {day1} emails / toques día 1.", kind="sequence")
    return {"day1_sent": day1, "simulated": True, "used_gmail": False}


def process_due_milestones(
    db: Session,
    campaign: Campaign,
    *,
    channels_allowed: list[str],
    education_blob: str,
) -> dict[str, Any]:
    """Ejecuta hitos 4–21 y 42 según calendario; respeta pausa por respuesta."""
    if om.is_real_mode():
        return {"touches": 0, "linkedin_drafts": 0, "tasks": 0, "reactivations": 0, "skipped_real_mode": True}
    now = datetime.now(UTC)
    stats = {"touches": 0, "linkedin_drafts": 0, "tasks": 0, "reactivations": 0}

    from app.core import sequence_templates as _seqt

    _seq_plan = getattr(campaign, "sequence_plan", None)
    _use_ia = _seqt.plan_is_ia(_seq_plan)
    channel_plan = _seqt.plan_channel_map(_seq_plan)  # None en modo IA
    if channel_plan is not None:
        _fu = _seqt.followup_channel(_seq_plan)
        if _fu != "auto":
            channel_plan = {**channel_plan, REACTIVATION_DAY: _fu}

    def _prior_channels(prospect_id: int) -> list[str]:
        rows = db.scalars(
            select(OutreachMessage.channel)
            .where(
                OutreachMessage.prospect_id == prospect_id,
                OutreachMessage.direction == "outbound",
            )
            .order_by(OutreachMessage.created_at.asc())
        ).all()
        return [str(r).lower() for r in rows if r]

    def _channel_for_milestone(m: int, prospect: Prospect) -> str | None:
        kw = dict(
            email=prospect.email,
            linkedin_url=prospect.linkedin_url,
            phone=prospect.phone,
            whatsapp_number=getattr(prospect, "whatsapp_number", None),
            allowed_channels=channels_allowed,
        )
        if _use_ia:
            return _seqt.resolve_ia_touch_channel(
                m, prior_channels=_prior_channels(prospect.id), **kw
            )
        return resolve_touch_channel(m, channel_plan=channel_plan, **kw)

    prospects = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign.id,
            Prospect.status.not_in([ProspectStatus.not_compatible.value]),
        )
    ).all()

    for prospect in prospects:
        if prospect.sequence_started_at is None:
            continue
        if getattr(prospect, "sequence_paused", False):
            continue
        if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_ENCAJONADO:
            continue
        if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_POSTERGADO:
            continue
        if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_REUNIONES:
            continue

        if _use_ia and not _seqt.prospect_has_min_channels(
            email=prospect.email,
            linkedin_url=prospect.linkedin_url,
            phone=prospect.phone,
            whatsapp_number=getattr(prospect, "whatsapp_number", None),
            allowed_channels=channels_allowed,
        ):
            continue

        day = _day_index_one_based(prospect.sequence_started_at, now)
        fired = _fired_list(prospect)

        pending_fu = bool(
            db.scalars(
                select(OutreachTask).where(
                    OutreachTask.prospect_id == prospect.id,
                    OutreachTask.status == "pending",
                    OutreachTask.task_kind == "scheduled_followup",
                )
            ).first()
        )
        _update_group_for_prospect(prospect, day, pending_fu)

        milestones: list[int] = []
        touch_days = _seqt.plan_touch_days(_seq_plan)
        last_touch = _seqt.plan_last_touch_day(_seq_plan)
        for d in touch_days:
            if d > 1 and d not in fired and day >= d:
                milestones.append(d)
                break
        if (
            not milestones
            and REACTIVATION_DAY not in fired
            and day >= REACTIVATION_DAY
            and last_touch in fired
        ):
            milestones.append(REACTIVATION_DAY)

        if day >= (last_touch + 1) and day < REACTIVATION_DAY and last_touch in fired:
            if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_REUNIONES:
                prospect.sequence_group = SEQUENCE_GROUP_DESCANSO

        for m in milestones:
            if m == REACTIVATION_DAY:
                if prospect.last_inbound_at and prospect.sequence_started_at:
                    if prospect.last_inbound_at > prospect.sequence_started_at:
                        _set_fired(prospect, fired + [m])
                        continue
                body = openai_service.generate_reactivation_ping(
                    prospect=_prospect_payload(prospect),
                    campaign=_campaign_payload(campaign),
                    product=_product_payload(campaign),
                    tone=campaign.tone,
                    education_blob=education_blob,
                )
                ch = _channel_for_milestone(REACTIVATION_DAY, prospect)
                if not ch:
                    continue
                db.add(
                    sim.make_message(
                        prospect_id=prospect.id,
                        campaign_id=campaign.id,
                        sender_type="ai",
                        message=body,
                        channel=ch,
                        direction="outbound",
                    )
                )
                followup_engine.record_ai_outbound(
                    db,
                    prospect,
                    campaign_calendar_link=campaign.calendar_link or "",
                    outbound_text=body,
                )
                prospect.reactivation_sent_at = now
                _set_fired(prospect, _fired_list(prospect) + [m])
                prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
                stats["reactivations"] += 1
                stats["touches"] += 1
                continue

            ch = _channel_for_milestone(m, prospect)
            if not ch:
                continue

            if ch in ("email", "whatsapp"):
                from app.services import daily_send_limits as _dsl

                _kind = _dsl.KIND_EMAIL if ch == "email" else _dsl.KIND_WHATSAPP
                if not _dsl.can_send(db, int(campaign.seller_id or 0), _kind):
                    continue
                if ch == "whatsapp" and not _dsl.whatsapp_qualified(db, prospect):
                    continue

            body = _touch_body(day=m, channel=ch, prospect=prospect, campaign=campaign, education=education_blob)

            if m in PLAYBOOK_LINKEDIN_DAYS and ch == "linkedin" and prospect.linkedin_url:
                from app.services.linkedin_assisted_service import is_real_linkedin_profile_url

                if is_real_linkedin_profile_url(prospect.linkedin_url):
                    from app.services.linkedin_assisted_service import (
                        queue_linkedin_sequence_touch,
                    )

                    li_action = queue_linkedin_sequence_touch(
                        db, prospect, campaign, body, log_event=True
                    )
                    if li_action == "hold":
                        # Esperando aceptación de la conexión: se difiere.
                        continue
                    if li_action == "skip":
                        # Sin conexión: se omite el toque LinkedIn (sin InMail).
                        _append_fired(prospect, m)
                        stats["touches"] += 1
                        continue
                    stats["linkedin_drafts"] += 1
                    if li_action == "connect":
                        inner = (
                            f"[Nexus — día {m}] Enviá la solicitud de conexión en LinkedIn desde "
                            "Notificaciones. Al aceptarte, se prepara el mensaje."
                        )
                    else:
                        inner = (
                            f"[Nexus — día {m}] Mensaje listo para enviar por LinkedIn al abrir el perfil. "
                            "Revisá el borrador en Notificaciones."
                        )
                else:
                    inner = (
                        f"[Nexus — día {m}] Sin LinkedIn real en este prospecto (URL demo). "
                        "No se creó borrador asistido."
                    )
                db.add(
                    sim.make_message(
                        prospect_id=prospect.id,
                        campaign_id=campaign.id,
                        sender_type="system",
                        message=inner,
                        channel="linkedin",
                        direction="outbound",
                    )
                )
            else:
                db.add(
                    sim.make_message(
                        prospect_id=prospect.id,
                        campaign_id=campaign.id,
                        sender_type="ai",
                        message=body,
                        channel=ch,
                        direction="outbound",
                    )
                )
                followup_engine.record_ai_outbound(
                    db,
                    prospect,
                    campaign_calendar_link=campaign.calendar_link or "",
                    outbound_text=body,
                )

            if m == 13:
                db.add(
                    OutreachTask(
                        company_id=campaign.company_id,
                        campaign_id=campaign.id,
                        prospect_id=prospect.id,
                        task_kind="sequence_sdr_hint",
                        title=f"Día {m}: interacción LinkedIn (post o reacción)",
                        notes="Nexus sugiere una interacción ligera en LinkedIn hoy; no envía DM automático.",
                        due_at=now + timedelta(hours=4),
                        status="pending",
                    )
                )
                stats["tasks"] += 1

            if (campaign.calendar_link or "") in body:
                prospect.sequence_state = STATE_LINK

            if m == PLAYBOOK_LAST_TOUCH_DAY:
                followup_engine.schedule_followup_task(
                    db,
                    company_id=campaign.company_id,
                    campaign_id=campaign.id,
                    prospect_id=prospect.id,
                    title=f"Follow-up post-secuencia (día {PLAYBOOK_LAST_TOUCH_DAY})",
                    campaign=campaign,
                )

            fired = _fired_list(prospect)
            _set_fired(prospect, fired + [m])
            stats["touches"] += 1

            if m == PLAYBOOK_LAST_TOUCH_DAY:
                prospect.sequence_group = SEQUENCE_GROUP_DESCANSO

    if stats["touches"] or stats["linkedin_drafts"] or stats["reactivations"]:
        parts = []
        if stats["touches"]:
            parts.append(f"{stats['touches']} toques de secuencia")
        if stats["linkedin_drafts"]:
            parts.append(f"{stats['linkedin_drafts']} borradores LinkedIn")
        if stats["reactivations"]:
            parts.append(f"{stats['reactivations']} reactivaciones día 42")
        _append_log(campaign, "Nexus · " + " · ".join(parts) + ".", kind="sequence")

    return stats


def on_inbound_pause_sequence(db: Session, prospect: Prospect) -> None:
    """Si el prospecto responde, la secuencia automática de hitos se pausa; la IA conversa."""
    prospect.sequence_paused = True
    if getattr(prospect, "sequence_state", None) != STATE_AGENDADO:
        prospect.sequence_state = STATE_CON


def promote_operational_group_after_prospect_reply(prospect: Prospect) -> None:
    """
    Regla operativa: Contactados = sin réplica. Cualquier inbound real/simulado que no va a
    postergado ni encajonado debe salir de `contactado` / `proximo_follow_up` / `descanso` → follow_ups.
    """
    if getattr(prospect, "status", None) == ProspectStatus.meeting_booked.value:
        return
    g = getattr(prospect, "sequence_group", None)
    if g == SEQUENCE_GROUP_ENCAJONADO:
        return
    if g == SEQUENCE_GROUP_FOLLOW_UPS:
        return
    if g in (SEQUENCE_GROUP_CONTACTADO, SEQUENCE_GROUP_PROXIMO_FU, SEQUENCE_GROUP_DESCANSO):
        prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS


def prospect_has_confirmed_future_meeting(db: Session, prospect: Prospect) -> bool:
    """Reunión futura no cancelada en Nexus (Calendar sync o manual)."""
    now = datetime.now(UTC)
    row = db.scalars(
        select(Meeting)
        .where(
            Meeting.prospect_id == prospect.id,
            Meeting.scheduled_for >= now,
            Meeting.meeting_status != MeetingStatus.canceled.value,
        )
        .order_by(Meeting.scheduled_for.asc())
        .limit(1)
    ).first()
    return row is not None


def prospect_in_meeting_priority(db: Session, prospect: Prospect) -> bool:
    """Reunión agendada tiene prioridad sobre postergado / follow-up / contactado."""
    if getattr(prospect, "status", None) == ProspectStatus.meeting_booked.value:
        return True
    if getattr(prospect, "sequence_group", None) == SEQUENCE_GROUP_REUNIONES:
        return True
    return prospect_has_confirmed_future_meeting(db, prospect)


def enforce_meeting_priority_over_sequence(
    db: Session, prospect: Prospect, campaign: Campaign
) -> bool:
    """
    Si hay reunión confirmada: Reuniones, pausa secuencia, sin postergado ni follow-ups.
    Devuelve True si aplicó (el caller debe omitir postergación).
    """
    if not prospect_in_meeting_priority(db, prospect):
        return False
    if getattr(prospect, "status", None) != ProspectStatus.meeting_booked.value:
        prospect.status = ProspectStatus.meeting_booked.value
    apply_real_calendar_booking(db, prospect, campaign)
    pipeline_sync.sync_pipeline_from_status(prospect)
    from app.services.meeting_booking import prospect_has_calendar_confirmed_meeting
    from app.services import prospect_commercial_state as pcs

    if prospect_has_calendar_confirmed_meeting(db, prospect):
        prospect.commercial_state = pcs.COMMERCIAL_REUNION_AGENDADA
        prospect.commercial_state_is_testing = False
    return True


def reconcile_meeting_vs_postergado_for_campaign(db: Session, campaign: Campaign) -> int:
    """Alias: reconciliación completa de grupos operativos vs reuniones futuras."""
    return reconcile_meeting_operational_groups_for_campaign(db, campaign)


def reconcile_meeting_operational_groups_for_campaign(db: Session, campaign: Campaign) -> int:
    """
    Cualquier prospecto con reunión futura confirmada en Nexus debe estar en Reuniones
    (no Postergados, Follow-up ni secuencia activa).
    """
    now = datetime.now(UTC)
    prospect_ids = {
        int(pid)
        for pid in db.scalars(
            select(Meeting.prospect_id)
            .where(
                Meeting.campaign_id == campaign.id,
                Meeting.prospect_id.isnot(None),
                Meeting.scheduled_for >= now,
                Meeting.meeting_status != MeetingStatus.canceled.value,
            )
            .distinct()
        ).all()
        if pid is not None
    }
    fixed = 0
    seen: set[int] = set()
    for pid in prospect_ids:
        prospect = db.get(Prospect, pid)
        if prospect is None or prospect.id in seen:
            continue
        seen.add(prospect.id)
        if enforce_meeting_priority_over_sequence(db, prospect, campaign):
            fixed += 1
    postergados = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign.id,
            Prospect.sequence_group == SEQUENCE_GROUP_POSTERGADO,
        )
    ).all()
    for prospect in postergados:
        if prospect.id in seen:
            continue
        if enforce_meeting_priority_over_sequence(db, prospect, campaign):
            fixed += 1
            seen.add(prospect.id)
    return fixed


def apply_prospect_timing_deferral(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    *,
    defer_resume_at: datetime,
    inbound_snippet: str,
) -> None:
    """Postergados: pausa secuencia y agenda re-contacto automático."""
    if prospect_in_meeting_priority(db, prospect):
        enforce_meeting_priority_over_sequence(db, prospect, campaign)
        return
    followup_engine.cancel_pending_followup_tasks(db, prospect.id)
    followup_engine.cancel_deferred_resume_tasks(db, prospect.id)
    d = defer_resume_at
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    d = d.astimezone(UTC)
    prospect.defer_resume_at = d
    prospect.sequence_group = SEQUENCE_GROUP_POSTERGADO
    prospect.sequence_paused = True
    if getattr(prospect, "sequence_state", None) != STATE_AGENDADO:
        prospect.sequence_state = STATE_CON
    db.add(
        OutreachTask(
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            task_kind="deferred_sequence_resume",
            title="Reanudar postergado (automático)",
            notes=f"Snippet inbound: {(inbound_snippet or '')[:480]}",
            due_at=d,
            status="pending",
        )
    )
    _append_log(
        campaign,
        f"{prospect.name} quedó en Postergados — re-contacto automático programado.",
        kind="defer",
    )


def clear_postergado_state(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    *,
    reason: str = "Inbound reclasificado (sin postergación)",
) -> None:
    """
    Sale de Postergados cuando el mensaje más reciente ya no debe congelar la secuencia
    (p. ej. quiere agendar ahora, o cualquier inbound que no dispara timing blando).
    Cancela el re-contacto automático pendiente.
    """
    if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_POSTERGADO:
        return
    followup_engine.cancel_deferred_resume_tasks(db, prospect.id)
    prospect.defer_resume_at = None
    prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
    prospect.sequence_paused = False
    _append_log(
        campaign,
        f"{prospect.name} salió de Postergados — {reason}.",
        kind="reclassify",
    )


def process_due_deferred_resume_tasks(db: Session, campaign: Campaign) -> int:
    """Marca cumplidas las reanudaciones por postergación y destraba la secuencia."""
    now = datetime.now(UTC)
    tasks = db.scalars(
        select(OutreachTask)
        .where(
            OutreachTask.campaign_id == campaign.id,
            OutreachTask.task_kind == "deferred_sequence_resume",
            OutreachTask.status == "pending",
            OutreachTask.due_at <= now,
        )
    ).all()
    n = 0
    for task in tasks:
        pid = task.prospect_id
        if not pid:
            task.status = "cancelled"
            continue
        prospect = db.get(Prospect, pid)
        if prospect is None:
            task.status = "cancelled"
            continue
        if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_POSTERGADO:
            task.status = "cancelled"
            continue
        prospect.sequence_paused = False
        prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
        prospect.defer_resume_at = None
        task.status = "done"
        task.updated_at = now
        followup_engine.schedule_followup_task(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            days=1,
            campaign=campaign,
            title="Seguimiento tras postergación",
        )
        _append_log(
            campaign,
            f"Nexus reanudó contacto para {prospect.name} (fin postergación).",
            kind="defer",
        )
        n += 1
    return n


def reactivate_from_postergado(db: Session, prospect: Prospect, campaign: Campaign) -> None:
    if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_POSTERGADO:
        return
    followup_engine.cancel_deferred_resume_tasks(db, prospect.id)
    prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
    prospect.sequence_paused = False
    prospect.defer_resume_at = None
    followup_engine.schedule_followup_task(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        title="Reactivación manual SDR (postergado)",
        campaign=campaign,
    )
    _append_log(
        campaign,
        f"SDR reactivó manualmente a {prospect.name} (salió de Postergados).",
        kind="reactivate",
    )


def mark_encajonado(prospect: Prospect) -> None:
    prospect.sequence_group = SEQUENCE_GROUP_ENCAJONADO
    prospect.sequence_paused = True


def reactivate_from_encajonado(db: Session, prospect: Prospect, campaign: Campaign) -> None:
    if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_ENCAJONADO:
        return
    prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
    prospect.sequence_paused = False
    prospect.sequence_started_at = datetime.now(UTC)
    _set_fired(prospect, [])
    followup_engine.schedule_followup_task(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        title="Reactivación manual SDR",
        campaign=campaign,
    )
    _append_log(campaign, f"SDR reactivó secuencia para {prospect.name}.", kind="reactivate")


def maybe_encajonar_after_reactivation_silence(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> None:
    """Si pasó 1 semana sin respuesta tras reactivación, encajonar."""
    if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_FOLLOW_UPS:
        return
    ra = getattr(prospect, "reactivation_sent_at", None)
    if not ra:
        return
    if datetime.now(UTC) - ra > timedelta(days=7) and not prospect.last_inbound_at:
        mark_encajonado(prospect)
        _append_log(campaign, f"{prospect.name} pasó a Encajonados (sin respuesta post-reactivación).", kind="sequence")


def sync_agendado_if_meeting(db: Session, prospect: Prospect) -> None:
    from app.models.meeting import Meeting

    n = db.scalar(select(func.count()).select_from(Meeting).where(Meeting.prospect_id == prospect.id))
    if n and int(n) > 0:
        prospect.sequence_state = STATE_AGENDADO


def apply_real_calendar_booking(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> None:
    """
    Reunión real en Google Calendar (evento futuro confirmado): prioridad sobre secuencia automática.
    Pausa outreach, cancela follow-ups y postergaciones pendientes, grupo Reuniones.
    """
    followup_engine.cancel_pending_followup_tasks(db, prospect.id)
    followup_engine.cancel_deferred_resume_tasks(db, prospect.id)
    prev_group = getattr(prospect, "sequence_group", None)
    prospect.sequence_paused = True
    prospect.sequence_group = SEQUENCE_GROUP_REUNIONES
    prospect.sequence_state = STATE_AGENDADO
    if getattr(prospect, "defer_resume_at", None) is not None:
        prospect.defer_resume_at = None
    if prev_group != SEQUENCE_GROUP_REUNIONES:
        _append_log(
            campaign,
            f"{prospect.name} — reunión detectada en Google Calendar (secuencia en pausa, sin follow-ups automáticos).",
            kind="calendar",
        )


def resume_from_reuniones(db: Session, prospect: Prospect, campaign: Campaign) -> None:
    """SDR: volver a seguimiento automático desde Reuniones (no borra la fila Meeting)."""
    if getattr(prospect, "sequence_group", None) != SEQUENCE_GROUP_REUNIONES:
        return
    prospect.sequence_group = SEQUENCE_GROUP_FOLLOW_UPS
    prospect.sequence_paused = False
    _append_log(
        campaign,
        f"{prospect.name} salió de Reuniones (reactivación manual).",
        kind="reactivate",
    )

