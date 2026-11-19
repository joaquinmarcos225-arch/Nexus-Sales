"""Contenedor «Base de prospectos»: revelados desde Explorar, sin secuencia.

No es «Secuencias individuales». Ahí solo entran cuando el SDR los suma a propósito.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, OutreachEmailMode
from app.models.product import Product
from app.models.user import User

PROSPECT_BASE_KIND = "prospect_base"
PROSPECT_BASE_NAME = "Base de prospectos"


def is_prospect_base_campaign(campaign: Any) -> bool:
    if campaign is None:
        return False
    if (getattr(campaign, "kind", None) or "") == PROSPECT_BASE_KIND:
        return True
    name = str(getattr(campaign, "name", None) or "").strip()
    return name == PROSPECT_BASE_NAME


def get_or_create_prospect_base_container(
    db: Session,
    *,
    company_id: int,
    product: Product,
    operator: User,
) -> Campaign:
    """Una base por empresa. Paused: no arranca secuencias ni sourcing."""
    from app.services.campaign_prospects import count_campaign_prospects
    from app.services.prospect_timezone import operator_timezone

    operator_tz = operator_timezone(user=operator)

    existing = db.scalars(
        select(Campaign)
        .where(
            Campaign.company_id == int(company_id),
            Campaign.kind == PROSPECT_BASE_KIND,
        )
        .order_by(Campaign.id.asc())
    ).first()
    if existing is None:
        by_name = db.scalars(
            select(Campaign)
            .where(
                Campaign.company_id == int(company_id),
                Campaign.name == PROSPECT_BASE_NAME,
            )
            .order_by(Campaign.id.asc())
        ).first()
        if by_name is not None:
            by_name.kind = PROSPECT_BASE_KIND
            existing = by_name

    if existing is not None:
        if not existing.seller_id:
            existing.seller_id = operator.id
        existing.product_id = product.id
        existing.kind = PROSPECT_BASE_KIND
        existing.name = PROSPECT_BASE_NAME
        existing.automation_paused = True
        existing.status = CampaignStatus.ready.value
        existing.outreach_email_mode = OutreachEmailMode.draft_only.value
        existing.timezone = operator_tz
        _relocate_misfiled_reveals(db, company_id=int(company_id), base=existing)
        imported = count_campaign_prospects(db, existing.id)
        if int(existing.prospect_count or 0) < max(imported, 1):
            existing.prospect_count = max(imported, 1)
        db.flush()
        return existing

    row = Campaign(
        company_id=int(company_id),
        seller_id=operator.id,
        product_id=product.id,
        name=PROSPECT_BASE_NAME,
        kind=PROSPECT_BASE_KIND,
        prospect_count=1,
        calendar_link="https://calendar.google.com/calendar/u/0/r",
        timezone=operator_tz,
        available_hours="Lun-Vie 09:00-18:00",
        tone="profesional y directo",
        target_country="Argentina",
        status=CampaignStatus.ready.value,
        automation_paused=True,
        outreach_email_mode=OutreachEmailMode.draft_only.value,
        inbound_reply_mode="draft_only",
        allowed_channels=["linkedin", "email", "whatsapp"],
        estimated_meetings_min=0,
        estimated_meetings_max=0,
        estimated_cost_min=0,
        estimated_cost_max=0,
        estimated_avg_cost_per_meeting=0.0,
    )
    db.add(row)
    db.flush()
    _relocate_misfiled_reveals(db, company_id=int(company_id), base=row)
    return row


def _relocate_misfiled_reveals(db: Session, *, company_id: int, base: Campaign) -> int:
    """Saca de Secuencias individuales a revelados que nunca arrancaron secuencia."""
    from app.models.prospect import Prospect
    from app.services.manual_sequence_kickoff import (
        INDIVIDUAL_CAMPAIGN_NAME,
        INDIVIDUAL_CAMPAIGN_PREFIX,
    )

    individual_ids = [
        int(cid)
        for cid in db.scalars(
            select(Campaign.id).where(
                Campaign.company_id == int(company_id),
                (Campaign.name == INDIVIDUAL_CAMPAIGN_NAME)
                | (Campaign.name.startswith(INDIVIDUAL_CAMPAIGN_PREFIX)),
            )
        ).all()
    ]
    if not individual_ids:
        return 0
    rows = db.scalars(
        select(Prospect).where(
            Prospect.company_id == int(company_id),
            Prospect.campaign_id.in_(individual_ids),
            Prospect.sequence_started_at.is_(None),
            Prospect.notes.like("%Revelado desde Explorar%"),
        )
    ).all()
    moved = 0
    for p in rows:
        p.campaign_id = int(base.id)
        moved += 1
    if moved:
        db.flush()
    return moved
