from datetime import UTC, datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.deps import get_campaign, get_prospect
from app.auth.deps import get_current_user
from app.models.user import User
from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage, OutreachSequence
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.linkedin_assisted import (
    LinkedInAssistQueueRead,
    LinkedInAssistedAbandonRead,
    LinkedInAssistedAssistRead,
    LinkedInAssistedMarkSentRead,
    LinkedInAssistedPrepareRead,
    LinkedInAssistedRegenerateRead,
    LinkedInAssistedSummaryRead,
    LinkedInConnectionStatusBody,
    LinkedInConnectionStatusRead,
    LinkedInConnectSentRead,
    LinkedInInboundRegisterBody,
    LinkedInInboundRegisterRead,
    LinkedInPendingConnectCheckRead,
    LinkedInPendingConnectChecksRead,
    LinkedInProfileUrnBody,
    LinkedInProfileUrnRead,
    LinkedInResolveProspectRead,
)
from app.services import linkedin_assisted_service
from app.services import whatsapp_assisted_service
from app.services import call_assisted_service
from app.services import mail_queue_service
from app.schemas.mail_queue import MailQueueRead
from app.schemas.whatsapp_assisted import (
    WhatsAppAssistQueueRead,
    WhatsAppAssistedAbandonRead,
    WhatsAppAssistedAssistRead,
    WhatsAppAssistedMarkSentRead,
    WhatsAppInboundRegisterBody,
    WhatsAppInboundRegisterRead,
    WhatsAppResolveProspectRead,
)
from app.schemas.call_assisted import CallAssistMarkDoneRead, CallAssistQueueRead
from app.services.linkedin_inbound_sync import register_linkedin_inbound as register_linkedin_inbound_message
from app.services.whatsapp_inbound_sync import (
    register_whatsapp_inbound as register_whatsapp_inbound_message,
    resolve_prospect_by_whatsapp_digits,
)
from app.schemas.outreach import (
    ContinueWithoutChannelBody,
    ContinueWithoutChannelResponse,
    FollowupPreviewRead,
    ManualFollowupActionRead,
    OutreachCampaignRead,
    OutreachMessageRead,
    OutreachSequenceRead,
    OutreachStartResponse,
    OutreachStats,
    ProspectConversationWorkspaceRead,
    ProspectReanalysisRead,
    ProspectResponseSimulationRead,
    SimulateResponsesBatchRead,
)
from app.schemas.outreach_tasks import FollowupReprogramRequest, ScheduledFollowupRunResponse
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services.campaign_outreach_context import campaign_dict_for_outreach
from app.services import conversation_intelligence
from app.services import followup_engine
from app.services.meeting_booking import ensure_simulated_meeting_for_booked_prospect
from app.services import outreach_simulation as sim
from app.services.ai_behavior_policy import load_behavior_policy, resolve_booking_priority_from_signals
from app.services.ai_instruction_context import campaign_education_blob
from app.services import multichannel_sequence as mseq
from app.services import openai_service
from app.services import pipeline_sync
from app.services import prospect_scoring
from app.services import outreach_metrics as om
from app.services.campaign_activation import activate_campaign, pause_campaign

router = APIRouter(tags=["outreach"])
logger = logging.getLogger(__name__)


def _serialize_message(m: OutreachMessage) -> OutreachMessageRead:
    return OutreachMessageRead.model_validate(m)


def _get_or_create_sequence(
    db: Session, campaign_id: int, *, create_if_missing: bool = True
) -> OutreachSequence | None:
    seq = db.scalars(
        select(OutreachSequence).where(OutreachSequence.campaign_id == campaign_id)
    ).first()
    if seq is None and create_if_missing:
        seq = OutreachSequence(campaign_id=campaign_id, is_running=False, current_step=0)
        db.add(seq)
        db.flush()
    return seq


def _sequence_read_for_campaign(db: Session, campaign_id: int) -> OutreachSequenceRead:
    """Secuencia persistida; si la campaña es nueva, crea la fila en BD (evita 500 en GET)."""
    had_row = (
        db.scalars(
            select(OutreachSequence.id).where(OutreachSequence.campaign_id == campaign_id)
        ).first()
        is not None
    )
    seq = _get_or_create_sequence(db, campaign_id, create_if_missing=True)
    if not had_row:
        db.commit()
    db.refresh(seq)
    return OutreachSequenceRead.model_validate(seq)


def _stats_for_campaign(db: Session, campaign_id: int) -> OutreachStats:
    """Métricas alineadas a actividad en mensajes (Gmail, borradores, IA) — no solo status legacy."""
    rows = db.execute(
        select(Prospect.status, func.count(Prospect.id))
        .where(Prospect.campaign_id == campaign_id)
        .group_by(Prospect.status)
    ).all()
    count_map = {status: count for status, count in rows}
    total_prospects = int(sum(count_map.values()) or 0)

    if om.is_real_mode():
        touched = om.distinct_prospects_contacted_campaign(db, campaign_id)
        with_inbound = om.distinct_prospects_with_real_gmail_inbound_campaign(db, campaign_id)
    else:
        touched = om.distinct_prospects_with_outbound_campaign(db, campaign_id)
        with_inbound = om.distinct_prospects_with_inbound_campaign(db, campaign_id)

    messages_outbound = om.count_outbound_messages_campaign(db, campaign_id)
    messages_inbound = om.count_inbound_messages_campaign(db, campaign_id)

    return OutreachStats(
        contacted=touched,
        responded=with_inbound,
        interested=count_map.get(ProspectStatus.interested.value, 0),
        not_interested=count_map.get(ProspectStatus.not_interested.value, 0),
        failed=count_map.get(ProspectStatus.failed.value, 0),
        total_prospects=total_prospects,
        prospects_pending_contact=max(0, total_prospects - touched),
        messages_outbound=messages_outbound,
        messages_inbound=messages_inbound,
    )


def _conversation_for_prospect(db: Session, prospect_id: int) -> list[OutreachMessage]:
    return db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect_id)
        .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
    ).all()


def _conversation_payload(messages: list[OutreachMessage]) -> list[dict[str, str]]:
    return [
        {
            "sender_type": m.sender_type,
            "direction": m.direction,
            "message": m.message,
        }
        for m in messages
    ]


def _campaign_allowed_channels_list(campaign: Campaign) -> list[str]:
    return coerce_allowed_channels(getattr(campaign, "allowed_channels", None))


def _campaign_payload(campaign: Campaign) -> dict[str, str]:
    return campaign_dict_for_outreach(campaign)


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


def _days_since(dt: datetime | None) -> float | None:
    if not dt:
        return None
    now = datetime.now(UTC)
    try:
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return None


def _refresh_conversation_scores(db: Session, prospect: Prospect, campaign: Campaign) -> None:
    msgs = _conversation_for_prospect(db, prospect.id)
    inbounds = [m for m in msgs if m.direction == "inbound" and m.sender_type == "prospect"]
    last_inbound = inbounds[-1].message if inbounds else ""
    asks_questions = bool(last_inbound and ("?" in last_inbound or len(last_inbound) > 60))
    _li = (last_inbound or "").lower()
    wants_meeting = conversation_intelligence.prospect_text_implies_explicit_meeting(
        last_inbound
    ) or any(x in _li for x in ("reunión", "reunion", "llamada", "llamado", "agendar", "coordina"))
    score, reason = prospect_scoring.compute_interest_probability(
        current_status=prospect.status,
        prior_interest_level=prospect.interest_level,
        objection_type=prospect.objection_type,
        inbound_count=len(inbounds),
        asks_questions=asks_questions,
        wants_meeting=wants_meeting,
        last_inbound_text=last_inbound,
        days_since_last_inbound=_days_since(prospect.last_inbound_at),
    )
    prospect.interest_probability = score
    compat, fit_reason = prospect_scoring.explain_compatibility(
        {
            "country": prospect.country,
            "industry": prospect.industry,
            "role": prospect.role,
            "email": prospect.email,
            "linkedin_url": prospect.linkedin_url,
        },
        campaign_country=campaign.target_country,
        campaign_industry=campaign.target_industry,
        campaign_role=campaign.target_role,
        product_name=campaign.product.name if campaign.product else "Nexus Sales",
    )
    prospect.compatibility_score = compat
    prospect.score_reason = f"fit: {fit_reason} | interes: {reason}"
    if prospect.status == ProspectStatus.not_interested.value:
        prospect.next_best_action = "No insistir. Cerrar ciclo y etiquetar aprendizaje."
    elif prospect.status == ProspectStatus.meeting_booked.value:
        prospect.next_best_action = "Confirmar llamada y compartir link de calendario acordado."
    elif prospect.status == ProspectStatus.interested.value:
        prospect.next_best_action = "Proponer llamada breve (10–15 min) para avanzar sin alargar el chat."
    elif len(inbounds) == 0:
        prospect.next_best_action = "Hacer follow-up breve con angulo nuevo."
    else:
        prospect.next_best_action = "Responder objecion/pregunta y validar timing."


def _build_followup_text(
    *,
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    instruction_blob: str,
    allow_soft_meeting_hint: bool = False,
) -> str:
    history = _conversation_for_prospect(db, prospect.id)
    return openai_service.generate_followup_message(
        prospect=_prospect_payload(prospect),
        previous_messages=_conversation_payload(history),
        campaign=_campaign_payload(campaign),
        product=_product_payload(campaign),
        education=instruction_blob,
        objection_type=prospect.objection_type,
        interest_level=prospect.interest_level or "low",
        outbound_seq_index=int(prospect.outreach_touch_count or 0),
        allow_soft_meeting_hint=allow_soft_meeting_hint,
        is_final_goodbye=True,
    )


@router.get("/campaigns/{campaign_id}/outreach", response_model=OutreachCampaignRead)
def get_campaign_outreach(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> OutreachCampaignRead:
    from app.services.sequence_channel_gate import (
        read_campaign_integration_block,
        seller_channel_block,
    )
    from app.services.campaign_sequence_channels import effective_channel_for_day

    last_messages = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.campaign_id == campaign_id)
        .order_by(OutreachMessage.created_at.desc())
        .limit(50)
    ).all()
    pending_ops = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(
                OutreachTask.campaign_id == campaign_id,
                OutreachTask.status == "pending",
                OutreachTask.task_kind.in_(
                    [
                        "scheduled_followup",
                        "deferred_sequence_resume",
                        "review_inbound",
                        "hot_lead",
                        "awaiting_reply",
                    ]
                ),
            )
        )
        or 0
    )
    block = read_campaign_integration_block(campaign)
    # LI-SAFE: borrar banner viejo de “verificando 1º/2º/3º” (ya no hay probes).
    if block and str(block.get("code") or "") == "extension_not_responding":
        from app.services.linkedin_assisted_service import LI_SAFE_NO_PROFILE_PROBE

        if LI_SAFE_NO_PROFILE_PROBE:
            from app.services.sequence_channel_gate import clear_campaign_integration_block

            clear_campaign_integration_block(
                campaign,
                channel="linkedin",
                note="LI-SAFE: sin verify de grado — se limpia bloqueo de extensión.",
            )
            db.commit()
            block = None
    # Si hay bloqueo guardado, revalidar: si ya reconectó, limpiar.
    if block:
        code = str(block.get("code") or "")
        if code == "extension_not_responding":
            from app.services.sequence_channel_gate import detect_linkedin_verify_stall

            stall = detect_linkedin_verify_stall(db, campaign)
            if stall is None:
                from app.services.sequence_channel_gate import clear_campaign_integration_block

                clear_campaign_integration_block(
                    campaign,
                    channel="linkedin",
                    note="LinkedIn ya respondió — secuencia puede continuar.",
                )
                db.commit()
                block = None
            else:
                block = {**block, **stall}
        else:
            live = seller_channel_block(db, campaign, block.get("channel"))
            if live is None:
                from app.services.sequence_channel_gate import clear_campaign_integration_block

                clear_campaign_integration_block(
                    campaign,
                    channel=str(block.get("channel") or ""),
                    note=f"Integración de {block.get('channel')} reconectada — secuencia reanudada.",
                )
                db.commit()
                block = None
            else:
                # Preferir el error live (más exacto/actual).
                block = {**block, **live}
    else:
        from app.services.sequence_channel_gate import detect_linkedin_verify_stall

        stall = detect_linkedin_verify_stall(db, campaign)
        if stall:
            block = stall
            from app.services.sequence_channel_gate import set_campaign_integration_block

            set_campaign_integration_block(
                campaign,
                stall,
                blocked_prospects=int(stall.get("blocked_prospects") or 0),
            )
            db.commit()
        elif (campaign.status or "") == "running" and not campaign.automation_paused:
            day1 = effective_channel_for_day(campaign, 1)
            live = seller_channel_block(db, campaign, day1)
            if live:
                block = live

    progress_note = None
    log = getattr(campaign, "outreach_activity_log", None)
    if isinstance(log, list):
        from app.services.linkedin_assisted_service import LI_SAFE_NO_PROFILE_PROBE
        from app.services.sequence_channel_gate import BLOCK_KIND, BLOCK_RESOLVED_KIND

        saw_block_resolved = False
        for entry in reversed(log):
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or "")
            msg = str(entry.get("message") or "").strip()
            if not msg:
                continue
            if kind == BLOCK_RESOLVED_KIND:
                saw_block_resolved = True
                continue
            # Bloqueo ya resuelto (o LI-SAFE sin verify): no reciclarlo como “qué está pasando”.
            if kind == BLOCK_KIND:
                if saw_block_resolved:
                    continue
                if LI_SAFE_NO_PROFILE_PROBE and (
                    str(entry.get("code") or "") == "extension_not_responding"
                    or "1º/2º/3º" in msg
                    or "verificando" in msg.lower()
                ):
                    continue
            if kind in ("sequence", "sourcing", "linkedin_suggested", BLOCK_KIND):
                progress_note = msg
                break

    return OutreachCampaignRead(
        sequence=_sequence_read_for_campaign(db, campaign_id),
        stats=_stats_for_campaign(db, campaign_id),
        last_messages=[_serialize_message(m) for m in last_messages],
        pending_operational_tasks=pending_ops,
        real_mode=om.is_real_mode(),
        simulation_disabled=om.is_outreach_simulation_disabled(),
        sequence_block=block,
        progress_note=progress_note,
    )


@router.post(
    "/campaigns/{campaign_id}/outreach/continue-without-channel",
    response_model=ContinueWithoutChannelResponse,
)
def continue_campaign_without_channel(
    campaign_id: int,
    body: ContinueWithoutChannelBody,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    current_user: User = Depends(get_current_user),
) -> ContinueWithoutChannelResponse:
    """Omite un canal bloqueado (extensión/Gmail) y sigue la secuencia con el resto del plan."""
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                "Confirmá explícitamente: seguir sin ese canal omite esos toques "
                "y la secuencia continúa solo con los canales restantes."
            ),
        )
    from app.services.sequence_channel_gate import (
        continue_sequence_without_channel,
        read_campaign_integration_block,
    )

    result = continue_sequence_without_channel(
        db,
        campaign,
        channel=body.channel,
        actor_user_id=int(current_user.id),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail") or "No se pudo continuar.")
    db.commit()
    db.refresh(campaign)
    return ContinueWithoutChannelResponse(
        ok=True,
        channel=result.get("channel"),
        allowed_channels=list(result.get("allowed_channels") or []),
        omitted_touches=int(result.get("omitted_touches") or 0),
        advanced_prospects=int(result.get("advanced_prospects") or 0),
        message=result.get("message"),
        sequence_block=read_campaign_integration_block(campaign),
    )


@router.post("/campaigns/{campaign_id}/outreach/start", response_model=OutreachStartResponse)
def start_campaign_outreach(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> OutreachStartResponse:
    result = activate_campaign(db, campaign_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail") or "No se pudo iniciar la campaña")

    seq = result["sequence"]
    campaign_loaded = result["campaign"]
    db.commit()
    db.refresh(seq)
    db.refresh(campaign_loaded)

    if result.get("defer_sourcing"):
        from app.services.lead_sourcing.auto_bootstrap import schedule_campaign_sourcing_background

        schedule_campaign_sourcing_background(int(campaign_id))

    return OutreachStartResponse(
        sequence=OutreachSequenceRead.model_validate(seq),
        contacted_now=int(result.get("contacted_now") or 0),
        drafts=int(result.get("drafts") or 0),
        sent=int(result.get("sent") or 0),
        skipped=int(result.get("skipped") or 0),
        errors=int(result.get("errors") or 0),
        error_messages=list(result.get("error_messages") or []),
        campaign_status=campaign_loaded.status,
        gmail_connected=bool(result.get("gmail_connected")),
        used_gmail=bool(result.get("used_gmail")),
        sourcing_ran=bool(result.get("sourcing_ran")),
        sourcing_queued=bool(result.get("sourcing_queued")),
        sourcing_imported=int(result.get("sourcing_imported") or 0),
        sourcing_message=result.get("sourcing_message"),
        channel_enrich_pending=int(result.get("channel_enrich_pending") or 0),
        sourcing_quota_met=bool(result.get("sourcing_quota_met")),
        sourcing_prospect_count_after=int(result.get("sourcing_prospect_count_after") or 0),
        sourcing_prospect_count_target=int(result.get("sourcing_prospect_count_target") or 0),
    )


@router.post("/campaigns/{campaign_id}/outreach/stop", response_model=OutreachSequenceRead)
def stop_campaign_outreach(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> OutreachSequenceRead:
    seq = pause_campaign(db, campaign_id)
    if seq is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    db.commit()
    db.refresh(seq)
    return OutreachSequenceRead.model_validate(seq)


_SIMULATABLE_STATUSES = frozenset(
    {
        ProspectStatus.contacted.value,
        ProspectStatus.replied.value,
        ProspectStatus.interested.value,
    }
)


def _simulate_prospect_response_impl(
    db: Session,
    prospect: Prospect,
) -> ProspectResponseSimulationRead:
    """Ejecuta un turno de simulación inbound+IA y hace commit. Lanza ValueError si el estado no admite simulación."""
    if prospect.status not in _SIMULATABLE_STATUSES:
        raise ValueError(
            "Solo se pueden simular respuestas para prospectos contactados, con réplica o interesados."
        )

    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None:
        raise ValueError("Campaña no encontrada")
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign.id)
        .options(selectinload(Campaign.product))
    ).first() or campaign

    instruction_blob = campaign_education_blob(db, campaign)
    channels_allowed = _campaign_allowed_channels_list(campaign)

    channel = sim.choose_channel(prospect, channels_allowed)
    created: list[OutreachMessage] = []

    inbound_text = openai_service.generate_simulated_inbound_turn(
        prospect=_prospect_payload(prospect),
        campaign=_campaign_payload(campaign),
        product=_product_payload(campaign),
        status_label=prospect.status,
        education=instruction_blob,
    )

    inbound = sim.make_message(
        prospect_id=prospect.id,
        campaign_id=prospect.campaign_id,
        sender_type="prospect",
        message=inbound_text,
        channel=channel,
        direction="inbound",
        is_testing=True,
    )
    db.add(inbound)
    db.flush()
    created.append(inbound)

    hist_payload = _conversation_payload(_conversation_for_prospect(db, prospect.id))
    digest_lines = [
        f"{x['sender_type']}/{x['direction']}: {(x.get('message') or '')[:240]}"
        for x in hist_payload[-16:]
    ]
    digest = "\n".join(digest_lines) if digest_lines else "(vacío)"

    policy = load_behavior_policy(db, campaign.company_id)
    norm_in = conversation_intelligence.normalize_inbound_text_for_classification(inbound.message)

    sig = conversation_intelligence.classify_inbound_full(
        inbound_text=inbound.message,
        prior_interest=getattr(prospect, "interest_level", None),
        conversation_digest=digest,
        education=instruction_blob,
    )
    booking_priority = resolve_booking_priority_from_signals(
        policy,
        inbound_text=norm_in or inbound.message,
        explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
        prospect_wants_meeting=bool(sig.prospect_wants_meeting),
        interest_level=sig.interest_level,
    )
    response_class, _ = conversation_intelligence.classify_commercial_response(inbound.message, sig)
    reply_objective = conversation_intelligence.resolve_reply_objective(
        text=inbound.message,
        sig=sig,
        response_class=response_class,
    )
    booking_priority = booking_priority or reply_objective == "agendar"
    inbound_n = followup_engine.count_inbound_prospect_messages(db, prospect.id)
    allow_meeting = conversation_intelligence.should_allow_meeting_nudge(
        sig,
        inbound_turn_index=inbound_n,
    )
    timing_soft = (
        sig.objection_type != "not_interested"
        and conversation_intelligence.timing_deferral_should_apply(sig, inbound_text=inbound.message)
        and not booking_priority
    )

    history = _conversation_for_prospect(db, prospect.id)
    ai_reply = openai_service.generate_inbound_response(
        prospect=_prospect_payload(prospect),
        inbound_message=inbound.message,
        conversation_history=_conversation_payload(history),
        campaign=_campaign_payload(campaign),
        product=_product_payload(campaign),
        education=instruction_blob,
        objection_type=sig.objection_type,
        interest_level=sig.interest_level or "low",
        allow_soft_meeting_close=allow_meeting,
        inbound_turn_index=inbound_n,
        prospect_timing_soft=timing_soft,
        prospect_booking_priority=booking_priority,
        ai_policy=policy,
        prospect_wants_meeting=bool(sig.prospect_wants_meeting),
        explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
        prospect_substantive_questions=bool(sig.asks_concrete_questions),
        reply_objective=reply_objective,
        response_class=response_class,
    )
    reply = sim.make_message(
        prospect_id=prospect.id,
        campaign_id=prospect.campaign_id,
        sender_type="ai",
        message=ai_reply,
        channel=channel,
        direction="outbound",
        is_testing=True,
    )
    db.add(reply)
    created.append(reply)

    _refresh_conversation_scores(db, prospect, campaign)
    db.commit()
    for msg in created:
        db.refresh(msg)

    return ProspectResponseSimulationRead(
        prospect_id=prospect.id,
        new_status=prospect.status,
        messages=[_serialize_message(m) for m in created],
    )


@router.post(
    "/prospects/{prospect_id}/simulate-response",
    response_model=ProspectResponseSimulationRead,
)
def simulate_prospect_response(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectResponseSimulationRead:
    if not om.is_outreach_simulation_disabled():
        try:
            return _simulate_prospect_response_impl(db, prospect)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    raise HTTPException(
        status_code=403,
        detail="Las simulaciones de outreach están deshabilitadas (variable NEXUS_DISABLE_OUTREACH_SIMULATION).",
    )


@router.post(
    "/campaigns/{campaign_id}/outreach/simulate-responses",
    response_model=SimulateResponsesBatchRead,
)
def simulate_campaign_responses_batch(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> SimulateResponsesBatchRead:
    """Simula un turno de respuesta para todos los prospectos elegibles de la campaña (un commit por prospecto)."""
    if om.is_outreach_simulation_disabled():
        raise HTTPException(
            status_code=403,
            detail="Las simulaciones de outreach están deshabilitadas (variable NEXUS_DISABLE_OUTREACH_SIMULATION).",
        )
    prospects = db.scalars(
        select(Prospect).where(Prospect.campaign_id == campaign_id).order_by(Prospect.id.asc())
    ).all()
    simulated = 0
    skipped = 0
    errors: list[str] = []
    for p in prospects:
        if p.status not in _SIMULATABLE_STATUSES:
            skipped += 1
            continue
        label = (p.name or "").strip() or "Prospecto"
        company = (p.company_name or "").strip()
        prefix = f"{label}" + (f" ({company})" if company else "")
        try:
            _simulate_prospect_response_impl(db, p)
            simulated += 1
        except ValueError as ve:
            skipped += 1
            errors.append(f"{prefix}: {ve}")
        except Exception as exc:  # noqa: BLE001 — API batch: capturar fallos OpenAI/red
            db.rollback()
            errors.append(f"{prefix}: {exc}")
    detail_parts = [f"Simulados: {simulated}", f"Omitidos: {skipped}"]
    if errors:
        detail_parts.append(f"Errores: {len(errors)}")
    return SimulateResponsesBatchRead(
        simulated=simulated,
        skipped=skipped,
        errors=errors[:50],
        detail=". ".join(detail_parts),
    )


@router.post(
    "/prospects/{prospect_id}/generate-next-reply",
    response_model=OutreachMessageRead,
)
def generate_next_ai_reply(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> OutreachMessageRead:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    instruction_blob = campaign_education_blob(db, campaign)
    channels_allowed = _campaign_allowed_channels_list(campaign)

    history = _conversation_for_prospect(db, prospect.id)
    inbound = next((m for m in reversed(history) if m.direction == "inbound"), None)
    inbound_text = inbound.message if inbound else "¿Podés contarme un poco más?"

    hp = _conversation_payload(history)
    digest = "\n".join(
        f"{x['sender_type']}/{x['direction']}: {(x.get('message') or '')[:200]}" for x in hp[-14:]
    )
    inbound_n = followup_engine.count_inbound_prospect_messages(db, prospect.id)
    policy = load_behavior_policy(db, campaign.company_id)
    allow_meeting = False
    timing_soft = False
    last_sig = None
    booking_priority = False
    reply_objective = "seguimiento"
    response_class = None
    if inbound:
        mseq.on_inbound_pause_sequence(db, prospect)
        last_sig = conversation_intelligence.classify_inbound_full(
            inbound_text=inbound.message,
            prior_interest=getattr(prospect, "interest_level", None),
            conversation_digest=digest or "(vacío)",
            education=instruction_blob,
        )
        followup_engine.apply_inbound_signals(
            db,
            prospect,
            objection_type=last_sig.objection_type,
            interest_level=last_sig.interest_level,
        )
        prospect.status = conversation_intelligence.prospect_status_from_inbound_signals(
            prospect.status, last_sig
        )
        pipeline_sync.sync_pipeline_from_status(prospect)
        if prospect.status == ProspectStatus.meeting_booked.value:
            ensure_simulated_meeting_for_booked_prospect(db, campaign, prospect)
        if last_sig.objection_type == "not_interested":
            mseq.mark_encajonado(prospect)
        allow_meeting = conversation_intelligence.should_allow_meeting_nudge(
            last_sig,
            inbound_turn_index=inbound_n,
        )
        norm_tail = conversation_intelligence.normalize_inbound_text_for_classification(inbound.message)
        booking_priority = resolve_booking_priority_from_signals(
            policy,
            inbound_text=norm_tail or inbound.message,
            explicit_meeting_commitment=bool(last_sig.explicit_meeting_commitment),
            prospect_wants_meeting=bool(last_sig.prospect_wants_meeting),
            interest_level=last_sig.interest_level,
        )
        response_class, _ = conversation_intelligence.classify_commercial_response(
            inbound.message, last_sig
        )
        reply_objective = conversation_intelligence.resolve_reply_objective(
            text=inbound.message,
            sig=last_sig,
            response_class=response_class,
        )
        booking_priority = booking_priority or reply_objective == "agendar"
        timing_soft = (
            last_sig.objection_type != "not_interested"
            and conversation_intelligence.timing_deferral_should_apply(last_sig, inbound_text=inbound.message)
            and not booking_priority
        )
        from app.services import prospect_commercial_state as pcs

        pcs.sync_commercial_state_from_inbound(
            db,
            prospect=prospect,
            inbound_text=inbound.message,
            sig=last_sig,
            testing=False,
        )

    content = openai_service.generate_inbound_response(
        prospect=_prospect_payload(prospect),
        inbound_message=inbound_text,
        conversation_history=hp,
        campaign=_campaign_payload(campaign),
        product=_product_payload(campaign),
        education=instruction_blob,
        objection_type=prospect.objection_type,
        interest_level=prospect.interest_level or "low",
        allow_soft_meeting_close=allow_meeting,
        inbound_turn_index=max(1, inbound_n),
        prospect_timing_soft=timing_soft,
        prospect_booking_priority=booking_priority,
        ai_policy=policy,
        prospect_wants_meeting=bool(last_sig.prospect_wants_meeting) if last_sig else False,
        explicit_meeting_commitment=bool(last_sig.explicit_meeting_commitment) if last_sig else False,
        prospect_substantive_questions=bool(last_sig.asks_concrete_questions) if last_sig else False,
        reply_objective=reply_objective,
        response_class=response_class,
    )

    msg = sim.make_message(
        prospect_id=prospect.id,
        campaign_id=prospect.campaign_id,
        sender_type="ai",
        message=content,
        channel=sim.choose_channel(prospect, channels_allowed),
        direction="outbound",
    )
    db.add(msg)
    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=content,
    )
    if timing_soft and not booking_priority and last_sig is not None and inbound is not None:
        plain = conversation_intelligence.normalize_inbound_text_for_classification(inbound.message)
        resume = conversation_intelligence.infer_defer_resume_utc(
            inbound_text=plain,
            defer_iso=last_sig.defer_resume_at_iso,
            now=datetime.now(UTC),
        )
        mseq.apply_prospect_timing_deferral(
            db,
            prospect,
            campaign,
            defer_resume_at=resume,
            inbound_snippet=(plain or inbound.message)[:480],
        )
    elif inbound is not None:
        mseq.clear_postergado_state(
            db,
            prospect,
            campaign,
            reason="prioridad de agendamiento" if booking_priority else "inbound reclasificado (sin postergación)",
        )
        mseq.promote_operational_group_after_prospect_reply(prospect)
    prospect.meeting_suggestion_pending = bool(allow_meeting) and (
        prospect.status != ProspectStatus.meeting_booked.value
    )
    followup_engine.cancel_pending_followup_tasks(db, prospect.id)
    if not timing_soft and prospect.status == ProspectStatus.contacted.value:
        followup_engine.schedule_followup_task(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            campaign=campaign,
        )
    _refresh_conversation_scores(db, prospect, campaign)
    db.commit()
    db.refresh(msg)
    return _serialize_message(msg)


@router.post(
    "/prospects/{prospect_id}/outreach/generate-followup-now",
    response_model=FollowupPreviewRead,
)
def generate_followup_now(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> FollowupPreviewRead:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    blob = campaign_education_blob(db, campaign)
    text = _build_followup_text(db=db, prospect=prospect, campaign=campaign, instruction_blob=blob)
    return FollowupPreviewRead(prospect_id=prospect.id, message=text)


@router.post(
    "/prospects/{prospect_id}/outreach/send-followup-simulated",
    response_model=OutreachMessageRead,
)
def send_followup_simulated(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> OutreachMessageRead:
    if om.is_outreach_simulation_disabled():
        raise HTTPException(
            status_code=403,
            detail="Envío simulado deshabilitado (NEXUS_REAL_MODE o NEXUS_DISABLE_OUTREACH_SIMULATION). Usá Gmail send.",
        )
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    blob = campaign_education_blob(db, campaign)
    channels_allowed = _campaign_allowed_channels_list(campaign)
    content = _build_followup_text(db=db, prospect=prospect, campaign=campaign, instruction_blob=blob)
    msg = sim.make_message(
        prospect_id=prospect.id,
        campaign_id=prospect.campaign_id,
        sender_type="ai",
        message=content,
        channel=sim.choose_channel(prospect, channels_allowed),
        direction="outbound",
    )
    db.add(msg)
    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=content,
    )
    prospect.followup_count = int(getattr(prospect, "followup_count", 0) or 0) + 1
    prospect.last_followup_at = datetime.now(UTC)
    if prospect.status in (ProspectStatus.imported.value, ProspectStatus.compatible.value):
        prospect.status = ProspectStatus.contacted.value
    pipeline_sync.sync_pipeline_from_status(prospect)
    followup_engine.schedule_followup_task(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        campaign=campaign,
    )
    _refresh_conversation_scores(db, prospect, campaign)
    db.commit()
    db.refresh(msg)
    return _serialize_message(msg)


@router.post(
    "/prospects/{prospect_id}/outreach/mark-followup-sent",
    response_model=ManualFollowupActionRead,
)
def mark_followup_sent(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ManualFollowupActionRead:
    task = db.scalars(
        select(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect.id,
            OutreachTask.task_kind == "scheduled_followup",
            OutreachTask.status == "pending",
        )
        .order_by(OutreachTask.due_at.asc())
    ).first()
    if task:
        task.status = "done"
    prospect.followup_count = int(getattr(prospect, "followup_count", 0) or 0) + 1
    prospect.last_followup_at = datetime.now(UTC)
    if prospect.status in (ProspectStatus.imported.value, ProspectStatus.compatible.value):
        prospect.status = ProspectStatus.contacted.value
    pipeline_sync.sync_pipeline_from_status(prospect)
    db.commit()
    return ManualFollowupActionRead(ok=True, detail="Follow-up marcado como enviado.")


@router.post(
    "/prospects/{prospect_id}/outreach/reprogram-followup",
    response_model=ManualFollowupActionRead,
)
def reprogram_followup(
    prospect_id: int,
    body: FollowupReprogramRequest,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ManualFollowupActionRead:
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    followup_engine.schedule_followup_task(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        days=body.days,
        campaign=campaign,
        title=f"Follow-up reprogramado ({body.days}d)",
    )
    db.commit()
    return ManualFollowupActionRead(ok=True, detail="Follow-up reprogramado.")


@router.post(
    "/prospects/{prospect_id}/reanalyze-state",
    response_model=ProspectReanalysisRead,
)
def reanalyze_state_with_ai(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectReanalysisRead:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    blob = campaign_education_blob(db, campaign)
    history = _conversation_for_prospect(db, prospect.id)
    hp = _conversation_payload(history)
    digest = "\n".join(
        f"{x['sender_type']}/{x['direction']}: {(x.get('message') or '')[:240]}" for x in hp[-20:]
    ) or "(vacío)"
    inbound = next((m for m in reversed(history) if m.direction == "inbound"), None)
    if inbound:
        sig = conversation_intelligence.classify_inbound_full(
            inbound_text=inbound.message,
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
        prospect.status = conversation_intelligence.prospect_status_from_inbound_signals(
            prospect.status, sig
        )
        pipeline_sync.sync_pipeline_from_status(prospect)
        if prospect.status == ProspectStatus.meeting_booked.value:
            ensure_simulated_meeting_for_booked_prospect(db, campaign, prospect)
        from app.services import prospect_commercial_state as pcs

        pcs.sync_commercial_state_from_inbound(
            db,
            prospect=prospect,
            inbound_text=inbound.message,
            sig=sig,
            testing=False,
        )
    _refresh_conversation_scores(db, prospect, campaign)
    db.commit()
    return ProspectReanalysisRead(
        prospect_id=prospect.id,
        status=prospect.status,
        interest_probability=int(prospect.interest_probability or 0),
        objection_type=prospect.objection_type,
        next_best_action=prospect.next_best_action,
        score_reason=prospect.score_reason,
    )


@router.post(
    "/campaigns/{campaign_id}/outreach/run-scheduled-followups",
    response_model=ScheduledFollowupRunResponse,
)
def run_scheduled_followups(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> ScheduledFollowupRunResponse:
    """Ejecuta tareas follow-up vencidas (manual hoy → cron/worker después)."""
    blob = campaign_education_blob(db, campaign)
    deferred_resumed = mseq.process_due_deferred_resume_tasks(db, campaign)
    stats = followup_engine.run_due_followups_for_campaign(db, campaign.id, education=blob)
    db.commit()
    return ScheduledFollowupRunResponse(deferred_resumed=deferred_resumed, **stats)


@router.get(
    "/prospects/{prospect_id}/conversation",
    response_model=list[OutreachMessageRead],
)
def get_prospect_conversation(
    prospect_id: int,
    db: Session = Depends(get_db),
    _: Prospect = Depends(get_prospect),
) -> list[OutreachMessageRead]:
    rows = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect_id)
        .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
    ).all()
    return [_serialize_message(r) for r in rows]


@router.get(
    "/prospects/{prospect_id}/conversation-workspace",
    response_model=ProspectConversationWorkspaceRead,
)
def get_prospect_conversation_workspace(
    prospect_id: int,
    include_testing: bool = True,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectConversationWorkspaceRead:
    from app.services.conversation_workspace import build_conversation_workspace

    data = build_conversation_workspace(
        db,
        prospect=prospect,
        include_testing=include_testing,
    )
    return ProspectConversationWorkspaceRead.model_validate(data)


@router.post(
    "/prospects/{prospect_id}/linkedin-assisted/prepare",
    response_model=LinkedInAssistedPrepareRead,
)
def prepare_linkedin_assisted_message(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInAssistedPrepareRead:
    try:
        linkedin_assisted_service.require_real_linkedin(prospect)
        campaign = linkedin_assisted_service._load_campaign(db, prospect)
        draft = linkedin_assisted_service.ensure_linkedin_draft(db, prospect, campaign)
        db.commit()
        db.refresh(prospect)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return LinkedInAssistedPrepareRead(
        message=draft,
        linkedin_url=(prospect.linkedin_url or "").strip() or None,
        assist_status=linkedin_assisted_service.read_assist_status(prospect),
        session_id=getattr(prospect, "linkedin_assist_session_id", None),
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-assisted/assist",
    response_model=LinkedInAssistedAssistRead,
)
def begin_linkedin_assisted_session(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInAssistedAssistRead:
    """Abre sesión asistida: borrador + log de apertura/copia. NO marca enviado."""
    try:
        linkedin_assisted_service.require_real_linkedin(prospect)
        campaign = linkedin_assisted_service._load_campaign(db, prospect)
        draft, session_id = linkedin_assisted_service.begin_assist_session(
            db, prospect, campaign
        )
        db.commit()
        db.refresh(prospect)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return LinkedInAssistedAssistRead(
        message=draft,
        linkedin_url=(prospect.linkedin_url or "").strip() or None,
        clipboard_ready=True,
        detail="Sesión iniciada. Revisá en LinkedIn y confirmá solo después de enviar.",
        assist_status=linkedin_assisted_service.read_assist_status(prospect),
        session_id=session_id,
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-assisted/abandon",
    response_model=LinkedInAssistedAbandonRead,
)
def abandon_linkedin_assisted_session(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInAssistedAbandonRead:
    """Cierra sesión sin confirmar envío; mantiene la notificación pendiente."""
    if not (prospect.linkedin_assisted_draft or "").strip():
        return LinkedInAssistedAbandonRead(ok=True, detail="Sin borrador pendiente.")
    try:
        campaign = linkedin_assisted_service._load_campaign(db, prospect)
        status = linkedin_assisted_service.abandon_assist_session(db, prospect, campaign)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return LinkedInAssistedAbandonRead(
        ok=True,
        detail="Sesión cerrada sin confirmar envío. La notificación sigue pendiente.",
        assist_status=status,
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-assisted/mark-sent",
    response_model=LinkedInAssistedMarkSentRead,
)
def mark_linkedin_assisted_sent(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInAssistedMarkSentRead:
    if not (prospect.linkedin_assisted_draft or "").strip():
        raise HTTPException(
            status_code=400,
            detail="No hay mensaje LinkedIn pendiente. Usá «Abrir LinkedIn» primero.",
        )
    try:
        linkedin_assisted_service.require_real_linkedin(prospect)
        detail = linkedin_assisted_service.confirm_linkedin_sent(db, prospect)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.refresh(prospect)
    return LinkedInAssistedMarkSentRead(
        ok=True,
        detail=detail,
        assist_status=linkedin_assisted_service.read_assist_status(prospect),
        session_id=None,
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-assisted/mark-connect-sent",
    response_model=LinkedInConnectSentRead,
)
def mark_linkedin_connect_sent(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInConnectSentRead:
    """El SDR envió la solicitud de conexión: pasa a esperar aceptación."""
    try:
        detail = linkedin_assisted_service.mark_connect_sent(db, prospect)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.refresh(prospect)
    return LinkedInConnectSentRead(
        ok=True,
        detail=detail,
        connection_status=linkedin_assisted_service.read_connection_status(prospect),
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-profile-urn",
    response_model=LinkedInProfileUrnRead,
)
def save_linkedin_profile_urn(
    prospect_id: int,
    body: LinkedInProfileUrnBody,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInProfileUrnRead:
    """
    Guarda el URN fsd_profile del prospecto (aprendido del botón Mensaje).
    Con ese URN Nexus arma /messaging/compose?... para cualquier contacto.
    """
    try:
        urn = linkedin_assisted_service.save_linkedin_profile_urn(
            prospect,
            urn=body.urn,
            compose_url=body.compose_url,
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.refresh(prospect)
    compose = linkedin_assisted_service.build_linkedin_compose_url(urn) or ""
    return LinkedInProfileUrnRead(
        ok=True,
        prospect_id=prospect.id,
        linkedin_profile_urn=urn,
        compose_url=compose,
        detail="URN LinkedIn guardado. Próximos envíos abren el chat directo.",
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-connection-status",
    response_model=LinkedInConnectionStatusRead,
)
def report_linkedin_connection_status(
    prospect_id: int,
    body: LinkedInConnectionStatusBody,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInConnectionStatusRead:
    """
    La extensión reporta el estado de conexión detectado (grado 1º = conectado).
    Al conectar, Nexus deja preparado el mensaje post-aceptación para enviar.
    """
    try:
        status, draft = linkedin_assisted_service.apply_connection_status(
            db, prospect, body.status
        )
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.refresh(prospect)
    text = (draft or "").strip()
    return LinkedInConnectionStatusRead(
        ok=True,
        detail=(
            "Conexión aceptada. Mensaje listo para enviar."
            if status == "connected"
            else "Estado de conexión actualizado."
        ),
        connection_status=status,
        message_ready=bool(text),
        message=text or None,
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-assisted/regenerate-reply",
    response_model=LinkedInAssistedRegenerateRead,
)
def regenerate_linkedin_assisted_reply(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInAssistedRegenerateRead:
    """Regenera borrador de réplica LinkedIn tras inbound (OpenAI si está configurada)."""
    try:
        linkedin_assisted_service.require_real_linkedin(prospect)
        campaign = linkedin_assisted_service._load_campaign(db, prospect)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    from app.services import openai_service

    try:
        draft = linkedin_assisted_service.regenerate_linkedin_reply_draft(
            db, prospect, campaign
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    db.commit()
    db.refresh(prospect)
    text = (draft or prospect.linkedin_assisted_draft or "").strip()
    return LinkedInAssistedRegenerateRead(
        ok=True,
        message=text,
        assist_status=linkedin_assisted_service.read_assist_status(prospect),
        openai_used=openai_service.openai_configured(),
        detail=(
            "Borrador regenerado con IA."
            if openai_service.openai_configured()
            else "Borrador regenerado (modo consultivo sin OpenAI)."
        ),
    )


@router.get(
    "/prospects/resolve-linkedin",
    response_model=LinkedInResolveProspectRead,
)
def resolve_linkedin_prospect(
    url: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LinkedInResolveProspectRead:
    """Resuelve prospect_id a partir de una URL de perfil LinkedIn (extensión inbound)."""
    prospect = linkedin_assisted_service.resolve_prospect_by_linkedin_url(
        db,
        company_id=user.company_id,
        url=url,
    )
    if prospect is None:
        raise HTTPException(status_code=404, detail="Prospecto no encontrado para esta URL de LinkedIn.")
    try:
        linkedin_assisted_service.require_real_linkedin(prospect)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return LinkedInResolveProspectRead(
        prospect_id=prospect.id,
        prospect_name=prospect.name or f"Prospecto #{prospect.id}",
        company_name=prospect.company_name,
        linkedin_url=(prospect.linkedin_url or url).strip(),
        campaign_id=prospect.campaign_id,
    )


@router.post(
    "/prospects/{prospect_id}/linkedin-inbound",
    response_model=LinkedInInboundRegisterRead,
)
def register_linkedin_inbound(
    prospect_id: int,
    body: LinkedInInboundRegisterBody,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> LinkedInInboundRegisterRead:
    """Registra respuesta inbound de LinkedIn (extensión o pegado manual del SDR)."""
    if not prospect.campaign_id:
        raise HTTPException(status_code=400, detail="El prospecto no tiene campaña asignada.")
    campaign = db.get(Campaign, int(prospect.campaign_id))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada.")
    try:
        linkedin_assisted_service.require_real_linkedin(prospect)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = register_linkedin_inbound_message(
        db,
        prospect=prospect,
        campaign=campaign,
        message=body.message.strip(),
        linkedin_message_id=body.linkedin_message_id,
        prepare_reply_draft=True,
    )
    db.commit()
    db.refresh(prospect)

    inserted = bool(result.get("inserted"))
    reply_at = result.get("reply_available_at")
    if inserted and reply_at and result.get("reply_draft_ready"):
        detail = "Respuesta LinkedIn registrada. La réplica aparecerá en cola en unos minutos."
    elif inserted:
        detail = "Respuesta LinkedIn registrada. Revisá la cola para responder."
    else:
        detail = "Ese mensaje ya estaba registrado."
    return LinkedInInboundRegisterRead(
        ok=True,
        inserted=inserted,
        duplicate=not inserted,
        sequence_paused=bool(result.get("sequence_paused")),
        reply_draft_ready=bool(result.get("reply_draft_ready")),
        reply_draft=result.get("reply_draft"),
        reply_available_at=reply_at,
        detail=detail,
    )


@router.get("/prospects/resolve-whatsapp", response_model=WhatsAppResolveProspectRead)
def resolve_whatsapp_prospect(
    phone: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WhatsAppResolveProspectRead:
    """Resuelve prospecto por teléfono (extensión WhatsApp Web / auto-inbound)."""
    prospect = resolve_prospect_by_whatsapp_digits(
        db,
        company_id=user.company_id,
        from_digits=phone,
    )
    if prospect is None:
        raise HTTPException(status_code=404, detail="Prospecto no encontrado para este teléfono.")
    digits = whatsapp_assisted_service.prospect_whatsapp_digits(prospect) or phone
    return WhatsAppResolveProspectRead(
        prospect_id=prospect.id,
        prospect_name=prospect.name or f"Prospecto #{prospect.id}",
        company_name=prospect.company_name,
        phone_digits=digits,
        campaign_id=prospect.campaign_id,
    )


@router.post(
    "/prospects/{prospect_id}/whatsapp-inbound",
    response_model=WhatsAppInboundRegisterRead,
)
def register_whatsapp_inbound(
    prospect_id: int,
    body: WhatsAppInboundRegisterBody,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> WhatsAppInboundRegisterRead:
    """Registra respuesta inbound de WhatsApp (webhook Meta o extensión Web)."""
    if not prospect.campaign_id:
        raise HTTPException(status_code=400, detail="El prospecto no tiene campaña asignada.")
    campaign = db.get(Campaign, int(prospect.campaign_id))
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada.")
    try:
        whatsapp_assisted_service.require_whatsapp_phone(prospect)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    from sqlalchemy.exc import IntegrityError, PendingRollbackError

    try:
        result = register_whatsapp_inbound_message(
            db,
            prospect=prospect,
            campaign=campaign,
            message=body.message.strip(),
            whatsapp_message_id=body.whatsapp_message_id,
            prepare_reply_draft=bool(body.prepare_reply_draft),
        )
        db.commit()
        db.refresh(prospect)
    except (IntegrityError, PendingRollbackError):
        db.rollback()
        # Race del unique index u sesión dirty tras dedupe: tratar como duplicado OK.
        return WhatsAppInboundRegisterRead(
            ok=True,
            inserted=False,
            duplicate=True,
            sequence_paused=bool(getattr(prospect, "sequence_paused", False)),
            reply_draft_ready=False,
            reply_draft=None,
            detail="Ese mensaje ya estaba registrado.",
        )
    except Exception as e:
        db.rollback()
        logger.exception("whatsapp-inbound failed prospect_id=%s", prospect_id)
        raise HTTPException(
            status_code=500,
            detail=f"whatsapp inbound failed: {str(e)[:180]}",
        ) from e

    if result.get("echo_ignored"):
        return WhatsAppInboundRegisterRead(
            ok=True,
            inserted=False,
            duplicate=False,
            sequence_paused=bool(result.get("sequence_paused")),
            reply_draft_ready=False,
            reply_draft=None,
            detail="Ignorado: eco del mensaje propio.",
        )

    inserted = bool(result.get("inserted"))
    calendar_reconnect = bool(result.get("calendar_reconnect_required"))
    operator_message = (result.get("operator_message") or "").strip() or None
    if calendar_reconnect:
        detail = operator_message or (
            "Google Calendar necesita reconexión. "
            "Andá a Configuración → Integraciones antes de confirmar la reunión."
        )
    elif inserted and result.get("reply_draft_ready"):
        detail = "Respuesta WhatsApp detectada. Réplica lista en la cola para enviar."
    elif inserted:
        detail = "Respuesta WhatsApp detectada."
    else:
        detail = "Ese mensaje ya estaba registrado."
    return WhatsAppInboundRegisterRead(
        ok=True,
        inserted=inserted,
        duplicate=not inserted,
        sequence_paused=bool(result.get("sequence_paused")),
        reply_draft_ready=bool(result.get("reply_draft_ready")),
        reply_draft=result.get("reply_draft"),
        calendar_reconnect_required=calendar_reconnect,
        operator_message=operator_message,
        detail=detail,
    )


@router.get(
    "/campaigns/{campaign_id}/linkedin-assisted/queue",
    response_model=LinkedInAssistQueueRead,
)
def linkedin_assisted_queue(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    user: User = Depends(get_current_user),
) -> LinkedInAssistQueueRead:
    return linkedin_assisted_service.build_campaign_queue(db, campaign_id, viewer=user)


@router.post(
    "/prospects/{prospect_id}/whatsapp-assisted/assist",
    response_model=WhatsAppAssistedAssistRead,
)
def begin_whatsapp_assisted_session(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> WhatsAppAssistedAssistRead:
    """Abre sesión asistida WhatsApp Web. NO marca enviado."""
    try:
        campaign = whatsapp_assisted_service._load_campaign(db, prospect)
        draft, session_id, phone = whatsapp_assisted_service.begin_assist_session(
            db, prospect, campaign
        )
        db.commit()
        db.refresh(prospect)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    send_url = whatsapp_assisted_service.wa_web_send_url(phone, draft)
    return WhatsAppAssistedAssistRead(
        message=draft,
        phone_digits=phone,
        send_url=send_url,
        app_send_url=whatsapp_assisted_service.wa_app_send_url(phone, draft),
        desktop_protocol_url=whatsapp_assisted_service.wa_desktop_protocol_url(phone, draft),
        clipboard_ready=True,
        detail="Sesión iniciada. Revisá en WhatsApp (Web o app) y confirmá solo después de enviar.",
        assist_status=whatsapp_assisted_service.read_assist_status(prospect),
        session_id=session_id,
    )


@router.post(
    "/prospects/{prospect_id}/whatsapp-assisted/abandon",
    response_model=WhatsAppAssistedAbandonRead,
)
def abandon_whatsapp_assisted_session(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> WhatsAppAssistedAbandonRead:
    if not (prospect.whatsapp_assisted_draft or "").strip():
        return WhatsAppAssistedAbandonRead(ok=True, detail="Sin borrador pendiente.")
    try:
        campaign = whatsapp_assisted_service._load_campaign(db, prospect)
        status = whatsapp_assisted_service.abandon_assist_session(db, prospect, campaign)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return WhatsAppAssistedAbandonRead(
        ok=True,
        detail="Sesión cerrada sin confirmar envío. La notificación sigue pendiente.",
        assist_status=status,
    )


@router.post(
    "/prospects/{prospect_id}/whatsapp-assisted/mark-sent",
    response_model=WhatsAppAssistedMarkSentRead,
)
def mark_whatsapp_assisted_sent(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> WhatsAppAssistedMarkSentRead:
    draft = (prospect.whatsapp_assisted_draft or "").strip()
    if not draft:
        status = whatsapp_assisted_service.read_assist_status(prospect)
        if status == whatsapp_assisted_service.STATUS_SENT or getattr(
            prospect, "whatsapp_sdr_marked_sent_at", None
        ):
            return WhatsAppAssistedMarkSentRead(
                ok=True,
                detail="Envío ya confirmado en WhatsApp.",
                assist_status=status,
                session_id=None,
            )
        raise HTTPException(
            status_code=400,
            detail="No hay mensaje WhatsApp pendiente. Usá «Enviar WhatsApp» primero.",
        )
    try:
        detail = whatsapp_assisted_service.confirm_whatsapp_sent(db, prospect)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.refresh(prospect)
    return WhatsAppAssistedMarkSentRead(
        ok=True,
        detail=detail,
        assist_status=whatsapp_assisted_service.read_assist_status(prospect),
        session_id=None,
    )


@router.get(
    "/campaigns/{campaign_id}/whatsapp-assisted/queue",
    response_model=WhatsAppAssistQueueRead,
)
def whatsapp_assisted_queue(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    user: User = Depends(get_current_user),
) -> WhatsAppAssistQueueRead:
    return whatsapp_assisted_service.build_campaign_queue(db, campaign_id, viewer=user)


@router.get(
    "/campaigns/{campaign_id}/call-assisted/queue",
    response_model=CallAssistQueueRead,
)
def call_assisted_queue(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    user: User = Depends(get_current_user),
) -> CallAssistQueueRead:
    return call_assisted_service.build_campaign_queue(db, campaign_id, viewer=user)


@router.post(
    "/prospects/{prospect_id}/call-assisted/mark-done",
    response_model=CallAssistMarkDoneRead,
)
def mark_call_assisted_done(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> CallAssistMarkDoneRead:
    brief = (prospect.call_assisted_brief or "").strip()
    if not brief:
        status = call_assisted_service.read_assist_status(prospect)
        if status == call_assisted_service.STATUS_DONE or getattr(
            prospect, "call_sdr_marked_done_at", None
        ):
            return CallAssistMarkDoneRead(
                ok=True,
                detail="Llamada ya confirmada.",
                assist_status=status,
            )
        raise HTTPException(
            status_code=400,
            detail="No hay llamada pendiente para este prospecto.",
        )
    try:
        detail = call_assisted_service.confirm_call_done(db, prospect)
        db.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.refresh(prospect)
    return CallAssistMarkDoneRead(
        ok=True,
        detail=detail,
        assist_status=call_assisted_service.read_assist_status(prospect),
    )


@router.get(
    "/campaigns/{campaign_id}/mail-queue",
    response_model=MailQueueRead,
)
def campaign_mail_queue(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    user: User = Depends(get_current_user),
) -> MailQueueRead:
    """Mails enviados de la campaña (notificación; sin acciones de envío)."""
    del campaign  # auth via get_campaign
    return mail_queue_service.build_campaign_mail_queue(db, campaign_id, viewer=user)


@router.get(
    "/companies/{company_id}/linkedin-assisted/pending-connect-checks",
    response_model=LinkedInPendingConnectChecksRead,
)
def linkedin_pending_connect_checks(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LinkedInPendingConnectChecksRead:
    """Lista para la extensión: verificar 1º grado sola (sin clic del SDR)."""
    if int(user.company_id) != int(company_id):
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    items = linkedin_assisted_service.list_pending_connect_checks(
        db, company_id=company_id, limit=1
    )
    return LinkedInPendingConnectChecksRead(
        items=[LinkedInPendingConnectCheckRead(**row) for row in items],
        total=len(items),
    )


@router.get(
    "/campaigns/{campaign_id}/linkedin-assisted/summary",
    response_model=LinkedInAssistedSummaryRead,
)
def linkedin_assisted_campaign_summary(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    user: User = Depends(get_current_user),
) -> LinkedInAssistedSummaryRead:
    from app.services.campaign_visibility import filter_prospects_for_viewer

    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
    rows = filter_prospects_for_viewer(user, campaign, list(rows))
    today = datetime.now(UTC).date()
    ready = 0
    drafts = 0
    replies_style = 0
    sent_today = 0
    for p in rows:
        if (p.linkedin_url or "").strip():
            if p.status in {
                ProspectStatus.compatible.value,
                ProspectStatus.contacted.value,
                ProspectStatus.replied.value,
                ProspectStatus.interested.value,
            }:
                ready += 1
        if (getattr(p, "linkedin_assisted_draft", None) or "").strip():
            drafts += 1
        if p.status in {ProspectStatus.replied.value, ProspectStatus.interested.value}:
            replies_style += 1
        ts = getattr(p, "linkedin_sdr_marked_sent_at", None)
        if ts is not None and ts.date() == today:
            sent_today += 1

    n = len(rows)
    if n > 80:
        risk = "alto"
    elif n > 40:
        risk = "medio"
    else:
        risk = "bajo"

    queue = linkedin_assisted_service.build_campaign_queue(db, campaign_id, viewer=user)

    return LinkedInAssistedSummaryRead(
        ready_for_linkedin=ready,
        prospects_with_draft=drafts,
        replies_pending_style=replies_style,
        marked_sent_today=sent_today,
        pending_queue=queue.total_pending,
        risk_level=risk,
    )


@router.get("/companies/{company_id}/responder-inbox")
def get_responder_inbox(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Bandeja unificada: borradores de respuesta email + LinkedIn + WhatsApp."""
    from app.core.permissions import normalize_role
    from app.models.enums import UserRole
    from app.services.responder_inbox_service import build_responder_inbox

    if int(user.company_id) != int(company_id):
        raise HTTPException(status_code=403, detail="Empresa no autorizada")
    role = normalize_role(user.role)
    seller_id = int(user.id) if role == UserRole.sdr else None
    return build_responder_inbox(db, company_id=company_id, seller_id=seller_id)
