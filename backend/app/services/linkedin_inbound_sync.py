"""Registrar respuestas LinkedIn inbound → pausa secuencia + borrador de réplica SDR."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services import multichannel_sequence as mseq
from app.services import pipeline_sync
from app.services.ai_instruction_context import campaign_education_blob
from app.services.gmail_inbound_sync import (
    _conversation_digest_rows,
    _has_pending_hot_lead,
    _has_pending_review_inbound,
)
from app.services.outreach_simulation import make_message
from app.services.linkedin_reply_delay import apply_reply_queue_delay

logger = logging.getLogger(__name__)

LINKEDIN_INBOUND_PREFIX = "[LinkedIn · respuesta real]"


def _normalize_li_echo_text(text: str | None) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def _strip_linkedin_outbound_prefix(text: str) -> str:
    body = (text or "").strip()
    for prefix in (
        "[LinkedIn · enviado por SDR]",
        "[LinkedIn · respuesta real]",
        "[LinkedIn]",
    ):
        if body.startswith(prefix):
            body = body.split("\n", 1)[-1].strip()
    return body


def _looks_like_nexus_meeting_confirmation(text: str | None) -> bool:
    """
    Confirmaciones que genera Nexus al agendar (nunca son inbound del prospecto).
    Ej.: «Perfecto. Te agendé para Lunes 10:00. Acá tenés la invitación: …»
    """
    t = _normalize_li_echo_text(_strip_linkedin_outbound_prefix(text))
    if len(t) < 12:
        return False
    if "te agendé" in t or "te agende" in t:
        return True
    if "moví la reunión" in t or "movi la reunion" in t:
        return True
    if "acá tenés la invitación" in t or "aca tenes la invitacion" in t:
        return True
    if "te comparto la invitación" in t or "te comparto la invitacion" in t:
        return True
    if "calendar.google.com/calendar/event" in t and (
        "perfecto" in t or "listo" in t or "nos vemos" in t
    ):
        return True
    return False


def _is_echo_of_our_linkedin_outbound(
    db: Session,
    *,
    prospect: Prospect,
    inbound_plain: str,
) -> bool:
    """True si el 'inbound' es en realidad nuestro propio outbound (eco de snippet/UI)."""
    inbound = _normalize_li_echo_text(inbound_plain)
    if len(inbound) < 12:
        return False

    # Confirmación de reunión de Nexus: nunca es respuesta del prospecto.
    if _looks_like_nexus_meeting_confirmation(inbound_plain):
        return True

    draft = _normalize_li_echo_text(getattr(prospect, "linkedin_assisted_draft", None) or "")
    if draft and (inbound[:80] in draft or draft[:80] in inbound or inbound == draft):
        return True

    try:
        last_out = db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.channel == "linkedin",
                OutreachMessage.direction == "outbound",
            )
            .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
            .limit(1)
        ).first()
    except Exception:
        return False
    if last_out is None:
        return False
    out_body = _normalize_li_echo_text(
        _strip_linkedin_outbound_prefix(getattr(last_out, "message", None) or "")
    )
    if not out_body:
        return False
    return inbound[:80] in out_body or out_body[:80] in inbound or inbound == out_body


def _linkedin_dedup_id(*, prospect_id: int, text: str, external_id: str | None) -> str:
    ext = (external_id or "").strip()
    if ext:
        return ext[:128]
    digest = hashlib.sha256(f"{prospect_id}:{text.strip()[:500]}".encode("utf-8")).hexdigest()[:32]
    return f"hash:{digest}"


def process_linkedin_inbound_for_prospect(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound_plain: str,
    linkedin_message_id: str | None = None,
) -> bool:
    """
    Persiste inbound LinkedIn (dedupe por linkedin_message_id) y dispara reglas.
    Devuelve True si insertó un mensaje nuevo.
    """
    body = (inbound_plain or "").strip()
    if len(body) < 2:
        return False

    dedup_id = _linkedin_dedup_id(prospect_id=prospect.id, text=body, external_id=linkedin_message_id)
    exists = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.linkedin_message_id == dedup_id,
        )
    )
    if int(exists or 0) > 0:
        return False

    display = f"{LINKEDIN_INBOUND_PREFIX}\n{body}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="prospect",
        message=display,
        channel="linkedin",
        direction="inbound",
        linkedin_message_id=dedup_id,
    )
    db.add(msg)
    db.flush()

    followup_engine.record_prospect_inbound(db, prospect)
    mseq.on_inbound_pause_sequence(db, prospect)

    education = campaign_education_blob(db, campaign)
    digest = _conversation_digest_rows(db, prospect.id)
    sig = ci.classify_inbound_full(
        inbound_text=body,
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
    followup_engine.cancel_pending_followup_tasks(db, prospect.id)
    if not _has_pending_review_inbound(db, prospect.id):
        followup_engine.create_review_inbound_task(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
        )
    if sig.interest_level == "high" and not (
        sig.prospect_timing_hold or sig.objection_type == "timing"
    ):
        if not _has_pending_hot_lead(db, prospect.id):
            followup_engine.create_hot_lead_task(
                db,
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                prospect_id=prospect.id,
            )

    prospect.status = ci.prospect_status_from_inbound_signals(prospect.status, sig)
    pipeline_sync.sync_pipeline_from_status(prospect)

    from app.services import prospect_commercial_state as pcs

    pcs.sync_commercial_state_from_inbound(
        db,
        prospect=prospect,
        inbound_text=body,
        sig=sig,
        testing=False,
    )

    if mseq.prospect_in_meeting_priority(db, prospect):
        mseq.enforce_meeting_priority_over_sequence(db, prospect, campaign)
        mseq._append_log(
            campaign,
            f"Nexus detectó respuesta por LinkedIn · {prospect.name or prospect.id} (prioridad reunión)",
            kind="inbound",
        )
        logger.info(
            "linkedin inbound (meeting priority) prospect_id=%s linkedin_message_id=%s",
            prospect.id,
            dedup_id,
        )
        try:
            from app.services.crm import sync as crm_sync

            crm_sync.sync_inbound_reply(
                db,
                prospect=prospect,
                channel="linkedin",
                message_id=dedup_id,
                message_body=body,
            )
        except Exception:
            logger.exception("crm inbound sync failed prospect_id=%s", prospect.id)
        return True

    timing_soft = ci.timing_deferral_should_apply(sig, inbound_text=body)
    if sig.objection_type == "not_interested":
        mseq.mark_encajonado(prospect)
    elif timing_soft:
        resume = ci.infer_defer_resume_utc(
            inbound_text=body,
            defer_iso=sig.defer_resume_at_iso,
            now=datetime.now(UTC),
        )
        mseq.apply_prospect_timing_deferral(
            db,
            prospect,
            campaign,
            defer_resume_at=resume,
            inbound_snippet=body[:480],
        )
    else:
        norm_body = ci.normalize_inbound_text_for_classification(body)
        rb = bool(norm_body.strip()) and ci.inbound_wants_immediate_booking(norm_body)
        mseq.clear_postergado_state(
            db,
            prospect,
            campaign,
            reason="prioridad de agendamiento" if rb else "inbound reclasificado (sin postergación)",
        )
        mseq.promote_operational_group_after_prospect_reply(prospect)

    pipeline_sync.sync_pipeline_from_status(prospect)

    mseq._append_log(
        campaign,
        f"Nexus detectó respuesta por LinkedIn · {prospect.name or prospect.id}",
        kind="inbound",
    )

    logger.info(
        "linkedin inbound processed prospect_id=%s campaign_id=%s linkedin_message_id=%s",
        prospect.id,
        campaign.id,
        dedup_id,
    )
    try:
        from app.services.crm import sync as crm_sync

        crm_sync.sync_inbound_reply(
            db,
            prospect=prospect,
            channel="linkedin",
            message_id=dedup_id,
            message_body=body,
        )
    except Exception:
        logger.exception("crm inbound sync failed prospect_id=%s", prospect.id)
    return True


def register_linkedin_inbound(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    message: str,
    linkedin_message_id: str | None = None,
    prepare_reply_draft: bool = True,
) -> dict[str, Any]:
    """API/extension: registra inbound y opcionalmente genera borrador de réplica."""
    from app.services import linkedin_assisted_service as las

    if _is_echo_of_our_linkedin_outbound(db, prospect=prospect, inbound_plain=message):
        logger.info(
            "linkedin inbound ignored as outbound echo prospect_id=%s",
            prospect.id,
        )
        return {
            "inserted": False,
            "sequence_paused": bool(prospect.sequence_paused),
            "reply_draft": None,
            "reply_draft_ready": False,
            "echo_ignored": True,
        }

    inserted = process_linkedin_inbound_for_prospect(
        db,
        prospect=prospect,
        campaign=campaign,
        inbound_plain=message,
        linkedin_message_id=linkedin_message_id,
    )
    draft: str | None = None
    reply_available_at: datetime | None = None
    if inserted and prepare_reply_draft:
        try:
            draft = las.prepare_linkedin_reply_after_inbound(db, prospect, campaign)
            reply_available_at = apply_reply_queue_delay(prospect)
        except Exception:
            logger.exception(
                "linkedin reply draft failed prospect_id=%s — inbound kept, using fallback",
                prospect.id,
            )
            from app.services.linkedin_reply_compose import compose_linkedin_inbound_reply

            draft = compose_linkedin_inbound_reply(
                db,
                prospect=prospect,
                campaign=campaign,
                inbound_text=message,
            )
            las.mark_draft_suggested(db, prospect, campaign, draft, log_event=True)
            reply_available_at = apply_reply_queue_delay(prospect)

    return {
        "inserted": inserted,
        "sequence_paused": bool(prospect.sequence_paused),
        "reply_draft": draft,
        "reply_draft_ready": bool((draft or "").strip()),
        "reply_available_at": reply_available_at,
        "duplicate": not inserted,
    }
