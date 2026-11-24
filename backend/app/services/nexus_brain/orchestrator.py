"""
Nexus Brain — agente orquestador que delega a especialistas.

Entry point: run_inbound_brain_turn(...)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services.nexus_brain.agent_loop import brain_agent_enabled, run_orchestrator_agent_loop
from app.services.nexus_brain.contract import BrainTurnResult
from app.services.nexus_brain.finalize import merge_turn
from app.services.nexus_brain.router import RoutePlan, plan_specialists
from app.services.nexus_brain.specialists import run_booker, run_policy, run_research, run_writer

logger = logging.getLogger(__name__)


def _product_bits(campaign: Campaign) -> tuple[str, str]:
    product = getattr(campaign, "product", None)
    name = (
        getattr(product, "name", None) or getattr(campaign, "name", None) or "nuestro producto"
    ).strip()
    pitch = (
        getattr(product, "description", None)
        or getattr(product, "pitch", None)
        or getattr(campaign, "value_proposition", None)
        or ""
    )
    return name, (pitch or "").strip()[:400]


def _build_user_brief(
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    channel: str,
    sig: Any | None,
    plan: RoutePlan,
) -> str:
    product_name, pitch = _product_bits(campaign)
    interest = getattr(sig, "interest_level", None) if sig is not None else prospect.interest_level
    objection = getattr(sig, "objection_type", None) if sig is not None else prospect.objection_type
    summary = (getattr(prospect, "conversation_summary", None) or "").strip()
    hints = []
    if plan.intent == "pricing":
        hints.append(
            "HINT: precio/número → SOLO draft_reply. "
            "NO research_competitor («agendar» no es un tool)."
        )
    elif plan.need_research:
        hints.append("HINT: llamá research_competitor")
    if plan.need_booker and plan.intent != "pricing":
        hints.append("HINT: llamá resolve_scheduling")
    if plan.need_writer:
        hints.append("HINT: llamá draft_reply")
    hints.append("Cerrá SIEMPRE con finish")
    inbound_full = (inbound_text or "").strip()[:2000]
    return (
        f"Canal: {channel}\n"
        f"Prospecto: {prospect.name or '—'} · {prospect.company_name or '—'} · {prospect.role or '—'}\n"
        f"Producto: {product_name}\n"
        f"Pitch: {pitch or '—'}\n"
        f"Interés previo: {interest or 'low'} · objeción: {objection or '—'}\n"
        f"Resumen conversación: {summary or '—'}\n"
        f"Plan local: intent={plan.intent} reason={plan.reason}\n"
        f"{'; '.join(hints)}\n\n"
        "INSTRUCCIÓN: leé el inbound COMPLETO abajo. No decidás por la primera palabra.\n"
        f"Inbound del prospecto (mensaje entero):\n{inbound_full}\n"
    )


def _run_plan_specialists(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    channel: str,
    sig: Any | None,
    reply_objective: str | None,
    testing: bool,
    plan: RoutePlan,
    state: dict[str, Any],
    trace: list[dict[str, Any]],
) -> None:
    """Ejecuta subagentes faltantes según el plan (gap-fill post-agente o pre-fallback)."""
    body = (inbound_text or "").strip()
    if plan.need_research and not (state.get("research") or {}).get("found"):
        state["research"] = run_research(
            db, campaign=campaign, prospect=prospect, inbound_text=body
        )
        trace.append(
            {
                "event": "gapfill_research",
                "competitor": (state["research"] or {}).get("competitor"),
            }
        )

    if plan.need_writer and not (state.get("writer") or {}).get("reply_body"):
        contrast = (state.get("research") or {}).get("contrast_reply") or ""
        state["writer"] = run_writer(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=body,
            channel=channel,
            intent=plan.intent,
            research_contrast=contrast,
            reply_objective=reply_objective,
            sig=sig,
        )
        trace.append(
            {"event": "gapfill_writer", "source": (state["writer"] or {}).get("source")}
        )

    if plan.need_booker and not (state.get("booker") or {}).get("action"):
        suggested = (state.get("writer") or {}).get("reply_body") or ""
        state["booker"] = run_booker(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=body,
            channel=channel,
            reply_objective=reply_objective,
            sig=sig,
            suggested_reply=suggested,
            testing=testing,
        )
        trace.append(
            {"event": "gapfill_booker", "action": (state["booker"] or {}).get("action")}
        )


def run_inbound_brain_turn(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    channel: str = "email",
    sig: Any | None = None,
    reply_objective: str | None = None,
    testing: bool = False,
    force_fallback: bool = False,
) -> BrainTurnResult:
    """
    Turno completo del Brain:
    Policy → plan → agente orquestador (tools) → gap-fill especialistas → merge.
    """
    trace: list[dict[str, Any]] = []
    ch = (channel or "email").strip().lower() or "email"

    policy = run_policy(
        db, campaign=campaign, prospect=prospect, inbound_text=inbound_text, channel=ch
    )
    trace.append(
        {"event": "policy", **{k: policy.get(k) for k in ("ok", "action", "skip_reason")}}
    )
    if not policy.get("ok"):
        return BrainTurnResult(
            action=policy.get("action") or "skip",  # type: ignore[arg-type]
            skip_reason=policy.get("skip_reason"),
            used_agent=False,
            agent_trace=trace,
            channel=ch,
            notes="policy_gate",
        )

    plan = plan_specialists(
        inbound_text, sig=sig, reply_objective=reply_objective
    )
    trace.append(
        {
            "event": "route_plan",
            "intent": plan.intent,
            "reason": plan.reason,
            "need_research": plan.need_research,
            "need_booker": plan.need_booker,
            "need_writer": plan.need_writer,
        }
    )

    # Precio sin ficha: no pasar por el agente (inventa “Agendar” desde «antes de agendar»).
    try:
        from app.services.linkedin_reply_compose import price_asked
        from app.services.nexus_brain.specialists.writer import (
            _price_unknown_writer_reply,
        )

        if price_asked(inbound_text) or plan.intent == "pricing":
            price_out = _price_unknown_writer_reply(
                campaign, inbound_text=inbound_text, channel=ch
            )
            if price_out and (price_out.get("reply_body") or "").strip():
                trace.append(
                    {
                        "event": "price_unknown_short_circuit",
                        "source": price_out.get("source"),
                    }
                )
                return BrainTurnResult(
                    action="normal_reply",
                    reply_body=str(price_out["reply_body"]).strip(),
                    subject=price_out.get("subject") if ch == "email" else None,
                    meeting_booking=None,
                    reply_objective="pricing",
                    response_class="pricing",
                    used_agent=False,
                    agent_trace=trace,
                    notes="price_unknown_short_circuit",
                    channel=ch,
                )
    except Exception:
        logger.exception("price_unknown short-circuit failed; continuing brain turn")

    state: dict[str, Any] = {"research": {}, "writer": {}, "booker": {}}

    def _tool_research(args: dict[str, Any]) -> dict[str, Any]:
        try:
            from app.services.linkedin_reply_compose import price_asked as _price_asked

            if _price_asked(inbound_text):
                out = {
                    "found": False,
                    "competitor": "",
                    "facts": [],
                    "contrast_reply": "",
                    "they_use": False,
                    "skipped": "price_question",
                }
                state["research"] = out
                return out
        except Exception:
            pass
        out = run_research(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=inbound_text,
            competitor_hint=(args.get("competitor_hint") or None),
        )
        state["research"] = out
        return out

    def _tool_booker(args: dict[str, Any]) -> dict[str, Any]:
        suggested = (args.get("suggested_reply") or "").strip()
        if not suggested:
            suggested = (state.get("writer") or {}).get("reply_body") or ""
        out = run_booker(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=inbound_text,
            channel=ch,
            reply_objective=reply_objective,
            sig=sig,
            suggested_reply=suggested,
            testing=testing,
        )
        state["booker"] = out
        return out

    def _tool_writer(args: dict[str, Any]) -> dict[str, Any]:
        contrast = (args.get("research_contrast") or "").strip()
        if not contrast:
            contrast = (state.get("research") or {}).get("contrast_reply") or ""
        out = run_writer(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=inbound_text,
            channel=ch,
            intent=(args.get("intent") or plan.intent),
            research_contrast=contrast,
            reply_objective=reply_objective,
            sig=sig,
        )
        state["writer"] = out
        return out

    use_agent = brain_agent_enabled() and not force_fallback
    finish: dict[str, Any] | None = None
    if use_agent:
        finish, agent_trace = run_orchestrator_agent_loop(
            user_brief=_build_user_brief(
                campaign=campaign,
                prospect=prospect,
                inbound_text=inbound_text,
                channel=ch,
                sig=sig,
                plan=plan,
            ),
            tool_handlers={
                "research_competitor": _tool_research,
                "resolve_scheduling": _tool_booker,
                "draft_reply": _tool_writer,
            },
        )
        trace.extend(agent_trace)

    # Garantiza especialistas del plan aunque el LLM se haya olvidado de un tool.
    _run_plan_specialists(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text=inbound_text,
        channel=ch,
        sig=sig,
        reply_objective=reply_objective,
        testing=testing,
        plan=plan,
        state=state,
        trace=trace,
    )

    merged = merge_turn(
        channel=ch,
        finish=finish,
        state=state,
        reply_objective=reply_objective,
        used_agent=bool(use_agent and finish is not None),
        trace=trace,
        notes="agent_merge" if finish else "specialists_only",
    )
    if merged is not None:
        return merged

    # Último recurso: forzar writer+booker aunque el plan diga poco.
    emergency = RoutePlan(
        need_research=True,
        need_booker=True,
        need_writer=True,
        intent="followup",
        reason="emergency",
    )
    _run_plan_specialists(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text=inbound_text,
        channel=ch,
        sig=sig,
        reply_objective=reply_objective,
        testing=testing,
        plan=emergency,
        state=state,
        trace=trace,
    )
    merged = merge_turn(
        channel=ch,
        finish=finish,
        state=state,
        reply_objective=reply_objective,
        used_agent=bool(use_agent and finish is not None),
        trace=trace,
        notes="emergency_merge",
    )
    if merged is not None:
        return merged

    return BrainTurnResult(
        action="normal_reply",
        reply_body=None,
        skip_reason="brain_empty_reply",
        used_agent=bool(use_agent and finish is not None),
        agent_trace=trace,
        channel=ch,
        notes="empty_after_merge",
    )
