"""Métricas básicas por usuario para la vista de Equipo."""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, ProspectOwnershipStatus
from app.models.prospect import Prospect


def user_team_metrics(db: Session, *, company_id: int, user_ids: list[int]) -> dict[int, dict[str, int]]:
    if not user_ids:
        return {}

    claimed_statuses = (
        ProspectOwnershipStatus.tomado.value,
        ProspectOwnershipStatus.en_secuencia.value,
        ProspectOwnershipStatus.secuencia_finalizada.value,
    )
    active_campaign_statuses = (CampaignStatus.running.value, CampaignStatus.ready.value)

    prospect_rows = db.execute(
        select(
            Prospect.owner_user_id,
            func.sum(
                case((Prospect.ownership_status.in_(claimed_statuses), 1), else_=0)
            ),
            func.sum(
                case(
                    (Prospect.ownership_status == ProspectOwnershipStatus.en_secuencia.value, 1),
                    else_=0,
                )
            ),
        )
        .where(
            Prospect.company_id == company_id,
            Prospect.owner_user_id.in_(user_ids),
        )
        .group_by(Prospect.owner_user_id)
    ).all()

    campaign_rows = db.execute(
        select(Campaign.seller_id, func.count(Campaign.id))
        .where(
            Campaign.company_id == company_id,
            Campaign.seller_id.in_(user_ids),
            Campaign.status.in_(active_campaign_statuses),
        )
        .group_by(Campaign.seller_id)
    ).all()

    prospect_map = {
        int(row[0]): {"prospects_claimed": int(row[1] or 0), "active_sequences": int(row[2] or 0)}
        for row in prospect_rows
        if row[0] is not None
    }
    campaign_map = {int(row[0]): int(row[1] or 0) for row in campaign_rows}

    out: dict[int, dict[str, int]] = {}
    for uid in user_ids:
        p = prospect_map.get(uid, {})
        out[uid] = {
            "prospects_claimed": p.get("prospects_claimed", 0),
            "active_sequences": p.get("active_sequences", 0),
            "active_campaigns": campaign_map.get(uid, 0),
        }
    return out
