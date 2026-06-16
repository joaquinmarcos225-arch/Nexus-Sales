from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.deps import get_campaign, get_prospect
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
    LinkedInAssistedSummaryRead,
)
from app.services import linkedin_assisted_service
from app.schemas.outreach import (
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


def _stats_for_campaign(db: Session, campaign_id: int) -> OutreachStats:
    """Métricas alineadas a actividad en mensajes (Gmail, borradores, IA) — no solo status legacy."""
    rows = db.execute(
        select(Prospect.status, func.count(Prospect.id))
        .where(Prospect.campaign_id == campaign_id)
        .group_by(Prospect.status)
    ).all()
    count_map = {status: count for status, count in rows}

    if om.is_real_mode():
        touched = om.distinct_prospects_with_real_gmail_outbound_campaign(db, campaign_id)
        with_inbound = om.distinct_prospects_with_real_gmail_inbound_campaign(db, campaign_id)
    else:
        touched = om.distinct_prospects_with_outbound_campaign(db, campaign_id)
        with_inbound = om.distinct_prospects_with_inbound_campaign(db, campaign_id)

    return OutreachStats(
        contacted=touched,
        responded=with_inbound,
        interested=count_map.get(ProspectStatus.interested.value, 0),
        not_interested=count_map.get(ProspectStatus.not_interested.value, 0),
        failed=count_map.get(ProspectStatus.failed.value, 0),
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
    ch = _campaign_allowed_channels_list(campaign)
    prio = " → ".join(ch)
    return {
        "name": campaign.name,
        "tone": campaign.tone,
        "target_role": campaign.target_role or "",
        "target_industry": campaign.target_industry or "",
        "target_country": campaign.target_country or "",
        "preferred_channel_hint": prio,
        "allowed_channels_csv": ",".join(ch),
        "calendar_link": campaign.calendar_link or "",
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
    )


@router.get("/campaigns/{campaign_id}/outreach", response_model=OutreachCampaignRead)
def get_campaign_outreach(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> OutreachCampaignRead:
    seq = _get_or_create_sequence(db, campaign_id, create_if_missing=False)
    if seq is None:
        seq = OutreachSequence(campaign_id=campaign_id, is_running=False, current_step=0)
    last_messages = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.campaign_id == campaign_id)
        .order_by(OutreachMessage.created_at.desc())
        .limit(20)
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
    return OutreachCampaignRead(
        sequence=OutreachSequenceRead.model_validate(seq),
        stats=_stats_for_campaign(db, campaign_id),
        last_messages=[_serialize_message(m) for m in last_messages],
        pending_operational_tasks=pending_ops,
        real_mode=om.is_real_mode(),
        simulation_disabled=om.is_outreach_simulation_disabled(),
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


@router.get(
    "/campaigns/{campaign_id}/linkedin-assisted/queue",
    response_model=LinkedInAssistQueueRead,
)
def linkedin_assisted_queue(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> LinkedInAssistQueueRead:
    return linkedin_assisted_service.build_campaign_queue(db, campaign_id)


@router.get(
    "/campaigns/{campaign_id}/linkedin-assisted/summary",
    response_model=LinkedInAssistedSummaryRead,
)
def linkedin_assisted_campaign_summary(
    campaign_id: int,
    db: Session = Depends(get_db),
    _: Campaign = Depends(get_campaign),
) -> LinkedInAssistedSummaryRead:
    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
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

    queue = linkedin_assisted_service.build_campaign_queue(db, campaign_id)

    return LinkedInAssistedSummaryRead(
        ready_for_linkedin=ready,
        prospects_with_draft=drafts,
        replies_pending_style=replies_style,
        marked_sent_today=sent_today,
        pending_queue=queue.total_pending,
        risk_level=risk,
    )
