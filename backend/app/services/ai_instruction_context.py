"""Carga instrucciones activas por empresa para inyectar en prompts OpenAI."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_instruction import AIInstruction
from app.models.campaign import Campaign
from app.services.ai_behavior_policy import is_behavior_system_instruction


def active_instruction_blob(db: Session, company_id: int) -> str:
    rows = db.scalars(
        select(AIInstruction)
        .where(
            AIInstruction.company_id == company_id,
            AIInstruction.is_active.is_(True),
        )
        .order_by(AIInstruction.created_at.asc())
    ).all()
    if not rows:
        return ""
    parts = []
    for r in rows:
        if is_behavior_system_instruction(r.title):
            continue
        parts.append(f"[{r.title}]\n{r.content.strip()}")
    return "\n\n".join(parts)


def compose_education_blob(
    base: str,
    *,
    campaign_ai_context: str | None = None,
    campaign_name: str = "",
) -> str:
    extra = (campaign_ai_context or "").strip()
    if not extra:
        return base or ""
    label = (campaign_name or "").strip() or "Campaña"
    block = f"[Contexto IA · {label}]\n{extra}"
    if base and str(base).strip():
        return f"{str(base).strip()}\n\n{block}"
    return block


def campaign_education_blob(db: Session, campaign: Campaign) -> str:
    base = active_instruction_blob(db, campaign.company_id)
    ctx = getattr(campaign, "ai_context", None)
    return compose_education_blob(base, campaign_ai_context=ctx, campaign_name=campaign.name)
