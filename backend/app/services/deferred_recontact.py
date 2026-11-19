"""Re-contacto al vencer una postergación, por el canal donde el prospecto pidió esperar."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.defer_timing import build_recontact_message, parse_defer_note

logger = logging.getLogger(__name__)

_CHANNELS = ("email", "whatsapp", "linkedin")


def _last_inbound_channel(db: Session, prospect_id: int) -> str | None:
    row = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "inbound",
        )
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(1)
    ).first()
    ch = (getattr(row, "channel", None) or "").strip().lower() if row else ""
    return ch if ch in _CHANNELS else None


def recontact_channel(db: Session, prospect: Prospect) -> str | None:
    ch = _last_inbound_channel(db, prospect.id)
    if ch:
        return ch
    pref = (prospect.preferred_channel or "").strip().lower()
    if pref in _CHANNELS:
        return pref
    if (prospect.email or "").strip():
        return "email"
    if (prospect.phone or "").strip() or (getattr(prospect, "whatsapp", None) or "").strip():
        return "whatsapp"
    if (prospect.linkedin_url or "").strip():
        return "linkedin"
    return None


def send_deferred_recontact(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    task_notes: str | None,
) -> str:
    """Devuelve 'sent', 'draft', 'queued' (bandeja manual) o 'skipped'."""
    kind, _ = parse_defer_note(task_notes)
    product = campaign.product.name if getattr(campaign, "product", None) else ""
    body = build_recontact_message(prospect_name=prospect.name, product_name=product, kind=kind)
    channel = recontact_channel(db, prospect)

    if channel == "email":
        from app.services.real_followup_gmail import deliver_email_via_gmail

        company = (prospect.company_name or "").strip() or "tu equipo"
        try:
            out = deliver_email_via_gmail(
                db,
                campaign=campaign,
                prospect=prospect,
                subject=f"Retomando — {company}",
                body=body,
                draft_label="re-contacto postergado",
            )
        except Exception:
            logger.exception("deferred recontact email failed prospect_id=%s", prospect.id)
            out = "skipped"
    elif channel == "whatsapp":
        from app.services import whatsapp_assisted_service as was

        was.mark_draft_suggested(db, prospect, campaign, body)
        out = "queued"
    elif channel == "linkedin":
        from app.services import linkedin_assisted_service as las

        las.mark_draft_suggested(db, prospect, campaign, body)
        out = "queued"
    else:
        out = "skipped"

    if out != "skipped":
        prospect.reactivation_sent_at = datetime.now(UTC)
    return out
