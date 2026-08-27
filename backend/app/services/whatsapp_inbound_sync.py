"""Inbound WhatsApp automático → pausa secuencia + borrador de réplica SDR."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
from app.services.whatsapp_cloud_service import (
    meta_api_recipient_candidates,
    normalize_whatsapp_digits,
)

logger = logging.getLogger(__name__)

WHATSAPP_INBOUND_PREFIX = "[WhatsApp · respuesta real]"

# IDs sintéticos de la extensión: no sirven como dedupe (cambian con tipitos / path).
_WA_SYNTHETIC_ID_PREFIXES = ("wa-store:", "wa-list:", "wa-in:", "wa-hash:")


def _normalize_wa_echo_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    body = text.strip().lower()
    for prefix in (
        WHATSAPP_INBOUND_PREFIX.lower(),
        "[whatsapp · enviado por sdr]",
        "[whatsapp",
    ):
        if body.startswith(prefix):
            body = body.split("\n", 1)[-1].strip()
            break
    body = re.sub(r"^(t[uú]|you|vos)\s*:\s*", "", body)
    body = re.sub(r"^\s*[✓✔]+\s*", "", body)
    body = re.sub(r"\s+", " ", body)
    return body[:240]


def _normalize_wa_dedup_compact(text: str) -> str:
    """Huella estable: minúsculas, sin tildes/puntuación/espacios (tipitos de OCR/lista)."""
    body = _normalize_wa_echo_text(text)
    body = unicodedata.normalize("NFD", body)
    body = re.sub(r"[\u0300-\u036f]", "", body)
    body = re.sub(r"[^\w\s]", "", body, flags=re.UNICODE)
    body = re.sub(r"\s+", "", body)
    return body[:240]


def _wa_texts_look_like_same_message(a: str, b: str) -> bool:
    """True si a y b son el mismo mensaje (eco / preview truncado / tipitos leves)."""
    if not a or not b:
        return False
    if a == b:
        return True
    ca, cb = _normalize_wa_dedup_compact(a), _normalize_wa_dedup_compact(b)
    if ca and cb and ca == cb:
        return True
    # Preview de lista suele truncar; comparar cabezas.
    n = min(48, len(a), len(b))
    if n >= 16 and (a[:n] == b[:n] or a[:n] in b or b[:n] in a):
        return True
    if len(a) >= 20 and a[:20] in b:
        return True
    if len(b) >= 20 and b[:20] in a:
        return True
    # Uno contenido en el otro (mínimo razonable).
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 24 and shorter in longer:
        return True
    # Tipitos leves en mensajes cortos ("oas" vs "las").
    if ca and cb and min(len(ca), len(cb)) >= 10:
        if SequenceMatcher(None, ca, cb).ratio() >= 0.88:
            return True
    return False


def _is_echo_of_our_whatsapp_outbound(
    db: Session,
    *,
    prospect: Prospect,
    inbound_plain: str,
) -> bool:
    """True si el 'inbound' es en realidad nuestro propio envío / borrador (eco de WA Web)."""
    inbound = _normalize_wa_echo_text(inbound_plain)
    if len(inbound) < 8:
        return False

    draft = _normalize_wa_echo_text(getattr(prospect, "whatsapp_assisted_draft", None) or "")
    if draft and _wa_texts_look_like_same_message(inbound, draft):
        return True

    try:
        recent_out = list(
            db.scalars(
                select(OutreachMessage)
                .where(
                    OutreachMessage.prospect_id == prospect.id,
                    OutreachMessage.channel == "whatsapp",
                    OutreachMessage.direction == "outbound",
                )
                .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
                .limit(5)
            ).all()
        )
    except Exception:
        return False

    for last_out in recent_out:
        out_body = _normalize_wa_echo_text(getattr(last_out, "message", None) or "")
        if out_body and _wa_texts_look_like_same_message(inbound, out_body):
            return True
    return False


def _wa_dedup_id(*, prospect_id: int, text: str, external_id: str | None) -> str:
    ext = (external_id or "").strip()
    # wamid de Meta u otros IDs reales: dedupe exacto.
    if ext and not ext.startswith(_WA_SYNTHETIC_ID_PREFIXES):
        return ext[:128]
    compact = _normalize_wa_dedup_compact(text) or text.strip()[:500]
    digest = hashlib.sha256(f"{prospect_id}:{compact}".encode("utf-8")).hexdigest()[:32]
    return f"wa-hash:{digest}"


def _recent_whatsapp_inbound_is_duplicate(
    db: Session,
    *,
    prospect_id: int,
    inbound_plain: str,
    limit: int = 30,
) -> bool:
    """True si ya hay un inbound WA reciente casi igual (tipitos / re-poll extensión)."""
    inbound = _normalize_wa_echo_text(inbound_plain)
    if len(inbound) < 2:
        return False
    try:
        rows = list(
            db.scalars(
                select(OutreachMessage)
                .where(
                    OutreachMessage.prospect_id == int(prospect_id),
                    OutreachMessage.channel == "whatsapp",
                    OutreachMessage.direction == "inbound",
                )
                .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
                .limit(limit)
            ).all()
        )
    except Exception:
        return False
    for row in rows:
        prev = _normalize_wa_echo_text(getattr(row, "message", None) or "")
        if prev and _wa_texts_look_like_same_message(inbound, prev):
            return True
    return False


def resolve_prospect_by_whatsapp_digits(
    db: Session,
    *,
    company_id: int | None,
    from_digits: str,
) -> Prospect | None:
    """Encuentra prospecto por teléfono/WhatsApp (variantes AR incluidas)."""
    digits = re.sub(r"\D", "", from_digits or "")
    if len(digits) < 8:
        return None
    candidates = meta_api_recipient_candidates(digits, digits)
    if digits not in candidates:
        candidates = [digits, *candidates]

    stmt = select(Prospect)
    if company_id is not None:
        stmt = stmt.where(Prospect.company_id == int(company_id))
    rows = db.scalars(stmt.order_by(Prospect.id.desc()).limit(500)).all()
    for p in rows:
        for cand in candidates:
            for field in (p.whatsapp, p.phone):
                norm = normalize_whatsapp_digits(field, field)
                if not norm:
                    continue
                variants = set(meta_api_recipient_candidates(field, field))
                variants.add(norm)
                if cand in variants:
                    return p
    return None


def process_whatsapp_inbound_for_prospect(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound_plain: str,
    whatsapp_message_id: str | None = None,
) -> bool:
    """Persiste inbound WhatsApp (dedupe) y dispara reglas. True si insertó."""
    body = (inbound_plain or "").strip()
    if len(body) < 1:
        return False

    dedup_id = _wa_dedup_id(prospect_id=prospect.id, text=body, external_id=whatsapp_message_id)
    exists = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.whatsapp_message_id == dedup_id,
        )
    )
    if int(exists or 0) > 0:
        return False
    if _recent_whatsapp_inbound_is_duplicate(db, prospect_id=prospect.id, inbound_plain=body):
        return False

    display = f"{WHATSAPP_INBOUND_PREFIX}\n{body}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="prospect",
        message=display,
        channel="whatsapp",
        direction="inbound",
        whatsapp_message_id=dedup_id,
    )
    try:
        # SAVEPOINT: si hay race en el unique index, no ensucia la transacción outer.
        with db.begin_nested():
            db.add(msg)
            db.flush()
    except IntegrityError:
        logger.info(
            "whatsapp inbound race-deduped prospect_id=%s dedup_id=%s",
            prospect.id,
            dedup_id,
        )
        return False
    except Exception:
        # Fallback sin SAVEPOINT (algunos drivers/configs).
        logger.exception(
            "whatsapp inbound nested flush failed prospect_id=%s — retry plain flush",
            prospect.id,
        )
        try:
            db.add(msg)
            db.flush()
        except IntegrityError:
            logger.info(
                "whatsapp inbound race-deduped (plain) prospect_id=%s dedup_id=%s",
                prospect.id,
                dedup_id,
            )
            # Re-raise para que la ruta haga rollback limpio (sesión SQLite dirty).
            raise

    followup_engine.record_prospect_inbound(db, prospect)
    mseq.on_inbound_pause_sequence(db, prospect)

    try:
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
        else:
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
    except Exception:
        # El inbound YA está persistido: no tumbar el request por clasificación/CRM.
        logger.exception(
            "whatsapp inbound post-process failed prospect_id=%s — inbound kept",
            prospect.id,
        )
        try:
            mseq.promote_operational_group_after_prospect_reply(prospect)
        except Exception:
            pass

    mseq._append_log(
        campaign,
        f"Nexus detectó respuesta por WhatsApp · {prospect.name or prospect.id}",
        kind="inbound",
    )
    logger.info(
        "whatsapp inbound processed prospect_id=%s campaign_id=%s wa_id=%s",
        prospect.id,
        campaign.id,
        dedup_id,
    )
    try:
        from app.services.crm import sync as crm_sync

        crm_sync.sync_inbound_reply(
            db,
            prospect=prospect,
            channel="whatsapp",
            message_id=dedup_id,
            message_body=body,
        )
    except Exception:
        logger.exception("crm inbound sync failed prospect_id=%s", prospect.id)
    return True


def register_whatsapp_inbound(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    message: str,
    whatsapp_message_id: str | None = None,
    prepare_reply_draft: bool = True,
) -> dict[str, Any]:
    """Webhook/extensión: registra inbound y genera borrador de réplica en cola."""
    from app.services import whatsapp_assisted_service as was

    # Eco de nuestro propio envío (preview WA Web) → no insertar ni regenerar draft.
    if _is_echo_of_our_whatsapp_outbound(db, prospect=prospect, inbound_plain=message):
        logger.info(
            "whatsapp inbound ignored as outbound echo prospect_id=%s",
            prospect.id,
        )
        return {
            "inserted": False,
            "sequence_paused": bool(prospect.sequence_paused),
            "reply_draft": None,
            "reply_draft_ready": False,
            "echo_ignored": True,
        }

    inserted = process_whatsapp_inbound_for_prospect(
        db,
        prospect=prospect,
        campaign=campaign,
        inbound_plain=message,
        whatsapp_message_id=whatsapp_message_id,
    )
    draft: str | None = None
    calendar_reconnect_required = False
    operator_message = ""
    if inserted and not prepare_reply_draft:
        # Paso registro: sacar borrador Day1 viejo de la cola; la réplica viene después.
        prospect.whatsapp_assisted_draft = None
        prospect.whatsapp_assist_session_id = None
        prospect.whatsapp_last_assisted_at = None
    if inserted and prepare_reply_draft:
        skipped = False
        prep_meta: dict = {}
        try:
            draft, skipped, prep_meta = was.prepare_whatsapp_reply_after_inbound(
                db, prospect, campaign
            )
        except Exception:
            logger.exception(
                "whatsapp reply draft failed prospect_id=%s — inbound kept, using fallback",
                prospect.id,
            )
            draft = None
            skipped = False
            prep_meta = {}
        if prep_meta.get("calendar_reconnect_required"):
            calendar_reconnect_required = True
            operator_message = str(prep_meta.get("operator_message") or "").strip()
        if not skipped and not (draft or "").strip() and not calendar_reconnect_required:
            from app.services.whatsapp_reply_compose import compose_whatsapp_inbound_reply

            try:
                draft = compose_whatsapp_inbound_reply(
                    db,
                    prospect=prospect,
                    campaign=campaign,
                    inbound_text=message,
                )
                was.mark_draft_suggested(db, prospect, campaign, draft, log_event=True)
            except Exception:
                logger.exception(
                    "whatsapp reply fallback failed prospect_id=%s",
                    prospect.id,
                )
                draft = None

    return {
        "inserted": inserted,
        "duplicate": not inserted,
        "sequence_paused": bool(prospect.sequence_paused),
        "reply_draft": draft,
        "reply_draft_ready": bool((draft or "").strip()),
        "calendar_reconnect_required": calendar_reconnect_required,
        "operator_message": operator_message,
    }


def ingest_meta_webhook_messages(
    db: Session,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Procesa el JSON de Meta Cloud API (messages).
    Devuelve contadores: processed, inserted, unmatched, duplicates.
    """
    processed = inserted = unmatched = duplicates = 0
    entries = payload.get("entry") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return {
            "processed": 0,
            "inserted": 0,
            "unmatched": 0,
            "duplicates": 0,
        }

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue
            messages = value.get("messages") or []
            if not isinstance(messages, list):
                continue
            contacts = value.get("contacts") or []
            contact_name = ""
            if contacts and isinstance(contacts[0], dict):
                profile = contacts[0].get("profile") or {}
                if isinstance(profile, dict):
                    contact_name = str(profile.get("name") or "").strip()

            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                processed += 1
                msg_type = str(msg.get("type") or "").strip().lower()
                if msg_type and msg_type not in ("text", "button", "interactive"):
                    # Ignorar audio/imagen/status-only por ahora; contar como processed.
                    continue
                from_raw = str(msg.get("from") or "").strip()
                wamid = str(msg.get("id") or "").strip() or None
                text = ""
                if msg_type == "text":
                    text = str((msg.get("text") or {}).get("body") or "").strip()
                elif msg_type == "button":
                    text = str((msg.get("button") or {}).get("text") or "").strip()
                elif msg_type == "interactive":
                    interactive = msg.get("interactive") or {}
                    button_reply = interactive.get("button_reply") or {}
                    list_reply = interactive.get("list_reply") or {}
                    text = str(
                        button_reply.get("title")
                        or list_reply.get("title")
                        or ""
                    ).strip()
                if not text:
                    continue

                prospect = resolve_prospect_by_whatsapp_digits(
                    db, company_id=None, from_digits=from_raw
                )
                # Preferir match por context.id (reply-to outbound wamid)
                context = msg.get("context") if isinstance(msg.get("context"), dict) else {}
                reply_to = str((context or {}).get("id") or "").strip()
                if reply_to:
                    outbound = db.scalars(
                        select(OutreachMessage)
                        .where(
                            OutreachMessage.whatsapp_message_id == reply_to,
                            OutreachMessage.direction == "outbound",
                        )
                        .limit(1)
                    ).first()
                    if outbound is not None:
                        p2 = db.get(Prospect, outbound.prospect_id)
                        if p2 is not None:
                            prospect = p2

                if prospect is None or not prospect.campaign_id:
                    unmatched += 1
                    logger.info(
                        "whatsapp webhook unmatched from=%s name=%s text=%s",
                        from_raw,
                        contact_name[:40],
                        text[:80],
                    )
                    continue

                campaign = db.get(Campaign, int(prospect.campaign_id))
                if campaign is None:
                    unmatched += 1
                    continue

                result = register_whatsapp_inbound(
                    db,
                    prospect=prospect,
                    campaign=campaign,
                    message=text,
                    whatsapp_message_id=wamid,
                    prepare_reply_draft=True,
                )
                if result.get("inserted"):
                    inserted += 1
                else:
                    duplicates += 1

    return {
        "processed": processed,
        "inserted": inserted,
        "unmatched": unmatched,
        "duplicates": duplicates,
    }
