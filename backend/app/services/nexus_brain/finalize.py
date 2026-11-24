"""Cierre de turno: validar action, preferir bodies de especialistas, QA mínima."""

from __future__ import annotations

import re
from typing import Any

from app.services.nexus_brain.contract import BrainAction, BrainTurnResult

_ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {
        "skip",
        "skip_autoresponder",
        "booked",
        "alternatives",
        "booking_failed",
        "offer_hours",
        "calendar_link",
        "ask_email",
        "ask_slot",
        "canceled",
        "referral",
        "normal_reply",
        "escalate",
    }
)

_SCHEDULING_ACTIONS: frozenset[str] = frozenset(
    {
        "booked",
        "alternatives",
        "offer_hours",
        "calendar_link",
        "ask_email",
        "ask_slot",
        "booking_failed",
        "canceled",
    }
)

_AI_SELF_RE = re.compile(
    r"\b(soy\s+(una?\s+)?ia|as\s+an\s+ai|i'?m\s+an\s+ai|language\s+model)\b",
    re.I,
)


def sanitize_action(raw: str | None, *, default: BrainAction = "normal_reply") -> BrainAction:
    key = (raw or "").strip().lower()
    if key in _ALLOWED_ACTIONS:
        return key  # type: ignore[return-value]
    return default


def scrub_reply(text: str | None) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    body = _AI_SELF_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def merge_turn(
    *,
    channel: str,
    finish: dict[str, Any] | None,
    state: dict[str, Any],
    reply_objective: str | None,
    used_agent: bool,
    trace: list[dict[str, Any]],
    notes: str = "",
) -> BrainTurnResult | None:
    """
    Arma BrainTurnResult priorizando especialistas:
    1) Booker si resolvió agenda / skip autoresponder
    2) Writer / research contrast
    3) finish.reply_body (último recurso)
    """
    booker = state.get("booker") or {}
    writer = state.get("writer") or {}
    research = state.get("research") or {}
    finish = finish or {}

    booker_action = sanitize_action(booker.get("action"), default="normal_reply")
    finish_action = sanitize_action(finish.get("action"), default="normal_reply")

    if booker_action == "skip_autoresponder":
        return BrainTurnResult(
            action="skip_autoresponder",
            skip_reason=booker.get("skip_reason") or "autoresponder",
            used_agent=used_agent,
            agent_trace=trace,
            channel=channel,
            reply_objective=str(
                booker.get("reply_objective") or reply_objective or "seguimiento"
            ),
            response_class=str(booker.get("response_class") or "otro"),
            notes="|".join(x for x in (notes, "merge_skip_autoresponder") if x),
        )

    writer_body = scrub_reply(writer.get("reply_body"))
    # Objetivo del turno (no el del Booker): «informar» no se pisa con ask_slot genérico.
    turn_objective = str(
        finish.get("reply_objective") or reply_objective or ""
    ).strip().lower()
    writer_intent = str(writer.get("intent") or "").strip().lower()
    prefer_writer_price = writer_body and (
        writer.get("source") == "price_unknown_gate" or writer_intent == "pricing"
    )
    prefer_writer_info = (
        booker_action in ("ask_slot", "offer_hours", "calendar_link")
        and writer_body
        and not booker.get("meeting_booking")
        and turn_objective
        in ("informar", "seguimiento", "responder", "question", "pricing", "")
    )
    # Precio honesty / pregunta de producto: no pisar con horarios/ask_slot genéricos.
    if prefer_writer_price or prefer_writer_info:
        return BrainTurnResult(
            action="normal_reply",
            reply_body=writer_body,
            subject=writer.get("subject") if channel == "email" else None,
            meeting_booking=None,
            reply_objective=str(
                finish.get("reply_objective") or reply_objective or "informar"
            ),
            response_class=str(
                finish.get("response_class") or writer.get("intent") or "answer"
            ),
            used_agent=used_agent,
            agent_trace=trace,
            notes="|".join(
                x
                for x in (
                    notes,
                    (
                        "prefer_price_unknown_gate"
                        if prefer_writer_price
                        else f"prefer_writer_over_bare_{booker_action}"
                    ),
                )
                if x
            ),
            channel=channel,
        )

    if booker_action in _SCHEDULING_ACTIONS:
        action = booker_action
        body = scrub_reply(booker.get("reply_body") or finish.get("reply_body"))
        meeting = booker.get("meeting_booking")
        objective = (
            booker.get("reply_objective")
            or finish.get("reply_objective")
            or reply_objective
            or "agendar"
        )
        response_class = (
            booker.get("response_class") or finish.get("response_class") or "interesado"
        )
        merge_note = "prefer_booker"
    else:
        action = finish_action
        body = writer_body
        if not body:
            body = scrub_reply(research.get("contrast_reply"))
        if not body:
            body = scrub_reply(finish.get("reply_body"))
        # Si finish inventó texto pero hay contrast más fiel, preferí contrast+finish corto
        contrast = scrub_reply(research.get("contrast_reply"))
        if contrast and body and body == scrub_reply(finish.get("reply_body")):
            body = contrast
        meeting = booker.get("meeting_booking")
        objective = (
            finish.get("reply_objective")
            or booker.get("reply_objective")
            or reply_objective
            or "seguimiento"
        )
        response_class = (
            finish.get("response_class") or booker.get("response_class") or "otro"
        )
        merge_note = "prefer_writer_or_research"

    if action == "escalate" and not body:
        return BrainTurnResult(
            action="escalate",
            reply_body=None,
            skip_reason=finish.get("handoff_reason") or "handoff",
            used_agent=used_agent,
            agent_trace=trace,
            channel=channel,
            reply_objective=str(objective or "seguimiento"),
            response_class=str(response_class or "otro"),
            notes="|".join(x for x in (notes, "merge_escalate") if x),
        )

    # Sin Calendar real: no dejar que Writer invente «quedamos / te enviaré el link».
    if action != "booked" and body:
        try:
            from app.services.outbound_text_normalize import strip_unverified_meeting_claims

            cleaned = strip_unverified_meeting_claims(body)
            if cleaned != body:
                merge_note = f"{merge_note}|strip_unverified_booking_claims"
                if cleaned:
                    body = cleaned
                elif (objective or "").strip().lower() == "agendar" or booker_action in (
                    "ask_slot",
                    "offer_hours",
                    "alternatives",
                    "normal_reply",
                ):
                    body = (
                        "Confirmame de nuevo día y hora (ej. martes 10:00) "
                        "y lo dejo agendado al toque."
                    )
                else:
                    body = cleaned
        except Exception:
            pass

    if not body:
        return None

    return BrainTurnResult(
        action=action,
        reply_body=body,
        subject=writer.get("subject") if channel == "email" else None,
        meeting_booking=meeting,
        reply_objective=str(objective or "seguimiento"),
        response_class=str(response_class or "otro"),
        skip_reason=finish.get("handoff_reason") if action == "escalate" else None,
        used_agent=used_agent,
        agent_trace=trace,
        notes="|".join(x for x in (notes, merge_note) if x),
        channel=channel,
    )
