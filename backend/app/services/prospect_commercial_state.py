"""Estado comercial del prospecto — capa sobre ownership y secuencia."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.enums import PipelineStage, ProspectStatus
from app.models.prospect import Prospect

COMMERCIAL_PROSPECCION = "prospeccion"
COMMERCIAL_INTERESADO = "interesado"
COMMERCIAL_REUNION_PENDIENTE = "reunion_pendiente"
COMMERCIAL_REUNION_AGENDADA = "reunion_agendada"
COMMERCIAL_NO_PRIORIDAD = "no_prioridad"
COMMERCIAL_DERIVADO = "derivado"
COMMERCIAL_NO_INTERESADO = "no_interesado"
COMMERCIAL_CLIENTE = "cliente"

COMMERCIAL_STATE_LABELS: dict[str, str] = {
    COMMERCIAL_PROSPECCION: "Prospección",
    COMMERCIAL_INTERESADO: "Interesado",
    COMMERCIAL_REUNION_PENDIENTE: "Reunión pendiente",
    COMMERCIAL_REUNION_AGENDADA: "Reunión agendada",
    COMMERCIAL_NO_PRIORIDAD: "No prioridad",
    COMMERCIAL_DERIVADO: "Derivado",
    COMMERCIAL_NO_INTERESADO: "No interesado",
    COMMERCIAL_CLIENTE: "Cliente",
}

FILTERABLE_COMMERCIAL_STATES = frozenset(COMMERCIAL_STATE_LABELS.keys())


def commercial_state_label(state: str | None) -> str:
    key = (state or COMMERCIAL_PROSPECCION).strip().lower()
    return COMMERCIAL_STATE_LABELS.get(key, key.replace("_", " ").title())


def commercial_state_from_classification(
    *,
    response_class: str | None,
    reply_objective: str | None,
) -> str:
    """Mapea clasificación de respuesta → estado comercial."""
    rc = (response_class or "").strip().lower()
    obj = (reply_objective or "").strip().lower()

    if obj == "agendar":
        return COMMERCIAL_REUNION_PENDIENTE
    if rc == "derivar_a_otra_persona" or obj == "referir":
        return COMMERCIAL_DERIVADO
    if rc == "no_interesado" or obj == "rechazo":
        return COMMERCIAL_NO_INTERESADO
    if rc == "contactar_mas_adelante" or obj == "timing":
        return COMMERCIAL_NO_PRIORIDAD
    if rc == "interesado":
        return COMMERCIAL_INTERESADO
    if rc == "pedir_mas_info" or obj == "informar":
        return COMMERCIAL_INTERESADO
    return COMMERCIAL_PROSPECCION


def _latest_touch_classification(
    prospect: Prospect,
    *,
    include_testing: bool = True,
) -> dict[str, Any] | None:
    raw = getattr(prospect, "sequence_touch_log", None)
    if not raw:
        return None
    try:
        log = json.loads(raw)
    except Exception:
        return None
    if not isinstance(log, dict):
        return None
    best: dict[str, Any] | None = None
    best_day = -1
    for key, entry in log.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("response_class") and not entry.get("reply_objective"):
            continue
        if not include_testing and entry.get("testing"):
            continue
        try:
            day = int(key)
        except (TypeError, ValueError):
            day = 0
        if day >= best_day:
            best_day = day
            best = entry
    return best


def resolve_commercial_state(
    prospect: Prospect,
    db: Session | None = None,
    *,
    include_testing: bool = True,
) -> str:
    """Estado comercial efectivo (persistido + señales + historial de toques)."""
    pipeline = (getattr(prospect, "pipeline_stage", None) or "").strip().lower()
    if pipeline == PipelineStage.cerrado_ganado.value:
        return COMMERCIAL_CLIENTE

    if db is not None:
        from app.services.meeting_booking import (
            prospect_has_calendar_confirmed_meeting,
            prospect_has_pending_meeting,
        )

        if prospect_has_calendar_confirmed_meeting(db, prospect):
            return COMMERCIAL_REUNION_AGENDADA
        if prospect_has_pending_meeting(db, prospect):
            return COMMERCIAL_REUNION_PENDIENTE

    touch = _latest_touch_classification(prospect, include_testing=include_testing)
    if touch:
        return commercial_state_from_classification(
            response_class=str(touch.get("response_class") or ""),
            reply_objective=str(touch.get("reply_objective") or ""),
        )

    if not include_testing and bool(getattr(prospect, "commercial_state_is_testing", False)):
        stored = (getattr(prospect, "commercial_state", None) or "").strip().lower()
        if stored and stored in COMMERCIAL_STATE_LABELS and stored != COMMERCIAL_PROSPECCION:
            return COMMERCIAL_PROSPECCION

    stored = (getattr(prospect, "commercial_state", None) or "").strip().lower()
    if stored and stored in COMMERCIAL_STATE_LABELS:
        if include_testing or not bool(getattr(prospect, "commercial_state_is_testing", False)):
            return stored

    if not include_testing:
        if getattr(prospect, "sequence_started_at", None) or getattr(prospect, "last_outbound_at", None):
            return COMMERCIAL_PROSPECCION
        return COMMERCIAL_PROSPECCION

    if status == ProspectStatus.not_interested.value:
        return COMMERCIAL_NO_INTERESADO
    if status == ProspectStatus.interested.value:
        return COMMERCIAL_INTERESADO
    if getattr(prospect, "last_inbound_at", None):
        objection = (getattr(prospect, "objection_type", None) or "").strip().lower()
        if objection in ("timing", "no_time", "not_priority"):
            return COMMERCIAL_NO_PRIORIDAD
        if (getattr(prospect, "interest_level", None) or "").lower() in ("high", "medium"):
            return COMMERCIAL_INTERESADO

    if getattr(prospect, "sequence_started_at", None) or getattr(prospect, "last_outbound_at", None):
        return COMMERCIAL_PROSPECCION

    return COMMERCIAL_PROSPECCION


def apply_commercial_state(
    prospect: Prospect,
    *,
    response_class: str | None = None,
    reply_objective: str | None = None,
    db: Session | None = None,
    testing: bool = False,
) -> str:
    """Actualiza prospect.commercial_state según clasificación o señales."""
    if response_class or reply_objective:
        state = commercial_state_from_classification(
            response_class=response_class,
            reply_objective=reply_objective,
        )
    else:
        state = resolve_commercial_state(prospect, db=db, include_testing=not testing)

    if db is not None:
        from app.services.meeting_booking import prospect_has_calendar_confirmed_meeting

        if prospect_has_calendar_confirmed_meeting(db, prospect):
            state = COMMERCIAL_REUNION_AGENDADA
        elif state == COMMERCIAL_REUNION_AGENDADA:
            state = COMMERCIAL_REUNION_PENDIENTE

    prospect.commercial_state = state
    prospect.commercial_state_is_testing = bool(testing)
    return state


def commercial_fields(
    prospect: Prospect,
    db: Session | None = None,
    *,
    include_testing: bool = True,
) -> dict[str, str | bool]:
    state = resolve_commercial_state(prospect, db=db, include_testing=include_testing)
    is_testing = bool(getattr(prospect, "commercial_state_is_testing", False))
    return {
        "commercial_state": state,
        "commercial_state_label": commercial_state_label(state),
        "commercial_state_is_testing": is_testing if include_testing else False,
    }


def build_commercial_summary(
    prospects: list[dict[str, Any]],
    *,
    include_testing: bool = False,
) -> dict[str, Any]:
    """Resumen para bandeja SDR — counts por estado comercial."""
    counts: dict[str, int] = {k: 0 for k in COMMERCIAL_STATE_LABELS}
    for row in prospects:
        if not include_testing and row.get("commercial_state_is_testing"):
            key = COMMERCIAL_PROSPECCION
        else:
            key = (row.get("commercial_state") or COMMERCIAL_PROSPECCION).strip().lower()
        if key not in counts:
            counts[key] = 0
        counts[key] += 1
    total = len(prospects)
    return {
        "total": total,
        "prospeccion": counts.get(COMMERCIAL_PROSPECCION, 0),
        "interesados": counts.get(COMMERCIAL_INTERESADO, 0),
        "reuniones_pendientes": counts.get(COMMERCIAL_REUNION_PENDIENTE, 0),
        "reuniones_agendadas": counts.get(COMMERCIAL_REUNION_AGENDADA, 0),
        "no_prioridad": counts.get(COMMERCIAL_NO_PRIORIDAD, 0),
        "derivados": counts.get(COMMERCIAL_DERIVADO, 0),
        "no_interesados": counts.get(COMMERCIAL_NO_INTERESADO, 0),
        "clientes": counts.get(COMMERCIAL_CLIENTE, 0),
        "by_state": counts,
    }


def sync_commercial_state_from_inbound(
    db: Session,
    *,
    prospect: Prospect,
    inbound_text: str,
    sig: Any | None = None,
    testing: bool = False,
) -> dict[str, str]:
    """
    Clasifica respuesta inbound → estado comercial persistido (+ touch log si hay secuencia).
    Usar testing=True solo en simulaciones de secuencia.
    """
    from app.services import conversation_intelligence as ci

    body = (inbound_text or "").strip()
    if sig is None and body:
        from app.services.ai_instruction_context import campaign_education_blob
        from app.models.campaign import Campaign

        campaign = None
        if prospect.campaign_id:
            campaign = db.get(Campaign, prospect.campaign_id)
        education = campaign_education_blob(db, campaign) if campaign else ""
        sig = ci.classify_inbound_full(
            inbound_text=body,
            prior_interest=getattr(prospect, "interest_level", None),
            conversation_digest="",
            education=education,
        )
    if sig is None:
        state = apply_commercial_state(prospect, db=db, testing=testing)
        return {
            "commercial_state": state,
            "commercial_state_label": commercial_state_label(state),
            "response_class": "",
            "reply_objective": "",
        }

    response_class, response_class_label = ci.classify_commercial_response(body, sig)
    reply_objective = ci.resolve_reply_objective(
        text=body,
        sig=sig,
        response_class=response_class,
    )

    if getattr(prospect, "sequence_started_at", None):
        from app.services import prospect_sequence as pseq

        affected_day = pseq.last_sent_touch_day(prospect)
        if affected_day is not None:
            from datetime import datetime, timezone

            pseq._set_touch_entry(
                prospect,
                affected_day,
                inbound_at=datetime.now(timezone.utc).isoformat(),
                inbound_message=body[:4000],
                response_class=response_class,
                response_class_label=response_class_label,
                reply_objective=reply_objective,
                reply_objective_label=ci.REPLY_OBJECTIVE_LABELS.get(reply_objective, reply_objective),
                testing=testing,
            )

    state = apply_commercial_state(
        prospect,
        response_class=response_class,
        reply_objective=reply_objective,
        db=db,
        testing=testing,
    )
    return {
        "commercial_state": state,
        "commercial_state_label": commercial_state_label(state),
        "response_class": response_class,
        "response_class_label": response_class_label,
        "reply_objective": reply_objective,
    }
