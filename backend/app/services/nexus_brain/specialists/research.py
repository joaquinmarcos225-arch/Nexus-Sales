"""Especialista Research: «ya usamos X» → qué es X y contraste vs producto."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect


def run_research(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    competitor_hint: str | None = None,
) -> dict[str, Any]:
    """
    Research de competencia + contraste con el producto de la campaña.
    Fail-open: si no hay tool, devuelve empty contrast.
    """
    from app.services.competitor_contrast import (
        brief_competitor_facts,
        build_competitor_contrast_reply,
        extract_competitor_name,
        inbound_claims_using_competitor,
        is_own_product_name,
        sanitize_competitor_hint,
    )

    body = (inbound_text or "").strip()
    hint = (competitor_hint or "").strip()
    # Precio/número: nunca research de «Agendar» fantasma por «antes de agendar».
    try:
        from app.services.linkedin_reply_compose import price_asked

        if price_asked(body):
            return {
                "found": False,
                "competitor": "",
                "facts": [],
                "contrast_reply": "",
                "they_use": False,
                "skipped": "price_question",
            }
    except Exception:
        pass
    # Hint del agente suele ser basura («Nexus Sales uso en equipos…», «precio…»).
    other = sanitize_competitor_hint(hint, campaign=campaign) if hint else ""
    if not other:
        other = extract_competitor_name(body)
    if not other or is_own_product_name(other, campaign):
        return {
            "found": False,
            "competitor": "",
            "facts": [],
            "contrast_reply": "",
            "they_use": False,
        }

    country = (getattr(prospect, "country", None) or "").strip() or None
    facts: list[str] = []
    try:
        facts = brief_competitor_facts(other, country=country) or []
    except Exception:
        facts = []

    contrast = ""
    try:
        contrast = build_competitor_contrast_reply(
            campaign,
            body if inbound_claims_using_competitor(body) or extract_competitor_name(body) else f"ya usamos {other}",
            country=country,
        )
    except Exception:
        contrast = ""

    return {
        "found": True,
        "competitor": other,
        "facts": facts[:5],
        "contrast_reply": (contrast or "").strip(),
        "they_use": inbound_claims_using_competitor(body) or bool(competitor_hint),
        "prospect_id": int(prospect.id),
        "db_touched": False,
    }
