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


def _join_blocks(*blocks: str | None) -> str:
    return "\n\n".join(b.strip() for b in blocks if b and str(b).strip())


def campaign_education_blob(
    db: Session,
    campaign: Campaign,
    *,
    channel: str | None = None,
    inbound_reply_channel: str | None = None,
    inbound_text: str | None = None,
) -> str:
    """`inbound_reply_channel` suma lo aprendido de respuestas reales (forma, no texto) para esa situación."""
    base = active_instruction_blob(db, campaign.company_id)
    ctx = getattr(campaign, "ai_context", None)
    blob = compose_education_blob(base, campaign_ai_context=ctx, campaign_name=campaign.name)
    style_block = ""
    seller_id = getattr(campaign, "seller_id", None)
    if seller_id:
        from app.models.user import User
        from app.services.user_outreach_style import build_style_prompt_block, style_record_for_user

        user = db.get(User, int(seller_id))
        style = style_record_for_user(db, int(seller_id))
        style_block = build_style_prompt_block(user=user, style=style, channel=channel)
    learning_block = ""
    if inbound_reply_channel is not None:
        from app.services.inbound_reply_learning import prompt_block

        learning_block = prompt_block(db, campaign.company_id, inbound_reply_channel, inbound_text)
    competitor_block = ""
    if (inbound_text or "").strip():
        try:
            from app.services.competitor_contrast import competitor_context_for_education

            competitor_block = competitor_context_for_education(inbound_text, campaign)
        except Exception:
            competitor_block = ""
    return _join_blocks(blob, style_block, learning_block, competitor_block)
