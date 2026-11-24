"""Router heurístico barato: qué especialistas conviene correr antes/después del LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoutePlan:
    need_research: bool
    need_booker: bool
    need_writer: bool
    intent: str
    reason: str


def plan_specialists(
    inbound_text: str,
    *,
    sig: Any | None = None,
    reply_objective: str | None = None,
) -> RoutePlan:
    from app.services import conversation_intelligence as ci
    from app.services.competitor_contrast import extract_competitor_name
    from app.services.inbound_turn_orchestrator import (
        inbound_mentions_scheduling,
        inbound_refers_to_prior_schedule,
    )

    body = (inbound_text or "").strip()
    try:
        from app.services.linkedin_reply_compose import inbound_asks_integration, price_asked

        pricing_q = price_asked(body)
        integration_q = inbound_asks_integration(body)
    except Exception:
        pricing_q = False
        integration_q = False
    competitor = bool(extract_competitor_name(body)) and not integration_q
    try:
        from app.services.whatsapp_reply_compose import inbound_is_info_question

        info_q = inbound_is_info_question(body)
    except Exception:
        info_q = False
    scheduling = bool(
        inbound_mentions_scheduling(body)
        or inbound_refers_to_prior_schedule(body)
        or (reply_objective or "").strip().lower() == "agendar"
        or (
            sig is not None
            and (
                bool(getattr(sig, "explicit_meeting_commitment", False))
                or bool(getattr(sig, "prospect_wants_meeting", False))
                or ci.inbound_has_explicit_meeting_slot(body)
            )
        )
    )
    # Pregunta de info/capacidad gana a un falso positivo de “horario” (ej. 24hs).
    if info_q and scheduling and not (
        sig is not None
        and (
            bool(getattr(sig, "explicit_meeting_commitment", False))
            or ci.inbound_has_explicit_meeting_slot(body)
        )
    ):
        scheduling = False
    rejection = (reply_objective or "").strip().lower() == "rechazo" or (
        sig is not None
        and (getattr(sig, "objection_type", None) or "") == "not_interested"
    )
    asks_question = (reply_objective or "").strip().lower() == "informar" or (
        sig is not None and bool(getattr(sig, "asks_concrete_questions", False))
    )
    try:
        from app.services.whatsapp_reply_compose import inbound_is_product_aside

        product_q = inbound_is_product_aside(body)
    except Exception:
        product_q = False

    if rejection:
        return RoutePlan(
            need_research=False,
            need_booker=False,
            need_writer=True,
            intent="reject",
            reason="rejection",
        )
    # Precio/número: Writer primero (gate honesty). No research de “Agendar” falso.
    if pricing_q:
        return RoutePlan(
            need_research=False,
            need_booker=False,
            need_writer=True,
            intent="pricing",
            reason="price_question",
        )
    # Integración con tool nombrada ≠ contraste competidor.
    if integration_q and not scheduling:
        return RoutePlan(
            need_research=False,
            need_booker=False,
            need_writer=True,
            intent="integration",
            reason="integration_question",
        )
    if competitor and scheduling:
        return RoutePlan(
            need_research=True,
            need_booker=True,
            need_writer=True,
            intent="competitor_schedule",
            reason="competitor+scheduling",
        )
    if competitor:
        return RoutePlan(
            need_research=True,
            need_booker=False,
            need_writer=True,
            intent="competitor",
            reason="competitor",
        )
    # Pregunta concreta de producto/adopción: Writer (OpenAI). Sin Booker falso.
    if (asks_question or product_q or info_q) and not scheduling:
        return RoutePlan(
            need_research=False,
            need_booker=False,
            need_writer=True,
            intent="question",
            reason="concrete_question",
        )
    if scheduling:
        return RoutePlan(
            need_research=False,
            need_booker=True,
            need_writer=True,
            intent="schedule",
            reason="scheduling",
        )
    return RoutePlan(
        need_research=False,
        need_booker=True,  # booker no-op → normal_reply barato / detecta OOO
        need_writer=True,
        intent="followup",
        reason="default",
    )
