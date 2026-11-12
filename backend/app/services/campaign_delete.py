"""Eliminar campaña sin borrar contactos de la empresa."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.services.manual_sequence_kickoff import (
    find_sequence_operator,
    get_or_create_individual_container,
)


class CampaignDeleteError(Exception):
    pass


def relocate_prospects_when_campaign_deleted(db: Session, campaign: Campaign) -> int:
    """Mueve prospectos a «Secuencias individuales»; conserva mail, historial y ownership."""
    prospects = list(
        db.scalars(select(Prospect).where(Prospect.campaign_id == campaign.id)).all()
    )
    if not prospects:
        return 0

    product = None
    if campaign.product_id:
        product = db.get(Product, int(campaign.product_id))
    if product is None:
        product = db.scalars(
            select(Product)
            .where(Product.company_id == campaign.company_id)
            .order_by(Product.id.asc())
        ).first()
    if product is None:
        raise CampaignDeleteError(
            "No podemos guardar los contactos al borrar la campaña: la empresa no tiene producto."
        )

    preferred = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
    operator = find_sequence_operator(
        db, company_id=int(campaign.company_id), preferred=preferred
    )
    container = get_or_create_individual_container(
        db,
        company_id=int(campaign.company_id),
        product=product,
        operator=operator,
    )
    if int(container.id) == int(campaign.id):
        return 0

    old_id = int(campaign.id)
    new_id = int(container.id)
    pids = [int(p.id) for p in prospects]
    for p in prospects:
        if p.origin_campaign_id is None:
            p.origin_campaign_id = old_id
        p.campaign_id = new_id

    db.execute(
        update(OutreachMessage)
        .where(OutreachMessage.campaign_id == old_id, OutreachMessage.prospect_id.in_(pids))
        .values(campaign_id=new_id)
    )
    db.execute(
        update(OutreachTask)
        .where(OutreachTask.campaign_id == old_id, OutreachTask.prospect_id.in_(pids))
        .values(campaign_id=new_id)
    )
    db.execute(
        update(Meeting)
        .where(Meeting.campaign_id == old_id, Meeting.prospect_id.in_(pids))
        .values(campaign_id=new_id)
    )
    db.flush()
    return len(prospects)
