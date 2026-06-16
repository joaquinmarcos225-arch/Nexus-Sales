"""Borradores y envío real Gmail (OAuth del SDR)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.models.user import User
from app.models.enums import ProspectStatus
from app.schemas.gmail import (
    GmailDraftCreate,
    GmailDraftRead,
    GmailInboundSyncCreate,
    GmailInboundSyncRead,
    GmailSendCreate,
    GmailSendRead,
)
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services import multichannel_sequence as mseq
from app.services import pipeline_sync
from app.services.ai_behavior_policy import load_behavior_policy, resolve_booking_priority_from_signals
from app.services.ai_instruction_context import active_instruction_blob
from app.services.followup_engine import _campaign_dict, _product_dict
from app.services.gmail_drafts import create_draft_for_user, get_valid_gmail_connection
from app.services.gmail_inbound_sync import sync_campaign_gmail_inbound
from app.services.gmail_send import send_email_for_user
from app.services.openai_service import generate_gmail_draft_email
from app.services.outreach_simulation import make_message

router = APIRouter(tags=["gmail"])


def _apply_gmail_reply_state_machine_after_inbound(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    last_inbound_text: str | None,
    digest: str,
    education: str,
) -> bool:
    """Si hay último inbound del prospecto: clasificación + postergados / encajonados / grupos."""
    timing_soft = False
    if not last_inbound_text:
        return False
    sig = ci.classify_inbound_full(
        inbound_text=last_inbound_text,
        prior_interest=getattr(prospect, "interest_level", None),
        conversation_digest=digest,
        education=education,
    )
    followup_engine.apply_inbound_signals(
        db,
        prospect,
        objection_type=sig.objection_type,
        interest_level=sig.interest_level,
    )
    prospect.status = ci.prospect_status_from_inbound_signals(prospect.status, sig)
    timing_soft = ci.timing_deferral_should_apply(sig, inbound_text=last_inbound_text)
    if sig.objection_type == "not_interested":
        mseq.mark_encajonado(prospect)
    elif timing_soft:
        norm = ci.normalize_inbound_text_for_classification(last_inbound_text)
        resume = ci.infer_defer_resume_utc(
            inbound_text=norm,
            defer_iso=sig.defer_resume_at_iso,
            now=datetime.now(UTC),
        )
        mseq.apply_prospect_timing_deferral(
            db,
            prospect,
            campaign,
            defer_resume_at=resume,
            inbound_snippet=norm[:480],
        )
    else:
        norm = ci.normalize_inbound_text_for_classification(last_inbound_text)
        rb = bool(norm.strip()) and ci.inbound_wants_immediate_booking(norm)
        mseq.clear_postergado_state(
            db,
            prospect,
            campaign,
            reason="prioridad de agendamiento" if rb else "inbound reclasificado (sin postergación)",
        )
        mseq.promote_operational_group_after_prospect_reply(prospect)
    pipeline_sync.sync_pipeline_from_status(prospect)
    return timing_soft


def _gmail_resolve_draft_context(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    campaign_id: int,
    prospect_id: int,
) -> tuple[User, Campaign, Prospect, str, list[OutreachMessage], str, str | None]:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    user = db.get(User, user_id)
    if user is None or int(user.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")

    campaign = db.scalars(
        select(Campaign)
        .where(
            Campaign.id == campaign_id,
            Campaign.company_id == company_id,
        )
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada en esta empresa")

    if int(campaign.seller_id) != int(user_id):
        raise HTTPException(
            status_code=403,
            detail="Solo el vendedor asignado a la campaña puede usar Gmail conectado para este prospecto.",
        )

    prospect = db.get(Prospect, prospect_id)
    if prospect is None or int(prospect.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="Prospecto no encontrado en esta empresa")
    if int(prospect.campaign_id) != int(campaign_id):
        raise HTTPException(status_code=400, detail="El prospecto no pertenece a esta campaña.")

    to_addr = (prospect.email or "").strip()
    if not to_addr:
        raise HTTPException(status_code=400, detail="El prospecto no tiene email cargado.")

    history_rows = list(
        db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect.id)
            .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
        ).all()
    )
    history_payload = [
        {"sender_type": m.sender_type, "direction": m.direction, "message": m.message} for m in history_rows
    ]

    digest_lines: list[str] = []
    for item in history_payload[-18:]:
        msg = (item.get("message") or "").strip().replace("\n", " ")
        if not msg:
            continue
        digest_lines.append(
            f"- {item.get('sender_type', '?')}/{item.get('direction', '?')}: {msg[:360]}"
        )
    digest = "\n".join(digest_lines) if digest_lines else "(vacío)"

    last_inbound_text: str | None = None
    for m in reversed(history_rows):
        if m.direction == "inbound" and m.sender_type == "prospect":
            tin = (m.message or "").strip()
            if tin:
                last_inbound_text = tin
                break

    return user, campaign, prospect, to_addr, history_rows, digest, last_inbound_text


def _gmail_campaign_context(campaign: Campaign) -> dict:
    d = _campaign_dict(campaign)
    d["target_company_size"] = campaign.target_company_size or ""
    d["target_language"] = campaign.target_language or ""
    raw = campaign.icp_ai_last_analysis
    if raw is None:
        d["icp_ai_digest"] = ""
    elif isinstance(raw, (dict, list)):
        try:
            d["icp_ai_digest"] = json.dumps(raw, ensure_ascii=False)[:2000]
        except (TypeError, ValueError):
            d["icp_ai_digest"] = ""
    else:
        d["icp_ai_digest"] = str(raw)[:2000]
    return d


@router.post("/gmail/drafts", response_model=GmailDraftRead)
def post_gmail_draft(
    payload: GmailDraftCreate,
    db: Session = Depends(get_db),
) -> GmailDraftRead:
    _user, campaign, prospect, to_addr, history_rows, digest, last_inbound_text = _gmail_resolve_draft_context(
        db,
        company_id=payload.company_id,
        user_id=payload.user_id,
        campaign_id=payload.campaign_id,
        prospect_id=payload.prospect_id,
    )

    history_payload = [
        {"sender_type": m.sender_type, "direction": m.direction, "message": m.message} for m in history_rows
    ]

    education = active_instruction_blob(db, payload.company_id)
    campaign_ctx = _gmail_campaign_context(campaign)
    product_ctx = _product_dict(campaign)
    prospect_ctx = {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
        "email": to_addr,
    }

    norm_in = (
        ci.normalize_inbound_text_for_classification(last_inbound_text) if last_inbound_text else ""
    )
    policy = load_behavior_policy(db, payload.company_id)
    reply_sig = None
    if norm_in.strip():
        reply_sig = ci.classify_inbound_full(
            inbound_text=last_inbound_text or "",
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

    timing_soft = _apply_gmail_reply_state_machine_after_inbound(
        db,
        prospect=prospect,
        campaign=campaign,
        last_inbound_text=last_inbound_text,
        digest=digest,
        education=education,
    )
    timing_soft = timing_soft and not booking_priority

    subject, body = generate_gmail_draft_email(
        prospect=prospect_ctx,
        campaign=campaign_ctx,
        product=product_ctx,
        tone=campaign.tone,
        education=education,
        conversation_history=history_payload,
        last_prospect_inbound=ci.normalize_inbound_text_for_classification(last_inbound_text)
        if last_inbound_text
        else None,
        prospect_timing_soft=timing_soft,
        prospect_booking_priority=booking_priority,
        ai_policy=policy,
        interest_level=reply_sig.interest_level if reply_sig else None,
        prospect_wants_meeting=bool(reply_sig.prospect_wants_meeting) if reply_sig else False,
        explicit_meeting_commitment=bool(reply_sig.explicit_meeting_commitment) if reply_sig else False,
        prospect_substantive_questions=bool(reply_sig.asks_concrete_questions) if reply_sig else False,
    )

    try:
        out = create_draft_for_user(
            db,
            company_id=payload.company_id,
            user_id=payload.user_id,
            to_addr=to_addr,
            subject=subject,
            body=body,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:800] if e.response is not None else str(e)
        raise HTTPException(status_code=502, detail=f"Gmail API: {detail}") from e

    tid = (out.get("thread_id") or "").strip()
    if tid:
        prospect.gmail_thread_id = tid

    hist_text = (
        "[Borrador Gmail · no enviado]\n"
        f"Asunto: {subject}\n\n{body}"
    )
    db.add(
        make_message(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="system",
            message=hist_text,
            channel="email",
            direction="outbound",
        )
    )
    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "email"
    db.commit()

    return GmailDraftRead(
        draft_id=out.get("draft_id") or "",
        message_id=out.get("message_id"),
        gmail_web_link=out.get("gmail_web_link"),
        subject=subject,
    )


@router.post("/gmail/send", response_model=GmailSendRead)
def post_gmail_send(
    payload: GmailSendCreate,
    db: Session = Depends(get_db),
) -> GmailSendRead:
    if not payload.confirm_send:
        raise HTTPException(
            status_code=400,
            detail="Pasá confirm_send=true sólo cuando quieras enviar el correo real por Gmail API.",
        )

    _, campaign, prospect, to_addr, history_rows, digest, last_inbound_text = _gmail_resolve_draft_context(
        db,
        company_id=payload.company_id,
        user_id=payload.user_id,
        campaign_id=payload.campaign_id,
        prospect_id=payload.prospect_id,
    )
    history_payload = [
        {"sender_type": m.sender_type, "direction": m.direction, "message": m.message} for m in history_rows
    ]

    education = active_instruction_blob(db, payload.company_id)
    campaign_ctx = _gmail_campaign_context(campaign)
    product_ctx = _product_dict(campaign)
    prospect_ctx = {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
        "email": to_addr,
    }

    norm_in = (
        ci.normalize_inbound_text_for_classification(last_inbound_text) if last_inbound_text else ""
    )
    policy = load_behavior_policy(db, payload.company_id)
    reply_sig = None
    if norm_in.strip():
        reply_sig = ci.classify_inbound_full(
            inbound_text=last_inbound_text or "",
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

    timing_soft = _apply_gmail_reply_state_machine_after_inbound(
        db,
        prospect=prospect,
        campaign=campaign,
        last_inbound_text=last_inbound_text,
        digest=digest,
        education=education,
    )
    timing_soft = timing_soft and not booking_priority

    sub_in = (payload.subject or "").strip()
    body_in = (payload.body or "").strip()
    if sub_in and body_in:
        subject, body = sub_in, body_in
    else:
        subject, body = generate_gmail_draft_email(
            prospect=prospect_ctx,
            campaign=campaign_ctx,
            product=product_ctx,
            tone=campaign.tone,
            education=education,
            conversation_history=history_payload,
            last_prospect_inbound=ci.normalize_inbound_text_for_classification(last_inbound_text)
            if last_inbound_text
            else None,
            prospect_timing_soft=timing_soft,
            prospect_booking_priority=booking_priority,
            ai_policy=policy,
            interest_level=reply_sig.interest_level if reply_sig else None,
            prospect_wants_meeting=bool(reply_sig.prospect_wants_meeting) if reply_sig else False,
            explicit_meeting_commitment=bool(reply_sig.explicit_meeting_commitment) if reply_sig else False,
            prospect_substantive_questions=bool(reply_sig.asks_concrete_questions) if reply_sig else False,
        )

    _access, gmail_row = get_valid_gmail_connection(db, company_id=payload.company_id, user_id=payload.user_id)
    from_addr = (gmail_row.external_email or "").strip()
    if not from_addr:
        u = db.get(User, payload.user_id)
        from_addr = ((u.email if u else None) or "").strip()
    if not from_addr:
        raise HTTPException(
            status_code=400,
            detail="No hay dirección remitente (external_email de Gmail ni email de usuario). Reconectá Google.",
        )

    thread_id = (prospect.gmail_thread_id or "").strip() or None
    try:
        out = send_email_for_user(
            db,
            company_id=payload.company_id,
            user_id=payload.user_id,
            from_addr=from_addr,
            to_addr=to_addr,
            subject=subject,
            body=body,
            thread_id=thread_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:800] if e.response is not None else str(e)
        raise HTTPException(status_code=502, detail=f"Gmail API: {detail}") from e

    gid = (out.get("gmail_message_id") or "").strip() or None
    tid = (out.get("thread_id") or "").strip() or None
    if tid:
        prospect.gmail_thread_id = tid

    hist_text = f"[Gmail · envío real]\nAsunto: {subject}\n\n{body}"
    om_rec = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="user",
        message=hist_text,
        channel="email",
        direction="outbound",
        gmail_message_id=gid,
    )
    db.add(om_rec)
    db.flush()

    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=body,
    )
    if prospect.status in (ProspectStatus.imported.value, ProspectStatus.compatible.value):
        prospect.status = ProspectStatus.contacted.value
        pipeline_sync.sync_pipeline_from_status(prospect)

    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "email"

    db.commit()
    db.refresh(om_rec)

    return GmailSendRead(
        gmail_message_id=gid,
        thread_id=tid or None,
        gmail_web_link=out.get("gmail_web_link"),
        subject=subject,
        outreach_message_id=om_rec.id,
    )


@router.post("/gmail/sync-inbound", response_model=GmailInboundSyncRead)
def post_gmail_sync_inbound(
    payload: GmailInboundSyncCreate,
    db: Session = Depends(get_db),
) -> GmailInboundSyncRead:
    """
    Lee el buzón Gmail del vendedor, importa respuestas reales de prospectos de la campaña
    y dispara clasificación + Postergados / tareas como en la simulación.
    """
    company = db.get(Company, payload.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    user = db.get(User, payload.user_id)
    if user is None or int(user.company_id) != int(payload.company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")

    try:
        stats = sync_campaign_gmail_inbound(
            db,
            company_id=payload.company_id,
            user_id=payload.user_id,
            campaign_id=payload.campaign_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    db.commit()
    return GmailInboundSyncRead.model_validate(stats)
