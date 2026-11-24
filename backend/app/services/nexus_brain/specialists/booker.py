"""Especialista Booker: agenda / slots / Calendar (reusa el orquestador de booking)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect


def run_booker(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    channel: str = "email",
    reply_objective: str | None = None,
    sig: Any | None = None,
    suggested_reply: str = "",
    testing: bool = False,
) -> dict[str, Any]:
    """Envuelve resolve_inbound_scheduling_reply — misma lógica de Calendar que hoy."""
    from app.services.inbound_turn_orchestrator import resolve_inbound_scheduling_reply

    kwargs = dict(
        campaign=campaign,
        prospect=prospect,
        inbound_text=inbound_text,
        reply_objective=reply_objective,
        sig=sig,
        suggested_reply=suggested_reply or "",
        testing=testing,
    )
    try:
        decision = resolve_inbound_scheduling_reply(db, channel=channel, **kwargs)
    except TypeError:
        # Deploy sin kw channel en resolve_inbound_scheduling_reply.
        decision = resolve_inbound_scheduling_reply(db, **kwargs)
    return {
        "action": decision.action,
        "reply_body": decision.reply_body,
        "meeting_booking": decision.meeting_booking,
        "reply_objective": decision.reply_objective,
        "response_class": decision.response_class,
        "skip_reason": decision.skip_reason,
        "offered_slots": list(decision.offered_slots or []),
        "notes": decision.notes or "",
    }
