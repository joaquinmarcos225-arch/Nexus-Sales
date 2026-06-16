"""Activar / pausar campaña: estado operativo + primer outreach automático."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.connected_account import ConnectedAccount
from app.models.enums import CampaignStatus, IntegrationProvider, IntegrationStatus
from app.models.outreach import OutreachSequence
from app.services import multichannel_sequence as mseq
from app.services.ai_instruction_context import campaign_education_blob
from app.schemas.campaign_channels import coerce_allowed_channels


def seller_has_gmail(db: Session, company_id: int, user_id: int) -> bool:
    row = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == IntegrationProvider.gmail.value,
            ConnectedAccount.status == IntegrationStatus.connected.value,
        )
    ).first()
    return row is not None


def _get_or_create_sequence(db: Session, campaign_id: int) -> OutreachSequence:
    seq = db.scalars(
        select(OutreachSequence).where(OutreachSequence.campaign_id == campaign_id)
    ).first()
    if seq is None:
        seq = OutreachSequence(campaign_id=campaign_id, is_running=False, current_step=0)
        db.add(seq)
        db.flush()
    return seq


def activate_campaign(db: Session, campaign_id: int) -> dict[str, Any]:
    """
    Marca campaña activa, enciende secuencia y dispara outreach inicial (Gmail si está conectado).
    """
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        return {"ok": False, "detail": "Campaña no encontrada"}

    if not campaign.seller_id:
        return {
            "ok": False,
            "detail": "Asigná un vendedor a la campaña antes de iniciarla.",
            "gmail_connected": False,
        }

    gmail_ok = seller_has_gmail(db, campaign.company_id, int(campaign.seller_id))

    campaign.status = CampaignStatus.running.value
    campaign.automation_paused = False
    if getattr(campaign, "updated_at", None) is not None:
        campaign.updated_at = datetime.now(UTC)

    seq = _get_or_create_sequence(db, campaign_id)
    seq.is_running = True
    seq.current_step = int(seq.current_step or 0) + 1

    education = campaign_education_blob(db, campaign)
    channels = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))

    bootstrap = mseq.bootstrap_on_start(
        db,
        campaign,
        channels_allowed=channels,
        education_blob=education,
    )

    contacted = int(bootstrap.get("day1_sent") or 0) + int(bootstrap.get("drafts") or 0) + int(
        bootstrap.get("sent") or 0
    )
    if contacted == 0:
        contacted = int(bootstrap.get("contacted_now") or 0)

    mseq._append_log(
        campaign,
        "Campaña iniciada: Nexus procesará outreach, respuestas y follow-ups en automático.",
        kind="sequence",
    )

    return {
        "ok": True,
        "sequence": seq,
        "campaign": campaign,
        "gmail_connected": gmail_ok,
        "contacted_now": contacted,
        "drafts": int(bootstrap.get("drafts") or 0),
        "sent": int(bootstrap.get("sent") or 0),
        "skipped": int(bootstrap.get("skipped") or 0),
        "errors": int(bootstrap.get("errors") or 0),
        "error_messages": list(bootstrap.get("error_messages") or []),
        "used_gmail": bool(bootstrap.get("used_gmail")),
        "simulated": bool(bootstrap.get("simulated")),
    }


def pause_campaign(db: Session, campaign_id: int) -> OutreachSequence | None:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None

    campaign.automation_paused = True
    if campaign.status == CampaignStatus.running.value:
        campaign.status = CampaignStatus.paused.value
    if getattr(campaign, "updated_at", None) is not None:
        campaign.updated_at = datetime.now(UTC)

    seq = _get_or_create_sequence(db, campaign_id)
    seq.is_running = False

    mseq._append_log(campaign, "Campaña pausada: automatización detenida.", kind="info")
    return seq
