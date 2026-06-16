"""Agente comercial Nexus — auto-respuesta a inbound con reglas de escalación."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services import multichannel_sequence as mseq
from app.services import pipeline_sync
from app.services.outreach_simulation import make_message

logger = logging.getLogger(__name__)

CONV_AUTO_ACTIVE = "conversacion_automatica_activa"
CONV_WAITING = "esperando_respuesta"
CONV_MEETING = "reunion_conseguida"
CONV_ESCALATED = "derivado_sdr"
CONV_NOT_INTERESTED = "no_interesado"
CONV_NONE = "sin_conversacion"

CONVERSATION_STATE_LABELS: dict[str, str] = {
    CONV_AUTO_ACTIVE: "Conversación automática activa",
    CONV_WAITING: "Esperando respuesta del prospecto",
    CONV_MEETING: "Reunión conseguida",
    CONV_ESCALATED: "Derivado a SDR",
    CONV_NOT_INTERESTED: "No interesado",
    CONV_NONE: "Sin conversación",
}

AUTO_SEND_CONFIDENCE_MIN = 0.62

_COMMERCIAL_NEGOTIATION_MARKERS: tuple[str, ...] = (
    "precio",
    "costo",
    "descuento",
    "cotizacion",
    "contrato",
    "condiciones comerciales",
    "terminos",
    "factura",
    "licencia anual",
    "propuesta comercial",
    "negociar",
    "presupuesto",
    "tarifa",
    "plan enterprise",
    "sla",
    "garantia",
)

_COMPLEX_PRODUCT_MARKERS: tuple[str, ...] = (
    "integracion con",
    "api custom",
    "caso de uso especifico",
    "arquitectura",
    "compliance",
    "gdpr",
    "soc 2",
    "implementacion on premise",
)

SIMULATE_AUTO_MARKER = "[auto-reply:simulate:"


def conversation_state_label(state: str | None) -> str:
    key = (state or CONV_NONE).strip().lower()
    return CONVERSATION_STATE_LABELS.get(key, key.replace("_", " ").title())


def simulate_auto_reply_marker(inbound_message_id: int) -> str:
    return f"{SIMULATE_AUTO_MARKER}{inbound_message_id}]"


def strip_auto_reply_marker(text: str) -> str:
    body = (text or "").strip()
    if body.startswith(SIMULATE_AUTO_MARKER):
        parts = body.split("\n\n", 1)
        return parts[1].strip() if len(parts) > 1 else body
    return body


def estimate_classification_confidence(
    *,
    text: str,
    sig: ci.InboundSignals,
    response_class: str,
) -> float:
    """Confianza heurística 0–1 para decidir auto-envío."""
    rc = (response_class or "desconocido").strip().lower()
    base_map = {
        "interesado": 0.72,
        "no_interesado": 0.88,
        "pedir_mas_info": 0.74,
        "derivar_a_otra_persona": 0.86,
        "contactar_mas_adelante": 0.82,
        "respuesta_automatica": 0.55,
        "desconocido": 0.35,
    }
    score = base_map.get(rc, 0.4)

    if sig.explicit_meeting_commitment or sig.prospect_wants_meeting:
        score = min(0.98, score + 0.15)
    if sig.interest_level == "high":
        score = min(0.96, score + 0.12)
    elif sig.interest_level == "medium":
        score = min(0.92, score + 0.06)
    elif sig.interest_level == "low" and rc == "interesado":
        score = max(0.5, score - 0.1)

    if sig.objection_type and rc not in ("no_interesado", "contactar_mas_adelante"):
        score = max(0.45, score - 0.08)

    if rc == "desconocido" and len((text or "").strip()) >= 20:
        score = max(score, 0.42)

    if ci.inbound_requests_meeting_or_demo(text) and rc == "interesado":
        score = min(0.97, score + 0.1)

    return round(max(0.0, min(1.0, score)), 3)


def detect_escalation_reason(
    *,
    text: str,
    sig: ci.InboundSignals,
    response_class: str,
    confidence: float,
) -> str | None:
    """Motivo para derivar a SDR en lugar de auto-responder."""
    norm = ci.fold_accents(ci.normalize_inbound_text_for_classification(text or ""))
    rc = (response_class or "").strip().lower()

    if confidence < AUTO_SEND_CONFIDENCE_MIN:
        return "baja confianza de clasificación"

    if rc == "derivar_a_otra_persona":
        return "derivación a otra persona — requiere SDR"

    if rc == "desconocido":
        return "intención no reconocida con suficiente certeza"

    if rc == "respuesta_automatica":
        return "respuesta automática del prospecto — revisar manualmente"

    for marker in _COMMERCIAL_NEGOTIATION_MARKERS:
        if marker not in norm:
            continue
        if marker in ("precio", "costo", "descuento", "cotizacion"):
            return "negociación de precios o descuentos"
        if marker in ("contrato", "condiciones comerciales", "terminos"):
            return "consulta sobre contratos o condiciones comerciales"
        return f"consulta comercial sensible ({marker})"

    if any(m in norm for m in _COMPLEX_PRODUCT_MARKERS):
        return "consulta técnica o comercial compleja"

    if sig.asks_concrete_questions:
        from app.services.openai_service import inbound_text_needs_substantive_answer

        if inbound_text_needs_substantive_answer(text):
            complex_q = ("legal", "juridico", "implementacion", "personalizado", "custom")
            if any(q in norm for q in complex_q):
                return "consulta compleja fuera del alcance automático"

    from app.services.inbound_auto_reply import should_force_draft_only

    if should_force_draft_only(sig, text) and rc not in (
        "interesado",
        "no_interesado",
        "contactar_mas_adelante",
        "pedir_mas_info",
    ):
        return "mensaje ambiguo — requiere criterio humano"

    return None


def simulation_reply_needs_openai(
    *,
    inbound_text: str,
    campaign: Campaign,
    reply_objective: str | None,
    escalation_reason: str | None,
) -> bool:
    """True si la simulación necesita generate_inbound_response antes de auto-enviar."""
    if escalation_reason:
        return False

    from app.services.meeting_slot_parser import parse_meeting_slot, prospect_proposed_meeting_slot
    from app.services import conversation_intelligence as ci

    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    if prospect_proposed_meeting_slot(inbound_text) or parse_meeting_slot(
        inbound_text, timezone=tz
    ):
        return False
    if ci.inbound_requests_meeting_or_demo(inbound_text) and ci.inbound_has_explicit_meeting_slot(
        inbound_text
    ):
        return False

    obj = (reply_objective or "").strip().lower()
    cal_link = (getattr(campaign, "calendar_link", None) or "").strip()
    if obj == "agendar" and cal_link:
        return False

    return True


def resolve_conversation_state_after_turn(
    *,
    response_class: str,
    reply_objective: str,
    escalated: bool,
    auto_sent: bool,
    sig: ci.InboundSignals,
    prospect_status: str | None = None,
) -> str:
    rc = (response_class or "").strip().lower()
    status = (prospect_status or "").strip().lower()
    if escalated:
        return CONV_ESCALATED
    if rc == "no_interesado":
        return CONV_NOT_INTERESTED
    if status == "meeting_booked":
        return CONV_MEETING
    if auto_sent:
        return CONV_WAITING
    if rc in ("interesado", "pedir_mas_info", "contactar_mas_adelante"):
        return CONV_AUTO_ACTIVE
    return CONV_AUTO_ACTIVE


def process_inbound_turn(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound: OutreachMessage,
    inbound_text: str,
    channel: str,
    sig: ci.InboundSignals,
    response_class: str,
    response_class_label: str,
    reply_objective: str,
    reply_objective_label: str,
    suggested_reply: str,
    testing: bool = True,
    simulate_delivery: bool = True,
) -> dict[str, Any]:
    """
    Decide auto-envío vs escalación, persiste outbound simulado y actualiza estado de conversación.
    En modo simulación no requiere Gmail/LinkedIn/WhatsApp real.
    """
    confidence = estimate_classification_confidence(
        text=inbound_text,
        sig=sig,
        response_class=response_class,
    )
    escalation_reason = detect_escalation_reason(
        text=inbound_text,
        sig=sig,
        response_class=response_class,
        confidence=confidence,
    )

    followup_engine.record_prospect_inbound(db, prospect)
    followup_engine.apply_inbound_signals(
        db,
        prospect,
        objection_type=sig.objection_type,
        interest_level=sig.interest_level or "low",
    )
    prospect.status = ci.prospect_status_from_inbound_signals(prospect.status, sig)
    pipeline_sync.sync_pipeline_from_status(prospect)
    mseq.promote_operational_group_after_prospect_reply(prospect)

    if sig.objection_type == "not_interested":
        mseq.mark_encajonado(prospect)
    elif ci.timing_deferral_should_apply(sig, inbound_text=inbound_text):
        if not ci.inbound_requests_meeting_or_demo(inbound_text):
            resume = ci.infer_defer_resume_utc(
                inbound_text=inbound_text,
                defer_iso=sig.defer_resume_at_iso,
                now=datetime.now(UTC),
            )
            mseq.apply_prospect_timing_deferral(
                db,
                prospect,
                campaign,
                defer_resume_at=resume,
            )

    from app.services.meeting_booking import (
        attempt_auto_book_from_message,
        prepare_agendar_reply_with_calendar_link,
    )

    meeting_booking: dict[str, Any] | None = attempt_auto_book_from_message(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text=inbound_text,
        reply_objective=reply_objective,
        sig=sig,
        testing=testing,
    )

    reply_body = (suggested_reply or "").strip()
    if meeting_booking and meeting_booking.get("confirmation_reply"):
        reply_body = str(meeting_booking["confirmation_reply"])
    elif meeting_booking and meeting_booking.get("booking_failed_reply"):
        reply_body = str(meeting_booking["booking_failed_reply"])
    elif meeting_booking and meeting_booking.get("alternatives_reply"):
        reply_body = str(meeting_booking["alternatives_reply"])
    else:
        reply_body = prepare_agendar_reply_with_calendar_link(
            prospect=prospect,
            campaign=campaign,
            reply_objective=reply_objective,
            suggested_reply=reply_body,
        )

    outbound: OutreachMessage | None = None
    delivery_mode: Literal["auto_sent", "escalated"] = "escalated"
    auto_sent = False
    reply_unavailable = reply_body.startswith("[Sugerencia no disponible")

    if (
        simulate_delivery
        and not escalation_reason
        and reply_body
        and not reply_unavailable
    ):
        body = f"{simulate_auto_reply_marker(inbound.id)}\n\n{reply_body}"
        outbound = make_message(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="ai",
            message=body,
            channel=channel,
            direction="outbound",
            is_testing=testing,
        )
        db.add(outbound)
        db.flush()
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=getattr(campaign, "calendar_link", None),
            outbound_text=reply_body,
        )
        delivery_mode = "auto_sent"
        auto_sent = True
        logger.info(
            "commercial_agent auto_sent prospect_id=%s inbound_id=%s channel=%s confidence=%.3f",
            prospect.id,
            inbound.id,
            channel,
            confidence,
        )
    elif escalation_reason:
        logger.info(
            "commercial_agent escalated prospect_id=%s reason=%s confidence=%.3f",
            prospect.id,
            escalation_reason,
            confidence,
        )

    conv_state = resolve_conversation_state_after_turn(
        response_class=response_class,
        reply_objective=reply_objective,
        escalated=bool(escalation_reason),
        auto_sent=auto_sent,
        sig=sig,
        prospect_status=prospect.status,
    )
    if meeting_booking and meeting_booking.get("calendar_created"):
        conv_state = CONV_MEETING
    prospect.conversation_state = conv_state

    return {
        "delivery_mode": delivery_mode,
        "auto_sent": auto_sent,
        "classification_confidence": confidence,
        "escalation_reason": escalation_reason,
        "conversation_state": conv_state,
        "conversation_state_label": conversation_state_label(conv_state),
        "outbound_message_id": outbound.id if outbound else None,
        "outbound_message": outbound,
        "channel": channel,
        "response_class": response_class,
        "response_class_label": response_class_label,
        "reply_objective": reply_objective,
        "reply_objective_label": reply_objective_label,
        "inbound_message_id": inbound.id,
        "inbound_text": inbound_text,
        "meeting_booking": meeting_booking,
        "saved_to_db": True,
    }
