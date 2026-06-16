from __future__ import annotations

import random
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.prospect import Prospect
from app.models.outreach import OutreachMessage
from app.schemas.autopilot import AutopilotCycleStats
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services import (
    conversation_intelligence,
    followup_engine,
    multichannel_sequence as mseq,
    openai_service,
    outreach_metrics as om,
    pipeline_sync,
    prospect_scoring,
)
from app.services.meeting_booking import ensure_simulated_meeting_for_booked_prospect
from app.services import outreach_simulation as sim
from app.services.ai_instruction_context import campaign_education_blob


def _conversation_for_prospect(db: Session, prospect_id: int) -> list[OutreachMessage]:
    return db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect_id)
        .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
    ).all()


def _conversation_payload(messages: list[OutreachMessage]) -> list[dict[str, str]]:
    return [
        {"sender_type": m.sender_type, "direction": m.direction, "message": m.message}
        for m in messages
    ]


def _campaign_payload(campaign: Campaign) -> dict[str, str]:
    ch = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
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
        "preferred_channel_hint": " → ".join(ch),
        "allowed_channels_csv": ",".join(ch),
        "calendar_link": campaign.calendar_link or "",
        "icp_ai_digest": digest,
        "sender_name": (getattr(campaign, "sender_name", None) or "").strip(),
        "sender_email": (getattr(campaign, "sender_email", None) or "").strip(),
    }


def _product_payload(campaign: Campaign) -> dict[str, str]:
    p = campaign.product
    return {
        "name": p.name if p else "Nexus Sales",
        "value_proposition": p.value_proposition if p and p.value_proposition else "",
        "description": p.description if p and p.description else "",
    }


def _prospect_payload(prospect: Prospect) -> dict[str, str]:
    return {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
    }


def _response_probability(prospect: Prospect) -> float:
    base = 0.35
    score = int(getattr(prospect, "compatibility_score", 0) or 0)
    if score >= 80:
        base += 0.2
    elif score >= 60:
        base += 0.1
    touches = int(getattr(prospect, "outreach_touch_count", 0) or 0)
    if touches > 2:
        base += 0.1
    return max(0.1, min(0.8, base))


def _days_since(dt: datetime | None) -> float | None:
    if not dt:
        return None
    return (datetime.now(UTC) - dt).total_seconds() / 86400.0


def _refresh_scores(prospect: Prospect) -> None:
    inbound_count = int(getattr(prospect, "followup_count", 0) or 0)
    score, reason = prospect_scoring.compute_interest_probability(
        current_status=prospect.status,
        prior_interest_level=prospect.interest_level,
        objection_type=prospect.objection_type,
        inbound_count=inbound_count,
        asks_questions=False,
        wants_meeting=prospect.status == ProspectStatus.meeting_booked.value,
        last_inbound_text="",
        days_since_last_inbound=_days_since(prospect.last_inbound_at),
    )
    prospect.interest_probability = score
    prospect.score_reason = reason
    if prospect.status == ProspectStatus.not_interested.value:
        prospect.next_best_action = "No insistir. Cerrar ciclo y documentar objeción."
    elif prospect.status == ProspectStatus.meeting_booked.value:
        prospect.next_best_action = "Preparar agenda y confirmar asistencia."
    elif prospect.status == ProspectStatus.interested.value:
        prospect.next_best_action = "Proponer reunión breve con próximos pasos."
    elif prospect.status == ProspectStatus.contacted.value:
        prospect.next_best_action = "Esperar respuesta o enviar follow-up corto."
    else:
        prospect.next_best_action = "Revisar manualmente conversación."


def run_campaign_cycle(db: Session, campaign: Campaign) -> tuple[AutopilotCycleStats, list[str]]:
    campaign_loaded = db.scalars(
        select(Campaign).where(Campaign.id == campaign.id).options(selectinload(Campaign.product))
    ).first()
    if campaign_loaded is None:
        return AutopilotCycleStats(), ["No se encontró campaña."]

    stats = AutopilotCycleStats()
    log: list[str] = []
    if om.is_real_mode():
        log.append(
            "NEXUS_REAL_MODE: el autopilot no genera mensajes simulados ni secuencia automática en BD. "
            "Usá envío Gmail real e inbound sync."
        )
        campaign_loaded.autopilot_last_cycle_at = datetime.now(UTC)
        campaign_loaded.autopilot_last_cycle_summary = {
            "processed": 0,
            "messages_generated": 0,
            "responses_simulated": 0,
            "followups_generated": 0,
            "tasks_created": 0,
            "meetings_created": 0,
            "interested_detected": 0,
            "log": log,
        }
        return stats, log

    blob = campaign_education_blob(db, campaign_loaded)
    channels = coerce_allowed_channels(getattr(campaign_loaded, "allowed_channels", None))

    seq_stats = mseq.process_due_milestones(
        db,
        campaign_loaded,
        channels_allowed=channels,
        education_blob=blob,
    )
    if seq_stats.get("touches") or seq_stats.get("linkedin_drafts") or seq_stats.get("reactivations"):
        log.append(
            f"Secuencia multicanal: {seq_stats.get('touches', 0)} hitos · "
            f"{seq_stats.get('linkedin_drafts', 0)} borradores LinkedIn · "
            f"{seq_stats.get('reactivations', 0)} reactivaciones (día 42)"
        )

    prospects = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign_loaded.id,
            Prospect.status.in_(
                [
                    ProspectStatus.imported.value,
                    ProspectStatus.compatible.value,
                    ProspectStatus.contacted.value,
                    ProspectStatus.replied.value,
                    ProspectStatus.interested.value,
                ]
            ),
        )
    ).all()
    stats.processed = len(prospects)

    for prospect in prospects:
        history = _conversation_for_prospect(db, prospect.id)
        had_history = bool(history)
        if prospect.status in (ProspectStatus.imported.value, ProspectStatus.compatible.value) and not had_history:
            content = openai_service.generate_outreach_message(
                prospect=_prospect_payload(prospect),
                campaign=_campaign_payload(campaign_loaded),
                product=_product_payload(campaign_loaded),
                tone=campaign_loaded.tone,
                education=blob,
            )
            db.add(
                sim.make_message(
                    prospect_id=prospect.id,
                    campaign_id=campaign_loaded.id,
                    sender_type="ai",
                    message=content,
                    channel=sim.choose_channel(prospect, channels),
                    direction="outbound",
                )
            )
            followup_engine.record_ai_outbound(
                db,
                prospect,
                campaign_calendar_link=campaign_loaded.calendar_link,
                outbound_text=content,
            )
            followup_engine.schedule_followup_task(
                db,
                company_id=campaign_loaded.company_id,
                campaign_id=campaign_loaded.id,
                prospect_id=prospect.id,
                title="Follow-up pendiente",
                campaign=campaign_loaded,
            )
            prospect.status = ProspectStatus.contacted.value
            pipeline_sync.sync_pipeline_from_status(prospect)
            stats.messages_generated += 1

        refreshed = _conversation_for_prospect(db, prospect.id)
        last_msg = refreshed[-1] if refreshed else None
        waiting_reply = bool(last_msg and last_msg.direction == "outbound")
        should_respond = waiting_reply and random.random() <= _response_probability(prospect)

        if should_respond and not om.is_outreach_simulation_disabled():
            inbound_text = openai_service.generate_simulated_inbound_turn(
                prospect=_prospect_payload(prospect),
                campaign=_campaign_payload(campaign_loaded),
                product=_product_payload(campaign_loaded),
                status_label=prospect.status,
                education=blob,
            )
            inbound = sim.make_message(
                prospect_id=prospect.id,
                campaign_id=campaign_loaded.id,
                sender_type="prospect",
                message=inbound_text,
                channel=sim.choose_channel(prospect, channels),
                direction="inbound",
            )
            db.add(inbound)
            followup_engine.record_prospect_inbound(db, prospect)
            mseq.on_inbound_pause_sequence(db, prospect)
            digest = "\n".join(
                f"{x['sender_type']}/{x['direction']}: {(x.get('message') or '')[:220]}"
                for x in _conversation_payload(_conversation_for_prospect(db, prospect.id))[-16:]
            ) or "(vacío)"
            sig = conversation_intelligence.classify_inbound_full(
                inbound_text=inbound_text,
                prior_interest=getattr(prospect, "interest_level", None),
                conversation_digest=digest,
                education=blob,
            )
            followup_engine.apply_inbound_signals(
                db,
                prospect,
                objection_type=sig.objection_type,
                interest_level=sig.interest_level,
            )
            followup_engine.cancel_pending_followup_tasks(db, prospect.id)
            followup_engine.create_review_inbound_task(
                db,
                company_id=campaign_loaded.company_id,
                campaign_id=campaign_loaded.id,
                prospect_id=prospect.id,
            )
            if sig.interest_level == "high" and not (
                sig.prospect_timing_hold or sig.objection_type == "timing"
            ):
                followup_engine.create_hot_lead_task(
                    db,
                    company_id=campaign_loaded.company_id,
                    campaign_id=campaign_loaded.id,
                    prospect_id=prospect.id,
                )
                stats.interested_detected += 1
            prospect.status = conversation_intelligence.prospect_status_from_inbound_signals(
                prospect.status, sig
            )
            if prospect.status == ProspectStatus.not_interested.value:
                mseq.mark_encajonado(prospect)
            pipeline_sync.sync_pipeline_from_status(prospect)
            if prospect.status == ProspectStatus.meeting_booked.value:
                if ensure_simulated_meeting_for_booked_prospect(db, campaign_loaded, prospect):
                    stats.meetings_created += 1
            if sig.objection_type != "not_interested":
                timing_soft = conversation_intelligence.timing_deferral_should_apply(
                    sig, inbound_text=inbound_text
                )
                if timing_soft:
                    resume = conversation_intelligence.infer_defer_resume_utc(
                        inbound_text=inbound_text,
                        defer_iso=sig.defer_resume_at_iso,
                        now=datetime.now(UTC),
                    )
                    mseq.apply_prospect_timing_deferral(
                        db,
                        prospect,
                        campaign_loaded,
                        defer_resume_at=resume,
                        inbound_snippet=(inbound_text or "")[:480],
                    )
                else:
                    plain_in = (inbound_text or "").strip()
                    norm_in = conversation_intelligence.normalize_inbound_text_for_classification(plain_in)
                    rb = bool(norm_in) and conversation_intelligence.inbound_wants_immediate_booking(norm_in)
                    mseq.clear_postergado_state(
                        db,
                        prospect,
                        campaign_loaded,
                        reason="prioridad de agendamiento" if rb else "inbound reclasificado (sin postergación)",
                    )
                    mseq.promote_operational_group_after_prospect_reply(prospect)
            _refresh_scores(prospect)
            stats.responses_simulated += 1
            stats.tasks_created += 1
            mseq.maybe_encajonar_after_reactivation_silence(db, prospect, campaign_loaded)
            mseq.sync_agendado_if_meeting(db, prospect)
            continue

        if prospect.status == ProspectStatus.contacted.value and waiting_reply:
            followup = openai_service.generate_followup_message(
                prospect=_prospect_payload(prospect),
                previous_messages=_conversation_payload(refreshed),
                campaign=_campaign_payload(campaign_loaded),
                product=_product_payload(campaign_loaded),
                education=blob,
                objection_type=prospect.objection_type,
                interest_level=prospect.interest_level or "low",
                outbound_seq_index=int(prospect.outreach_touch_count or 0),
                allow_soft_meeting_hint=False,
            )
            db.add(
                sim.make_message(
                    prospect_id=prospect.id,
                    campaign_id=campaign_loaded.id,
                    sender_type="ai",
                    message=followup,
                    channel=sim.choose_channel(prospect, channels),
                    direction="outbound",
                )
            )
            followup_engine.record_ai_outbound(
                db,
                prospect,
                campaign_calendar_link=campaign_loaded.calendar_link,
                outbound_text=followup,
            )
            followup_engine.schedule_followup_task(
                db,
                company_id=campaign_loaded.company_id,
                campaign_id=campaign_loaded.id,
                prospect_id=prospect.id,
                title="Follow-up pendiente",
                campaign=campaign_loaded,
            )
            pipeline_sync.sync_pipeline_from_status(prospect)
            _refresh_scores(prospect)
            stats.followups_generated += 1
            stats.tasks_created += 1
        else:
            _refresh_scores(prospect)
        mseq.maybe_encajonar_after_reactivation_silence(db, prospect, campaign_loaded)
        mseq.sync_agendado_if_meeting(db, prospect)

    if stats.messages_generated:
        log.append(f"Nexus contactó {stats.messages_generated} prospectos")
    if stats.responses_simulated:
        log.append(f"Nexus simuló {stats.responses_simulated} respuestas")
    if stats.interested_detected:
        log.append(f"Nexus detectó {stats.interested_detected} interesados")
    if stats.followups_generated:
        log.append(f"Nexus creó {stats.followups_generated} follow-ups")
    if stats.meetings_created:
        log.append(f"Nexus registró {stats.meetings_created} reuniones")
    if not log:
        log.append("Nexus no encontró cambios para este ciclo.")

    campaign_loaded.autopilot_last_cycle_at = datetime.now(UTC)
    campaign_loaded.autopilot_last_cycle_summary = {
        "processed": stats.processed,
        "messages_generated": stats.messages_generated,
        "responses_simulated": stats.responses_simulated,
        "followups_generated": stats.followups_generated,
        "tasks_created": stats.tasks_created,
        "meetings_created": stats.meetings_created,
        "interested_detected": stats.interested_detected,
        "log": log,
    }
    return stats, log
