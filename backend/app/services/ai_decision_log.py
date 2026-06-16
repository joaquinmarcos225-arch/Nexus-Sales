"""Registro y consulta del timeline de decisiones IA."""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.ai_decision_event import AiDecisionEvent
from app.services import conversation_intelligence as ci


def signals_payload(sig: ci.InboundSignals) -> dict[str, Any]:
    return {
        "objection_type": sig.objection_type,
        "interest_level": sig.interest_level,
        "prospect_wants_meeting": sig.prospect_wants_meeting,
        "explicit_meeting_commitment": sig.explicit_meeting_commitment,
        "asks_concrete_questions": sig.asks_concrete_questions,
        "is_brushoff": sig.is_brushoff,
        "prospect_timing_hold": sig.prospect_timing_hold,
        "defer_resume_at_iso": sig.defer_resume_at_iso,
    }


def record_ai_decision(
    db: Session,
    *,
    company_id: int,
    event_type: str,
    decision: str,
    summary: str,
    campaign_id: int | None = None,
    prospect_id: int | None = None,
    payload: dict[str, Any] | None = None,
    confidence: float | None = None,
    commit: bool = False,
) -> AiDecisionEvent:
    row = AiDecisionEvent(
        company_id=company_id,
        campaign_id=campaign_id,
        prospect_id=prospect_id,
        event_type=event_type,
        decision=decision,
        summary=summary,
        payload=payload,
        confidence=confidence,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def list_prospect_timeline(
    db: Session,
    *,
    company_id: int,
    prospect_id: int,
    limit: int = 80,
) -> list[AiDecisionEvent]:
    lim = max(1, min(limit, 200))
    return list(
        db.scalars(
            select(AiDecisionEvent)
            .where(
                AiDecisionEvent.company_id == company_id,
                AiDecisionEvent.prospect_id == prospect_id,
            )
            .order_by(desc(AiDecisionEvent.created_at), desc(AiDecisionEvent.id))
            .limit(lim)
        ).all()
    )


def list_company_feed(
    db: Session,
    *,
    company_id: int,
    limit: int = 60,
    campaign_id: int | None = None,
) -> list[AiDecisionEvent]:
    lim = max(1, min(limit, 150))
    q = select(AiDecisionEvent).where(AiDecisionEvent.company_id == company_id)
    if campaign_id is not None:
        q = q.where(AiDecisionEvent.campaign_id == campaign_id)
    q = q.order_by(desc(AiDecisionEvent.created_at), desc(AiDecisionEvent.id)).limit(lim)
    return list(db.scalars(q).all())
