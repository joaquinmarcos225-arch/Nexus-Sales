"""Especialista Writer: redacta la respuesta según canal + intent del Brain."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect


def _price_unknown_writer_reply(
    campaign: Campaign,
    *,
    inbound_text: str,
    channel: str,
) -> dict[str, Any] | None:
    """Si pide precio y no hay hechos en ficha → copy determinístico (sin inventar)."""
    from app.services.linkedin_reply_compose import (
        build_price_unknown_email_reply,
        inbound_insists_price_before_meeting,
        price_asked,
        product_has_pricing_facts,
    )

    body = (inbound_text or "").strip()
    if not body or not price_asked(body):
        return None
    prod = getattr(campaign, "product", None)
    blobs = [
        getattr(prod, "value_proposition", None) if prod is not None else None,
        getattr(prod, "description", None) if prod is not None else None,
        getattr(prod, "target_notes", None) if prod is not None else None,
        getattr(prod, "notes", None) if prod is not None else None,
        getattr(campaign, "education_blob", None),
    ]
    if product_has_pricing_facts(*blobs):
        return None
    team_hint = None
    import re

    m_team = re.search(r"(\d+)\s*[-–]?\s*(\d+)?\s*sdrs?", body, re.I)
    if m_team:
        a, b = m_team.group(1), m_team.group(2)
        team_hint = f"{a}–{b} SDRs" if b else f"{a} SDRs"
    draft = build_price_unknown_email_reply(
        insist=inbound_insists_price_before_meeting(body),
        team_hint=team_hint,
    )
    ch = (channel or "email").strip().lower()
    if ch in ("whatsapp", "wa") and len(draft) > 700:
        draft = draft[:680].rsplit(" ", 1)[0] + "…"
    return {
        "reply_body": draft.strip(),
        "subject": "Pricing" if ch == "email" else None,
        "source": "price_unknown_gate",
        "intent": "pricing",
    }


def _ai_identity_writer_reply(
    *,
    inbound_text: str,
    channel: str,
) -> dict[str, Any] | None:
    """«¿Sos una IA?» → negación clara. Nunca defer de producto / ángulo IA."""
    from app.services.whatsapp_humanize import ai_denial_reply, is_ai_identity_question

    body = (inbound_text or "").strip()
    if not body or not is_ai_identity_question(body):
        return None
    draft = ai_denial_reply(
        continue_with="¿Qué día y horario te quedan cómodos para una charla corta?",
    )
    ch = (channel or "email").strip().lower()
    return {
        "reply_body": draft.strip(),
        "subject": None if ch in ("whatsapp", "wa", "linkedin", "li") else "Re:",
        "source": "ai_identity_denial",
        "intent": "identity",
    }


def run_writer(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    channel: str = "email",
    intent: str | None = None,
    research_contrast: str | None = None,
    reply_objective: str | None = None,
    sig: Any | None = None,
) -> dict[str, Any]:
    """
    Redacción canal-aware. Si hay contrast_reply de Research, lo prioriza
    (el orquestador puede mezclar después con booking).
    """
    ai_id = _ai_identity_writer_reply(inbound_text=inbound_text, channel=channel)
    if ai_id:
        return ai_id

    # Precio sin ficha: nunca contrast de competidor inventado ni LLM inventando cifras.
    price_gate = _price_unknown_writer_reply(
        campaign, inbound_text=inbound_text, channel=channel
    )
    if price_gate:
        return price_gate

    contrast = (research_contrast or "").strip()
    if contrast and (intent or "").strip().lower() in (
        "competitor",
        "competitor_objection",
        "ya_usamos",
        "",
    ):
        # Research ya trajo copy listo; Writer solo valida longitud.
        body = contrast
        if channel == "whatsapp" and len(body) > 700:
            body = body[:680].rsplit(" ", 1)[0] + "…"
        return {
            "reply_body": body,
            "subject": "Re:" if channel == "email" else None,
            "source": "research_contrast",
            "intent": intent or "competitor",
        }

    ch = (channel or "email").strip().lower()
    if ch in ("whatsapp", "wa"):
        from app.services.whatsapp_reply_compose import compose_whatsapp_inbound_reply

        draft = (
            compose_whatsapp_inbound_reply(
                db,
                prospect=prospect,
                campaign=campaign,
                inbound_text=inbound_text,
            )
            or ""
        ).strip()
        return {
            "reply_body": draft,
            "subject": None,
            "source": "whatsapp_compose",
            "intent": intent or reply_objective or "seguimiento",
        }

    if ch in ("linkedin", "li"):
        from app.services.linkedin_reply_compose import compose_linkedin_inbound_reply

        try:
            draft = (
                compose_linkedin_inbound_reply(
                    db,
                    prospect=prospect,
                    campaign=campaign,
                    inbound_text=inbound_text,
                )
                or ""
            ).strip()
        except TypeError:
            # Firmas alternativas en el compose LI.
            from app.services.sdr_outreach_compose import (
                generate_linkedin_inbound_reply_for_prospect,
            )

            draft = (
                generate_linkedin_inbound_reply_for_prospect(
                    db, prospect=prospect, campaign=campaign, inbound_text=inbound_text
                )
                or ""
            ).strip()
        return {
            "reply_body": draft,
            "subject": None,
            "source": "linkedin_compose",
            "intent": intent or reply_objective or "seguimiento",
        }

    # Email / default: reuse Gmail draft path via inbound_auto_reply helper.
    from app.services import conversation_intelligence as ci
    from app.services.inbound_auto_reply import _generate_reply_content

    body = (inbound_text or "").strip()
    local_sig = sig
    if local_sig is None:
        prior = (prospect.interest_level or "low").strip() or "low"
        local_sig = ci.build_signals_from_keywords(
            ci.normalize_inbound_text_for_classification(body),
            prior,
        )
    subject, reply_body, _timing, _booking = _generate_reply_content(
        db,
        campaign=campaign,
        prospect=prospect,
        body=body,
        sig=local_sig,
        history_payload=[],
        meeting_booked=False,
    )
    if contrast and reply_body:
        # Mezcla suave: contraste primero, CTA del draft después si aporta.
        reply_body = f"{contrast.rstrip()}\n\n{reply_body.strip()}"
    elif contrast:
        reply_body = contrast
    return {
        "reply_body": (reply_body or "").strip(),
        "subject": (subject or "Re:").strip() or "Re:",
        "source": "gmail_compose",
        "intent": intent or reply_objective or "seguimiento",
    }
