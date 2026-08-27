"""Backfill nombre de empresa + regenerar copy con placeholder «Empresa»."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.outreach_display_names import (
    prospect_company_display,
    resolve_prospect_company_name,
)

_logger = logging.getLogger(__name__)
_EMPRESA_RE = re.compile(r"\[Empresa\]|\{Empresa\}|(?<![Tt]u )(?<![Nn]uestra )\bEmpresa\b")


def _copy_has_empresa(text: str | None) -> bool:
    return bool(text and _EMPRESA_RE.search(text))


def backfill_prospect_company_name(prospect: Prospect) -> str | None:
    """Si el nombre es placeholder, infiere del mail/web. None si no cambió."""
    current = prospect_company_display(prospect.company_name)
    inferred = resolve_prospect_company_name(
        company_name=prospect.company_name,
        email=prospect.email,
        website=getattr(prospect, "company_website", None),
    )
    if not inferred or inferred == current:
        return None
    prospect.company_name = inferred[:255]
    return inferred


def repair_campaign_empresa_copy(
    db: Session,
    campaign_id: int,
    *,
    regenerate: bool = True,
) -> dict[str, Any]:
    """
    Rellena company_name y regenera SOLO borradores/mensajes no enviados
    que todavía dicen «Empresa». No toca enviados ni inbound.
    """
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller), selectinload(Campaign.company))
    ).first()
    if campaign is None:
        return {"ok": False, "detail": "Campaña no encontrada"}

    prospects = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
    named = 0
    li_regen = 0
    wa_regen = 0
    msg_regen = 0
    skipped_sent = 0

    from app.services.ai_instruction_context import campaign_education_blob
    from app.services.linkedin_assisted_service import ensure_linkedin_draft
    from app.services.sdr_outreach_compose import generate_playbook_touch_for_prospect, prior_touches_from_history

    education = campaign_education_blob(db, campaign)

    for prospect in prospects:
        if backfill_prospect_company_name(prospect):
            named += 1

        if regenerate and _copy_has_empresa(prospect.linkedin_assisted_draft):
            if getattr(prospect, "linkedin_sdr_marked_sent_at", None):
                skipped_sent += 1
            else:
                prospect.linkedin_assisted_draft = None
                try:
                    ensure_linkedin_draft(db, prospect, campaign)
                    li_regen += 1
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("LI regen failed prospect=%s: %s", prospect.id, exc)

        if regenerate and _copy_has_empresa(getattr(prospect, "whatsapp_assisted_draft", None)):
            if (getattr(prospect, "whatsapp_assist_status", None) or "") == "sent":
                skipped_sent += 1
            else:
                try:
                    history = list(prospect.outreach_messages or [])
                    prior = prior_touches_from_history(history)
                    _subj, body = generate_playbook_touch_for_prospect(
                        db,
                        campaign=campaign,
                        prospect=prospect,
                        education=education,
                        channel="whatsapp",
                        prior_touches=prior,
                    )
                    if body:
                        prospect.whatsapp_assisted_draft = body
                        wa_regen += 1
                except Exception as exc:  # noqa: BLE001
                    _logger.warning("WA regen failed prospect=%s: %s", prospect.id, exc)

        draft_json = getattr(prospect, "sequence_playbook_draft", None)
        if regenerate and _copy_has_empresa(str(draft_json or "")):
            try:
                _subj, body = generate_playbook_touch_for_prospect(
                    db,
                    campaign=campaign,
                    prospect=prospect,
                    education=education,
                    channel="email",
                    prior_touches=[],
                )
                if body:
                    from app.services.sdr_outreach_compose import persist_day1_playbook_draft

                    persist_day1_playbook_draft(
                        prospect, subject=_subj, body=body, objective="primer contacto"
                    )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("playbook regen failed prospect=%s: %s", prospect.id, exc)

    if regenerate:
        msgs = db.scalars(
            select(OutreachMessage).where(
                OutreachMessage.campaign_id == campaign_id,
                OutreachMessage.direction == "outbound",
            )
        ).all()
        by_id = {p.id: p for p in prospects}
        for msg in msgs:
            if not _copy_has_empresa(msg.message):
                continue
            if msg.gmail_message_id or msg.linkedin_message_id or msg.whatsapp_message_id:
                skipped_sent += 1
                continue
            prospect = by_id.get(msg.prospect_id)
            if prospect is None:
                continue
            channel = (msg.channel or "email").strip().lower() or "email"
            try:
                history = [m for m in (prospect.outreach_messages or []) if m.id != msg.id]
                prior = prior_touches_from_history(history)
                subject, body = generate_playbook_touch_for_prospect(
                    db,
                    campaign=campaign,
                    prospect=prospect,
                    education=education,
                    channel=channel if channel in {"email", "linkedin", "whatsapp"} else "email",
                    prior_touches=prior,
                )
                if not body:
                    continue
                if channel == "email" and subject:
                    msg.message = f"Asunto: {subject}\n\n{body}"
                else:
                    msg.message = body
                msg_regen += 1
            except Exception as exc:  # noqa: BLE001
                _logger.warning("msg regen failed id=%s: %s", msg.id, exc)

    db.commit()
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "campaign_name": campaign.name,
        "prospects": len(prospects),
        "company_names_filled": named,
        "linkedin_regenerated": li_regen,
        "whatsapp_regenerated": wa_regen,
        "messages_regenerated": msg_regen,
        "skipped_already_sent": skipped_sent,
    }
