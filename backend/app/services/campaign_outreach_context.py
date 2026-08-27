"""Marca, remitente e ICP para copy de outreach (evita usar nombre de campaña como empresa)."""

from __future__ import annotations

import json

from app.models.campaign import Campaign
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services.campaign_market import normalize_outreach_mode
from app.services.outreach_display_names import outreach_company_display


def company_brand_name(campaign: Campaign) -> str:
    """
    Nombre de la empresa vendedora para mensajes («te hablo desde X»).

    Fuente: Company.name del tenant (relación campaign.company).
    Se limpian tokens Demo/Test/Client del seed; nunca se usa «Nexus» ni el
    nombre interno de la campaña como marca.
    """
    company = getattr(campaign, "company", None)
    if company is not None:
        cleaned = outreach_company_display(getattr(company, "name", None))
        if cleaned:
            return cleaned
    # Sin empresa cargada / sin nombre usable: vacío (el compose evita inventar marca).
    return ""


def icp_ai_digest(campaign: Campaign) -> str:
    raw = getattr(campaign, "icp_ai_last_analysis", None)
    if isinstance(raw, dict):
        return str(
            raw.get("recommendations") or raw.get("notes") or raw.get("summary") or ""
        )[:1200]
    if isinstance(raw, str):
        return raw[:1200]
    if raw is not None:
        try:
            return json.dumps(raw, ensure_ascii=False)[:1200]
        except (TypeError, ValueError):
            return str(raw)[:1200]
    return ""


def campaign_dict_for_outreach(campaign: Campaign) -> dict[str, str]:
    ch = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    brand = company_brand_name(campaign)
    mode = normalize_outreach_mode(getattr(campaign, "outreach_mode", None))
    return {
        "name": campaign.name or "",
        "tone": campaign.tone or "",
        "outreach_mode": mode,
        "target_company_size": campaign.target_company_size or "",
        "target_industry": campaign.target_industry or "",
        "target_country": campaign.target_country or "",
        "target_language": campaign.target_language or "",
        "target_role": campaign.target_role or "",
        "target_area": getattr(campaign, "target_area", None) or "",
        "target_interests": getattr(campaign, "target_interests", None) or "",
        "preferred_channel_hint": " → ".join(ch),
        "allowed_channels_csv": ",".join(ch),
        "calendar_link": campaign.calendar_link or "",
        "sender_name": (getattr(campaign, "sender_name", None) or "").strip(),
        "sender_email": (getattr(campaign, "sender_email", None) or "").strip(),
        "brand_name": brand,
        "company_name": brand,
        "seller_company_name": brand,
        "icp_ai_digest": icp_ai_digest(campaign),
    }
