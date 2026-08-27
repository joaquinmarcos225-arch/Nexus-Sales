"""Arma mensajes del playbook SDR para outreach real (Prospect + Campaign)."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.models.user import User
from app.services.campaign_outreach_context import company_brand_name
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK, PlaybookStepDef
from app.services.lead_sourcing.nexus_outreach_mvp import _product_dict
from app.services.lead_sourcing.sdr_playbook_outreach import generate_sdr_playbook_touch


def campaign_dict_for_sdr(db: Session, campaign: Campaign) -> dict[str, str]:
    from app.models.company import Company
    brand = company_brand_name(campaign)
    from app.services.outreach_display_names import prospect_company_display, sender_first_name

    if getattr(campaign, "company", None) is None and getattr(campaign, "company_id", None):
        campaign.company = db.get(Company, int(campaign.company_id))

    seller = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
    sender = sender_first_name(
        user=seller,
        campaign_sender=getattr(campaign, "sender_name", None),
        fallback="",
    )
    brand = company_brand_name(campaign)
    return {
        "id": str(getattr(campaign, "id", "") or ""),
        "name": campaign.name or "",
        "tone": campaign.tone or "",
        "target_role": campaign.target_role or "",
        "target_industry": campaign.target_industry or "",
        "target_country": campaign.target_country or "",
        "calendar_link": campaign.calendar_link or "",
        "sender_name": sender,
        "brand_name": brand,
        "company_name": brand,
        "seller_company_name": brand,
        "outreach_mode": str(getattr(campaign, "outreach_mode", None) or "b2b").strip().lower()
        or "b2b",
    }


def prospect_dict_for_sdr(prospect: Prospect) -> dict[str, str]:
    from app.services.outreach_display_names import prospect_company_display
    from app.services.outreach_prospect_research import research_context_for_prompt

    research = research_context_for_prompt(prospect)
    return {
        "id": str(getattr(prospect, "id", "") or ""),
        "name": prospect.name or "",
        "company_name": prospect_company_display(prospect.company_name) or "",
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
        "email": (prospect.email or "").strip(),
        "linkedin_url": (prospect.linkedin_url or "").strip(),
        "prospecting_context": research,
        "research_brief": research,
    }


def _extract_body_from_outbound_message(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""
    m = re.search(r"Asunto:\s*.+?\n\n([\s\S]+)$", text, re.I)
    if m:
        return m.group(1).strip()
    if "\n\n" in text and text.lower().startswith("["):
        parts = text.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return text


def prior_touches_from_history(history: list[OutreachMessage]) -> list[dict[str, Any]]:
    """Reconstruye toques previos del playbook a partir del historial outbound."""
    prior: list[dict[str, Any]] = []
    seen_days: set[int] = set()
    for msg in history:
        if (msg.direction or "") != "outbound":
            continue
        channel = (msg.channel or "email").strip().lower()
        body = _extract_body_from_outbound_message(msg.message or "")
        if not body or len(body) < 20:
            continue
        day = 1
        if channel == "linkedin":
            day = 4 if prior else 1
        elif channel == "whatsapp":
            day = 7 if prior else 1
        if day in seen_days:
            continue
        seen_days.add(day)
        prior.append({"day": day, "channel": channel, "body": body[:2000]})
    prior.sort(key=lambda t: int(t.get("day") or 0))
    return prior


def _step_day1_for_channel(channel: str) -> PlaybookStepDef:
    base = DEFAULT_MVP_PLAYBOOK[0]
    return PlaybookStepDef(day=base.day, channel=channel, objective=base.objective)  # type: ignore[arg-type]


def resolve_playbook_step(
    channel: str,
    prior_touches: list[dict[str, Any]],
    *,
    campaign: Campaign | None = None,
) -> PlaybookStepDef:
    from app.services.campaign_sequence_channels import effective_playbook_steps

    steps = list(effective_playbook_steps(campaign)) if campaign is not None else list(DEFAULT_MVP_PLAYBOOK)
    if not prior_touches:
        for step in steps:
            if step.channel == channel or not channel:
                return PlaybookStepDef(day=step.day, channel=channel or step.channel, objective=step.objective)
        return _step_day1_for_channel(channel)

    completed_days = {int(t.get("day") or 0) for t in prior_touches if isinstance(t, dict)}
    for step in steps:
        if step.day in completed_days:
            continue
        if channel and step.channel != channel:
            # Prefer matching channel; otherwise take next planned step remapped to requested channel.
            continue
        return step
    for step in steps:
        if step.day not in completed_days:
            return PlaybookStepDef(day=step.day, channel=channel or step.channel, objective=step.objective)
    return _step_day1_for_channel(channel)


def generate_playbook_touch_for_prospect(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    education: str,
    channel: str,
    prior_touches: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str]:
    prior = prior_touches or []
    step = resolve_playbook_step(channel, prior, campaign=campaign)

    # Research progresiva: solo primer compose (día 1, sin toques previos).
    try:
        from app.models.product import Product
        from app.services.outreach_prospect_research import (
            ensure_outreach_research,
            extract_stored_research,
            resolve_research_depth,
        )

        product = db.get(Product, int(campaign.product_id)) if campaign.product_id else None
        depth = resolve_research_depth(
            day=step.day,
            prior_touches=prior,
            has_stored_brief=bool(extract_stored_research(prospect.notes)),
            prospect=prospect,
            campaign=campaign,
        )
        if depth != "skip":
            ensure_outreach_research(
                db,
                prospect=prospect,
                campaign=campaign,
                product=product,
                force=False,
                depth=depth,
                prior_touches=prior,
                day=step.day,
            )
            db.flush()
    except Exception:
        pass

    subject, body, _reason = generate_sdr_playbook_touch(
        channel=step.channel,
        prospect=prospect_dict_for_sdr(prospect),
        campaign=campaign_dict_for_sdr(db, campaign),
        product=_product_dict(campaign),
        education=education,
        step_day=step.day,
        step_objective=step.objective,
        prior_touches=prior,
        tone=campaign.tone or "",
    )
    from app.services.outreach_display_names import scrub_generic_empresa_in_copy

    brand = company_brand_name(campaign)
    if subject:
        subject = scrub_generic_empresa_in_copy(
            subject, prospect_company=prospect.company_name, brand=brand
        )
    if body:
        body = scrub_generic_empresa_in_copy(
            body, prospect_company=prospect.company_name, brand=brand
        )
    return subject, body


def persist_day1_playbook_draft(
    prospect: Prospect,
    *,
    subject: str | None,
    body: str,
    objective: str,
) -> None:
    touch = {
        "day": 1,
        "channel": "email",
        "objective": objective,
        "subject": (subject or "").strip() or None,
        "body": body,
    }
    prospect.sequence_playbook_draft = json.dumps([touch], ensure_ascii=False)


def generate_linkedin_inbound_reply_for_prospect(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    education: str,
    inbound_text: str,
    prior_touches: list[dict[str, Any]] | None = None,
) -> str:
    """
    Réplica LinkedIn inbound vía el mismo motor de toques SDR (personalizada, concisa).
    """
    from app.services.lead_sourcing.sdr_playbook_outreach import generate_sdr_playbook_touch

    inbound = (inbound_text or "").strip()
    snippet = inbound[:320]
    objective = (
        "Réplica LinkedIn DM al mensaje inbound del prospecto (YA contestó; no es cold open). "
        f"MENSAJE DEL PROSPECTO: «{snippet}». "
        "Máximo 45 palabras. "
        "NO expliques el producto ni beneficios salvo que pregunte qué hace / cómo funciona / "
        "precio / diferencia. Si solo muestra interés: acknowledge + CTA a reunión breve. "
        "Sin plantillas de marketing."
    )
    prior = prior_touches or []
    _, body, _reason = generate_sdr_playbook_touch(
        channel="linkedin",
        prospect=prospect_dict_for_sdr(prospect),
        campaign=campaign_dict_for_sdr(db, campaign),
        product=_product_dict(campaign),
        education=education,
        step_day=4,
        step_objective=objective,
        prior_touches=prior,
        tone=campaign.tone or "",
    )
    return (body or "").strip()
