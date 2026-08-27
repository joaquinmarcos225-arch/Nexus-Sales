"""Conteo de prospectos por campaña — cupo de prospecciones."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect


def count_campaign_prospects(session: Session, campaign_id: int) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Prospect).where(Prospect.campaign_id == campaign_id)
        )
        or 0
    )


def campaign_prospect_slots_remaining(session: Session, campaign: Campaign) -> int:
    imported = count_campaign_prospects(session, campaign.id)
    return max(0, int(campaign.prospect_count) - imported)
