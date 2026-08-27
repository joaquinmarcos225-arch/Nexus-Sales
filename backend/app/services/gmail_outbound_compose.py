"""Composición unificada de asunto/cuerpo para envíos Gmail (manual y automático)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services.ai_behavior_policy import load_behavior_policy, resolve_booking_priority_from_signals
from app.services.ai_instruction_context import campaign_education_blob
from app.services.meeting_booking import (
    attempt_auto_book_from_message,
    prepare_agendar_reply_with_calendar_link,
    prospect_has_calendar_confirmed_meeting,
)
from app.services.openai_service import generate_gmail_draft_email


def _campaign_ctx(campaign: Campaign) -> dict:
    return followup_engine._campaign_dict(campaign)


def _product_ctx(campaign: Campaign) -> dict:
    return followup_engine._product_dict(campaign)


def _prospect_ctx(prospect: Prospect, email: str) -> dict:
    return {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
        "email": email,
    }


def compose_gmail_subject_and_body(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    to_addr: str,
    history_payload: list[dict[str, str]],
    last_inbound_text: str | None,
    digest: str,
    manual_subject: str | None = None,
    manual_body: str | None = None,
    inbound_plain_override: str | None = None,
) -> tuple[str, str]:
    """Genera asunto y cuerpo. Prioriza confirmaciones determinísticas de reunión sobre IA."""
    sub_in = (manual_subject or "").strip()
    body_in = (manual_body or "").strip()
    if sub_in and body_in:
        return sub_in, body_in

    from app.services.gmail_inbound_sync import extract_prospect_inbound_plain

    raw_inbound = (inbound_plain_override or "").strip()
    if not raw_inbound:
        raw_inbound = extract_prospect_inbound_plain(last_inbound_text)

    education = campaign_education_blob(db, campaign)
    policy = load_behavior_policy(db, campaign.company_id)
    norm_in = (
        ci.normalize_inbound_text_for_classification(raw_inbound) if raw_inbound else ""
    )
    reply_sig = None
    if norm_in.strip():
        reply_sig = ci.classify_inbound_full(
            inbound_text=raw_inbound,
            prior_interest=getattr(prospect, "interest_level", None),
            conversation_digest=digest,
            education=education,
        )

    booking_priority = resolve_booking_priority_from_signals(
        policy,
        inbound_text=norm_in,
        explicit_meeting_commitment=bool(reply_sig.explicit_meeting_commitment) if reply_sig else False,
        prospect_wants_meeting=bool(reply_sig.prospect_wants_meeting) if reply_sig else False,
        interest_level=reply_sig.interest_level if reply_sig else None,
    )
    timing_soft = (
        reply_sig is not None
        and reply_sig.objection_type != "not_interested"
        and ci.timing_deferral_should_apply(reply_sig, inbound_text=raw_inbound)
        and not booking_priority
    )

    response_class = None
    reply_objective = None
    if reply_sig and norm_in.strip():
        response_class, _ = ci.classify_commercial_response(raw_inbound, reply_sig)
        reply_objective = ci.resolve_reply_objective(
            text=raw_inbound,
            sig=reply_sig,
            response_class=response_class,
        )

    meeting_booking = None
    inbound_for_booking = norm_in
    if inbound_for_booking.strip() and (
        booking_priority
        or ci.inbound_has_explicit_meeting_slot(inbound_for_booking)
        or (reply_objective or "").strip().lower() == "agendar"
    ):
        meeting_booking = attempt_auto_book_from_message(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=inbound_for_booking,
            reply_objective=reply_objective,
            sig=reply_sig,
            testing=False,
        )

    if meeting_booking and meeting_booking.get("confirmation_reply"):
        return "Re: reunión", str(meeting_booking["confirmation_reply"])
    if meeting_booking and meeting_booking.get("requires_calendar_reconnect"):
        # No enviar al prospecto un texto de Configuración/Integraciones.
        raise RuntimeError("calendar_reconnect_required")
    if meeting_booking and meeting_booking.get("booking_failed_reply"):
        return "Re: reunión", str(meeting_booking["booking_failed_reply"])
    if meeting_booking and meeting_booking.get("alternatives_reply"):
        return "Re: horarios", str(meeting_booking["alternatives_reply"])

    meeting_confirmed = prospect_has_calendar_confirmed_meeting(db, prospect)
    if meeting_confirmed and not booking_priority:
        subject, body = generate_gmail_draft_email(
            prospect=_prospect_ctx(prospect, to_addr),
            campaign=_campaign_ctx(campaign),
            product=_product_ctx(campaign),
            tone=campaign.tone,
            education=education,
            conversation_history=history_payload,
            last_prospect_inbound=norm_in or None,
            prospect_timing_soft=False,
            prospect_booking_priority=False,
            meeting_already_booked=True,
            ai_policy=policy,
        )
        return subject, body

    if norm_in.strip() and booking_priority:
        subject = "Re:"
        body = ""
    else:
        subject, body = generate_gmail_draft_email(
            prospect=_prospect_ctx(prospect, to_addr),
            campaign=_campaign_ctx(campaign),
            product=_product_ctx(campaign),
            tone=campaign.tone,
            education=education,
            conversation_history=history_payload,
            last_prospect_inbound=norm_in or None,
            prospect_timing_soft=timing_soft,
            prospect_booking_priority=booking_priority,
            prospect_substantive_questions=bool(
                reply_sig and (
                    reply_sig.asks_concrete_questions
                    or reply_sig.objection_type in ("send_info", "other")
                )
            ),
            ai_policy=policy,
            interest_level=reply_sig.interest_level if reply_sig else None,
            prospect_wants_meeting=bool(reply_sig.prospect_wants_meeting) if reply_sig else False,
            explicit_meeting_commitment=bool(reply_sig.explicit_meeting_commitment) if reply_sig else False,
            reply_objective=reply_objective,
            response_class=response_class,
            meeting_already_booked=meeting_confirmed,
        )

    body = prepare_agendar_reply_with_calendar_link(
        prospect=prospect,
        campaign=campaign,
        reply_objective=reply_objective,
        suggested_reply=body,
    )
    if not subject.strip():
        subject = "Re:"
    return subject, body
