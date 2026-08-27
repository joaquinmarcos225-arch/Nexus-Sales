"""Sincronizar respuestas reales de Gmail → OutreachMessage inbound + IA + automatizaciones."""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.models.user import User
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services import multichannel_sequence as mseq
from app.services import pipeline_sync
from app.services.ai_instruction_context import campaign_education_blob
from app.services.gmail_automation_flags import gmail_automation_enabled, log_gmail_automation_skipped
from app.services.gmail_drafts import get_valid_gmail_connection
from app.services.sequence_gmail_draft_sent import (
    reconcile_campaign_gmail_draft_sents,
    reconcile_prospect_gmail_draft_sents,
)
from app.services.gmail_threads import (
    fetch_message_full,
    fetch_thread_full,
    gmail_get,
    parse_address_email,
    resolve_thread_id_for_prospect,
)
from app.services.inbound_auto_reply import (
    already_auto_replied,
    auto_reply_is_finished,
    ensure_auto_reply_for_gmail_message,
    get_pending_send_task,
    inbound_auto_reply_enabled,
    inbound_needs_auto_reply_retry,
    log_auto_reply_outcome_to_activity,
    process_due_inbound_auto_reply_tasks,
    try_execute_overdue_scheduled_reply,
)
from app.services.outreach_simulation import make_message

logger = logging.getLogger(__name__)


def _b64url_decode(data: str) -> str:
    if not data:
        return ""
    pad = len(data) % 4
    if pad:
        data += "=" * (4 - pad)
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _header_map(payload: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").strip()
        if not name:
            continue
        out[name.lower()] = (h.get("value") or "").strip()
    return out


def _extract_plain_from_part(part: dict) -> str:
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    data = body.get("data")
    if mime == "text/plain" and data:
        return _b64url_decode(data)
    for sub in part.get("parts") or []:
        got = _extract_plain_from_part(sub)
        if got.strip():
            return got
    return ""


def _extract_html_from_part(part: dict) -> str:
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    data = body.get("data")
    if mime == "text/html" and data:
        return _b64url_decode(data)
    for sub in part.get("parts") or []:
        got = _extract_html_from_part(sub)
        if got.strip():
            return got
    return ""


def _html_to_plain(html: str) -> str:
    if not html:
        return ""
    t = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style.*?>.*?</style>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def plain_body_from_gmail_message(msg: dict) -> str:
    payload = msg.get("payload") or {}
    if (payload.get("body") or {}).get("data"):
        raw = _b64url_decode((payload["body"] or {}).get("data") or "")
        if raw.strip():
            return raw
    for part in payload.get("parts") or []:
        t = _extract_plain_from_part(part)
        if t.strip():
            return t
    for part in payload.get("parts") or []:
        h = _extract_html_from_part(part)
        if h.strip():
            return _html_to_plain(h)
    return ""


def _norm_email(s: str | None) -> str:
    return (s or "").strip().lower()


def _headers_involve_email(headers: dict[str, str], email: str) -> bool:
    em = _norm_email(email)
    if not em:
        return False
    for key in ("from", "to", "cc", "delivered-to", "x-original-to"):
        raw = headers.get(key, "")
        if em in _norm_email(raw) or em in parse_address_email(raw):
            return True
        if em in (raw or "").lower():
            return True
    return False


def _thread_involves_parties(messages: list[dict], *, user_email: str, prospect_email: str) -> bool:
    ue = _norm_email(user_email)
    pe = _norm_email(prospect_email)
    for gm in messages:
        headers = _header_map(gm.get("payload") or {})
        if _headers_involve_email(headers, pe) and _headers_involve_email(headers, ue):
            return True
        from_em = parse_address_email(headers.get("from", ""))
        to_raw = headers.get("to", "")
        if from_em == pe and ue in _norm_email(to_raw):
            return True
        if from_em == ue and pe in _norm_email(to_raw):
            return True
    return False


def _is_gmail_delivery_bounce(*, subject: str, body: str, from_header: str = "") -> bool:
    """Ignora rebotes / DSN de Gmail — no son respuestas del prospecto."""
    from_em = parse_address_email(from_header or "")
    if from_em and ("mailer-daemon" in from_em or "postmaster" in from_em):
        return True
    subj = (subject or "").lower()
    if "delivery status notification" in subj or "mail delivery failed" in subj:
        return True
    low = (body or "").lower()
    if "address not found" in low and "wasn't delivered" in low:
        return True
    if "delivery status notification (failure)" in low:
        return True
    return False


def _is_prospect_reply_message(
    headers: dict[str, str],
    *,
    prospect_email: str,
    user_email: str,
    thread_messages: list[dict] | None = None,
) -> bool:
    """True si el mensaje parece una respuesta del prospecto (email exacto o hilo ya vinculado)."""
    pe = _norm_email(prospect_email)
    ue = _norm_email(user_email)
    from_email = parse_address_email(headers.get("from", ""))
    if not from_email or from_email == ue:
        return False
    if from_email == pe:
        return True
    if pe in (headers.get("from", "") or "").lower():
        return True
    if thread_messages and _thread_involves_parties(thread_messages, user_email=ue, prospect_email=pe):
        return from_email != ue
    return False


def prospect_has_outbound_touch(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "outbound",
        )
    )
    return int(n or 0) > 0


def _conversation_digest_rows(db: Session, prospect_id: int, limit: int = 18) -> str:
    rows = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect_id)
        .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
    ).all()
    lines: list[str] = []
    for m in rows[-limit:]:
        msg = (m.message or "").strip().replace("\n", " ")
        if not msg:
            continue
        lines.append(f"- {m.sender_type}/{m.direction}: {msg[:360]}")
    return "\n".join(lines) if lines else "(vacío)"


def _has_pending_review_inbound(db: Session, prospect_id: int) -> bool:
    from app.models.outreach_task import OutreachTask

    n = db.scalar(
        select(func.count(OutreachTask.id)).where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.task_kind == "review_inbound",
            OutreachTask.status == "pending",
        )
    )
    return int(n or 0) > 0


def _has_pending_hot_lead(db: Session, prospect_id: int) -> bool:
    from app.models.outreach_task import OutreachTask

    n = db.scalar(
        select(func.count(OutreachTask.id)).where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.task_kind == "hot_lead",
            OutreachTask.status == "pending",
        )
    )
    return int(n or 0) > 0


def process_gmail_inbound_for_prospect(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound_plain: str,
    gmail_message_id: str,
    subject: str,
) -> bool:
    """
    Persiste un mensaje inbound real (dedupe por gmail_message_id) y dispara clasificación + reglas.
    Devuelve True si insertó un mensaje nuevo.
    """
    exists = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.gmail_message_id == gmail_message_id,
        )
    )
    if int(exists or 0) > 0:
        return False

    body = (inbound_plain or "").strip()
    if len(body) < 2:
        return False

    display = (
        "[Gmail · respuesta real]\n"
        f"Asunto: {(subject or '').strip() or '—'}\n\n"
        f"{body}"
    )
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="prospect",
        message=display,
        channel="email",
        direction="inbound",
        gmail_message_id=gmail_message_id,
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
            f"Nexus detectó respuesta por email · {prospect.name or prospect.email} (prioridad reunión)",
            kind="inbound",
        )
        logger.info(
            "inbound processed (meeting priority) prospect_id=%s gmail_message_id=%s",
            prospect.id,
            gmail_message_id,
        )
        try:
            from app.services.crm import sync as crm_sync

            crm_sync.sync_inbound_reply(
                db,
                prospect=prospect,
                channel="email",
                message_id=gmail_message_id,
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
        f"Nexus detectó respuesta por email · {prospect.name or prospect.email}",
        kind="inbound",
    )

    logger.info(
        "inbound processed prospect_id=%s campaign_id=%s status=%s gmail_message_id=%s",
        prospect.id,
        campaign.id,
        prospect.status,
        gmail_message_id,
    )
    try:
        from app.services.crm import sync as crm_sync

        crm_sync.sync_inbound_reply(
            db,
            prospect=prospect,
            channel="email",
            message_id=gmail_message_id,
            message_body=body,
        )
    except Exception:
        logger.exception("crm inbound sync failed prospect_id=%s", prospect.id)
    return True


def _handle_inbound_message(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    gmail_message_id: str,
    inbound_plain: str,
    subject: str,
    trace: list[str],
) -> tuple[int, str]:
    """
    Importa inbound si es nuevo y dispara auto-respuesta (idempotente).
    Devuelve (imported 0|1, outcome de auto-reply).
    """
    imported_flag = 0
    reply_outcome = "skipped"
    prior_status = (prospect.status or "").strip()
    mid = (gmail_message_id or "").strip()
    if _is_gmail_delivery_bounce(subject=subject, body=inbound_plain):
        trace.append(f"bounce_skipped prospect={prospect.id} mid={mid}")
        return 0, "skipped"
    try:
        if process_gmail_inbound_for_prospect(
            db,
            prospect=prospect,
            campaign=campaign,
            inbound_plain=inbound_plain,
            gmail_message_id=mid,
            subject=subject,
        ):
            imported_flag = 1
            trace.append(f"imported prospect={prospect.id} mid={mid}")
        else:
            trace.append(f"inbound_exists prospect={prospect.id} mid={mid}")
    except Exception as exc:  # noqa: BLE001
        trace.append(f"import_error prospect={prospect.id}: {exc}")
        logger.exception("inbound import failed prospect_id=%s", prospect.id)
        return 0, "skipped"

    if auto_reply_is_finished(db, prospect.id, mid):
        trace.append(f"auto_reply_done prospect={prospect.id} mid={mid}")
        return imported_flag, "skipped_already"

    overdue_out = try_execute_overdue_scheduled_reply(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_gmail_message_id=mid,
        inbound_plain=inbound_plain,
    )
    if overdue_out is not None:
        trace.append(f"auto_reply_overdue prospect={prospect.id} mid={mid} outcome={overdue_out}")
        return imported_flag, overdue_out

    if already_auto_replied(db, prospect.id, mid):
        trace.append(f"auto_reply_skip_already prospect={prospect.id} mid={mid}")
        return imported_flag, "skipped_already"

    if imported_flag == 0 and not inbound_needs_auto_reply_retry(db, prospect.id, mid):
        trace.append(f"auto_reply_skip_existing prospect={prospect.id} mid={mid}")
        return imported_flag, "skipped_existing_inbound"

    try:
        reply_outcome = ensure_auto_reply_for_gmail_message(
            db,
            campaign=campaign,
            prospect=prospect,
            gmail_message_id=mid,
            inbound_plain=inbound_plain,
            prior_prospect_status=prior_status,
        )
        trace.append(f"auto_reply prospect={prospect.id} mid={mid} outcome={reply_outcome}")
    except Exception as exc:  # noqa: BLE001
        trace.append(f"auto_reply_error prospect={prospect.id}: {exc}")
        logger.exception("inbound auto-reply failed prospect_id=%s", prospect.id)
        reply_outcome = "error"
        log_auto_reply_outcome_to_activity(
            campaign, prospect, mid, f"error:{type(exc).__name__}", detail=str(exc)[:240]
        )

    return imported_flag, reply_outcome


def _sync_prospect_via_gmail_search(
    db: Session,
    client: httpx.Client,
    access: str,
    *,
    prospect: Prospect,
    campaign: Campaign,
    user_email: str,
    trace: list[str],
) -> tuple[int, int, int, str]:
    """
    Busca mensajes `from:prospect` en Gmail (sin depender solo del thread guardado).
    Devuelve (imported, messages_fetched, replies_detected, last_auto_outcome).
    """
    pe = _norm_email(prospect.email)
    if not pe:
        return 0, 0, 0, "skipped"

    contacted_statuses = {
        ProspectStatus.contacted.value,
        ProspectStatus.replied.value,
        ProspectStatus.interested.value,
        ProspectStatus.meeting_booked.value,
    }
    status = (prospect.status or "").strip()
    has_touch = prospect_has_outbound_touch(db, prospect.id) or bool(
        (prospect.gmail_thread_id or "").strip()
    )
    if not has_touch and status not in contacted_statuses:
        trace.append(f"search_skip prospect={prospect.id}: sin outbound previo")
        return 0, 0, 0, "skipped"

    imported = fetched = replies = 0
    last_auto = "skipped"
    seen_mids: set[str] = set()
    queries = [
        f"from:{pe} newer_than:30d",
        f"from:{pe} newer_than:7d in:inbox",
    ]

    for q in queries:
        data = gmail_get(client, access, "/messages", params={"q": q, "maxResults": "15"})
        if data.get("_unauthorized"):
            trace.append(f"search_unauthorized prospect={prospect.id}")
            break
        for ref in data.get("messages") or []:
            mid = ref.get("id")
            if not mid or mid in seen_mids:
                continue
            seen_mids.add(mid)
            fetched += 1
            full = fetch_message_full(client, access, str(mid))
            if not full:
                continue
            headers = _header_map(full.get("payload") or {})
            if not _is_prospect_reply_message(
                headers, prospect_email=pe, user_email=user_email
            ):
                continue
            replies += 1
            tid = (full.get("threadId") or "").strip()
            if tid:
                prospect.gmail_thread_id = tid
                db.flush()
            subj = headers.get("subject", "")
            plain = plain_body_from_gmail_message(full)
            if _is_gmail_delivery_bounce(
                subject=subj,
                body=plain,
                from_header=headers.get("from", ""),
            ):
                trace.append(f"bounce_skipped prospect={prospect.id} mid={mid}")
                continue
            imp, auto_out = _handle_inbound_message(
                db,
                campaign=campaign,
                prospect=prospect,
                gmail_message_id=str(mid),
                inbound_plain=plain,
                subject=subj,
                trace=trace,
            )
            imported += imp
            last_auto = auto_out

    return imported, fetched, replies, last_auto


def _sync_prospect_via_thread(
    db: Session,
    client: httpx.Client,
    access: str,
    *,
    prospect: Prospect,
    campaign: Campaign,
    user_email: str,
    trace: list[str],
) -> tuple[int, int, int, int, str]:
    """
    Lee hilo completo. Devuelve (imported, threads_examined, messages_in_thread, replies_detected, last_auto_outcome).
    """
    pe = _norm_email(prospect.email)
    if not pe:
        return 0, 0, 0, 0, "skipped"

    tid = (prospect.gmail_thread_id or "").strip()
    if not tid:
        tid = resolve_thread_id_for_prospect(
            client, access, user_email=user_email, prospect_email=pe
        )
        if tid:
            prospect.gmail_thread_id = tid
            db.flush()
            trace.append(f"thread_resolved prospect={prospect.id} tid={tid[:12]}…")
    if not tid:
        trace.append(f"thread_missing prospect={prospect.id}")
        return 0, 0, 0, 0, "skipped"

    thread = fetch_thread_full(client, access, tid)
    if thread is None:
        trace.append(f"thread_fetch_failed prospect={prospect.id}")
        return 0, 0, 0, 0, "skipped"

    messages = list(thread.get("messages") or [])
    messages.sort(key=lambda m: int(m.get("internalDate") or 0))
    replies_detected = 0
    imported = 0
    last_auto = "skipped"

    prior_gmail_inbound = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.direction == "inbound",
                OutreachMessage.sender_type == "prospect",
                OutreachMessage.gmail_message_id.isnot(None),
            )
        )
        or 0
    )

    candidates: list[tuple[dict, int]] = []
    for gm in messages:
        mid = gm.get("id")
        if not mid:
            continue
        headers = _header_map(gm.get("payload") or {})
        if not _is_prospect_reply_message(
            headers,
            prospect_email=pe,
            user_email=user_email,
            thread_messages=messages,
        ):
            continue
        replies_detected += 1
        internal = int(gm.get("internalDate") or 0)
        candidates.append((gm, internal))

    if not candidates:
        trace.append(
            f"thread_no_replies prospect={prospect.id} msgs={len(messages)} tid={tid[:12]}…"
        )
        return 0, 1, len(messages), 0, "skipped"

    if prior_gmail_inbound == 0 and len(candidates) > 1:
        candidates = [max(candidates, key=lambda x: x[1])]

    for gm, _internal in sorted(candidates, key=lambda x: x[1]):
        mid = gm.get("id")
        if not mid:
            continue
        headers = _header_map(gm.get("payload") or {})
        subj = headers.get("subject", "")
        plain = plain_body_from_gmail_message(gm)
        if _is_gmail_delivery_bounce(
            subject=subj,
            body=plain,
            from_header=headers.get("from", ""),
        ):
            trace.append(f"bounce_skipped prospect={prospect.id} mid={mid}")
            continue
        imp, auto_out = _handle_inbound_message(
            db,
            campaign=campaign,
            prospect=prospect,
            gmail_message_id=str(mid),
            inbound_plain=plain,
            subject=subj,
            trace=trace,
        )
        imported += imp
        last_auto = auto_out

    return imported, 1, len(messages), replies_detected, last_auto


def sync_campaign_gmail_inbound(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    campaign_id: int,
    allow_manual: bool = False,
    allow_company_gmail_operator: bool = False,
) -> dict[str, Any]:
    """
    Lee hilos Gmail del buzón del vendedor (o del operador Gmail de la empresa) y
    registra respuestas de prospectos de la campaña.
    Estrategia dual: hilo guardado + búsqueda `from:prospect` (más fiable tras enviar borradores).

    `allow_manual=True` permite sincronización explícita desde la UI aunque
    ENABLE_GMAIL_AUTOMATION=0 (el flag sigue controlando ticks del scheduler).

    `allow_company_gmail_operator=True` permite `user_id` distinto del seller cuando
    el outbound usó otra cuenta Gmail de la misma empresa.
    """
    if not allow_manual and not gmail_automation_enabled():
        log_gmail_automation_skipped(f"sync_campaign_gmail_inbound campaign_id={campaign_id}")
        return {"skipped": True, "reason": "gmail_automation_disabled"}

    logger.info(
        "gmail inbound sync started campaign_id=%s company_id=%s user_id=%s",
        campaign_id,
        company_id,
        user_id,
    )

    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.company_id == company_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise ValueError("Campaña no encontrada")
    if int(campaign.seller_id) != int(user_id):
        if not allow_company_gmail_operator:
            raise ValueError("Solo el vendedor asignado puede sincronizar el inbox de su Gmail.")
        try:
            get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                "Solo el vendedor asignado o un operador con Gmail de la empresa "
                "puede sincronizar el inbox."
            ) from exc

    reconciled = mseq.reconcile_meeting_vs_postergado_for_campaign(db, campaign)
    if reconciled:
        logger.info(
            "gmail inbound reconcile meeting>postergado campaign_id=%s fixed=%s",
            campaign_id,
            reconciled,
        )

    access, row = get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)
    user_email = _norm_email(row.external_email)
    if not user_email:
        raise ValueError("No hay email de la cuenta Gmail conectada; reconectá Google.")

    seller = db.get(User, int(user_id))
    if seller is None:
        raise ValueError("Vendedor no encontrado")

    prospects = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign_id,
            Prospect.company_id == company_id,
        )
    ).all()

    imported = 0
    skipped = 0
    errors: list[str] = []
    examined_threads = 0
    messages_fetched = 0
    replies_detected = 0
    prospects_matched = 0
    auto_drafts = 0
    auto_sent = 0
    gmail_draft_sents = 0
    trace: list[str] = []

    def _tally_auto(outcome: str) -> bool:
        nonlocal auto_drafts, auto_sent
        if outcome == "draft":
            auto_drafts += 1
            return True
        if outcome == "sent":
            auto_sent += 1
            return True
        return False

    with httpx.Client(timeout=60.0) as client:
        for prospect in prospects:
            pe = _norm_email(prospect.email)
            if not pe:
                continue

            t_imp, t_threads, t_msgs, t_replies, t_auto = _sync_prospect_via_thread(
                db,
                client,
                access,
                prospect=prospect,
                campaign=campaign,
                user_email=user_email,
                trace=trace,
            )
            imported += t_imp
            examined_threads += t_threads
            messages_fetched += t_msgs
            replies_detected += t_replies
            had_auto = _tally_auto(t_auto)

            prospect_touched = t_imp > 0 or had_auto

            if not prospect_touched and t_replies == 0:
                alt_tid = resolve_thread_id_for_prospect(
                    client, access, user_email=user_email, prospect_email=pe
                )
                stored = (prospect.gmail_thread_id or "").strip()
                if alt_tid and alt_tid != stored:
                    prospect.gmail_thread_id = alt_tid
                    db.flush()
                    trace.append(f"thread_retry prospect={prospect.id} new_tid={alt_tid[:12]}…")
                    t_imp2, t_threads2, t_msgs2, t_replies2, t_auto2 = _sync_prospect_via_thread(
                        db,
                        client,
                        access,
                        prospect=prospect,
                        campaign=campaign,
                        user_email=user_email,
                        trace=trace,
                    )
                    imported += t_imp2
                    examined_threads += t_threads2
                    messages_fetched += t_msgs2
                    replies_detected += t_replies2
                    had_auto2 = _tally_auto(t_auto2)
                    prospect_touched = prospect_touched or t_imp2 > 0 or had_auto2

            if not prospect_touched:
                s_imp, s_fetched, s_replies, s_auto = _sync_prospect_via_gmail_search(
                    db,
                    client,
                    access,
                    prospect=prospect,
                    campaign=campaign,
                    user_email=user_email,
                    trace=trace,
                )
                imported += s_imp
                messages_fetched += s_fetched
                replies_detected += s_replies
                had_auto_s = _tally_auto(s_auto)
                prospect_touched = prospect_touched or s_imp > 0 or had_auto_s
                if not prospect_touched and t_replies == 0 and s_replies == 0:
                    if not (prospect.gmail_thread_id or "").strip():
                        skipped += 1

            if prospect_touched or t_replies > 0:
                prospects_matched += 1

        draft_stats = reconcile_campaign_gmail_draft_sents(
            db,
            user=seller,
            campaign=campaign,
            prospects=prospects,
            user_email=user_email,
            client=client,
            access=access,
        )
        gmail_draft_sents = int(draft_stats.get("gmail_draft_sents_detected") or 0)
        if gmail_draft_sents:
            trace.append(f"gmail_draft_sents={gmail_draft_sents}")
            db.commit()

    worker_meta: dict[str, Any] = {}
    if inbound_auto_reply_enabled():
        worker_meta = process_due_inbound_auto_reply_tasks(db)
        if int(worker_meta.get("sent") or 0) > 0:
            auto_sent += int(worker_meta["sent"])
        if int(worker_meta.get("drafted") or 0) > 0:
            auto_drafts += int(worker_meta["drafted"])
        trace.append(
            f"worker_due sent={worker_meta.get('sent')} draft={worker_meta.get('drafted')} "
            f"due={worker_meta.get('due_tasks')} pending_all={worker_meta.get('pending_all')}"
        )

    result = {
        "imported": imported,
        "skipped_no_thread": skipped,
        "threads_examined": examined_threads,
        "messages_fetched": messages_fetched,
        "replies_detected": replies_detected,
        "prospects_matched": prospects_matched,
        "auto_drafts": auto_drafts,
        "auto_sent": auto_sent,
        "gmail_draft_sents_detected": gmail_draft_sents,
        "prospects_scanned": len([p for p in prospects if _norm_email(p.email)]),
        "errors": errors[:12],
        "trace": trace[:40],
        "inbound_auto_reply_worker": worker_meta,
    }
    logger.info(
        "gmail inbound sync finished campaign_id=%s imported=%s replies_detected=%s "
        "threads=%s prospects_matched=%s auto_drafts=%s auto_sent=%s skipped=%s",
        campaign_id,
        imported,
        replies_detected,
        examined_threads,
        prospects_matched,
        auto_drafts,
        auto_sent,
        skipped,
    )
    return result


def extract_prospect_inbound_plain(stored: str | None) -> str:
    """Texto del prospecto sin envoltorio Nexus/Gmail ni citas del hilo."""
    from app.services.meeting_slot_parser import strip_email_reply_quotes

    t = (stored or "").strip()
    if not t:
        return ""
    low = t.lower()
    if low.startswith("[gmail · respuesta real]") or low.startswith("[gmail ·"):
        parts = t.split("\n\n", 1)
        if len(parts) >= 2:
            t = parts[1].strip()
    return ci.normalize_inbound_text_for_classification(strip_email_reply_quotes(t))


def sync_prospect_gmail_inbound(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    campaign_id: int,
    prospect_id: int,
    allow_manual: bool = True,
    allow_company_gmail_operator: bool = False,
) -> dict[str, Any]:
    """Sincroniza Gmail solo para un prospecto (antes de enviar/responder manual)."""
    if not allow_manual and not gmail_automation_enabled():
        return {"skipped": True, "reason": "gmail_automation_disabled"}

    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign_id, Campaign.company_id == company_id)
        .options(selectinload(Campaign.product))
    ).first()
    if campaign is None:
        raise ValueError("Campaña no encontrada")
    if int(campaign.seller_id) != int(user_id):
        if not allow_company_gmail_operator:
            raise ValueError("Solo el vendedor asignado puede sincronizar su Gmail.")
        try:
            get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                "Solo el vendedor asignado o un operador con Gmail de la empresa "
                "puede sincronizar el inbox."
            ) from exc

    prospect = db.get(Prospect, prospect_id)
    if prospect is None or int(prospect.company_id) != int(company_id):
        raise ValueError("Prospecto no encontrado")
    if int(prospect.campaign_id) != int(campaign_id):
        raise ValueError("El prospecto no pertenece a esta campaña")

    access, row = get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)
    user_email = _norm_email(row.external_email)
    if not user_email:
        raise ValueError("No hay email de la cuenta Gmail conectada.")

    seller = db.get(User, int(user_id))
    if seller is None:
        raise ValueError("Vendedor no encontrado")

    trace: list[str] = []
    imported = examined = msgs = replies = 0
    auto_out = "skipped"
    gmail_draft_sents = 0

    with httpx.Client(timeout=60.0) as client:
        t_imp, t_threads, t_msgs, t_replies, t_auto = _sync_prospect_via_thread(
            db,
            client,
            access,
            prospect=prospect,
            campaign=campaign,
            user_email=user_email,
            trace=trace,
        )
        imported += t_imp
        examined += t_threads
        msgs += t_msgs
        replies += t_replies
        auto_out = t_auto

        if t_imp == 0 and t_replies == 0:
            alt_tid = resolve_thread_id_for_prospect(
                client, access, user_email=user_email, prospect_email=_norm_email(prospect.email)
            )
            stored = (prospect.gmail_thread_id or "").strip()
            if alt_tid and alt_tid != stored:
                prospect.gmail_thread_id = alt_tid
                db.flush()
                t_imp2, t_threads2, t_msgs2, t_replies2, t_auto2 = _sync_prospect_via_thread(
                    db,
                    client,
                    access,
                    prospect=prospect,
                    campaign=campaign,
                    user_email=user_email,
                    trace=trace,
                )
                imported += t_imp2
                examined += t_threads2
                msgs += t_msgs2
                replies += t_replies2
                auto_out = t_auto2

        if imported == 0 and replies == 0:
            s_imp, s_fetched, s_replies, s_auto = _sync_prospect_via_gmail_search(
                db,
                client,
                access,
                prospect=prospect,
                campaign=campaign,
                user_email=user_email,
                trace=trace,
            )
            imported += s_imp
            msgs += s_fetched
            replies += s_replies
            auto_out = s_auto

        marked_days = reconcile_prospect_gmail_draft_sents(
            db,
            user=seller,
            campaign=campaign,
            prospect=prospect,
            user_email=user_email,
            client=client,
            access=access,
        )
        if marked_days:
            gmail_draft_sents = len(marked_days)
            trace.append(f"gmail_draft_sents={marked_days}")
            db.commit()

    db.flush()
    return {
        "prospect_id": prospect_id,
        "imported": imported,
        "threads_examined": examined,
        "messages_fetched": msgs,
        "replies_detected": replies,
        "auto_outcome": auto_out,
        "gmail_draft_sents_detected": gmail_draft_sents,
        "trace": trace[:20],
    }


def latest_prospect_inbound_plain_from_gmail(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    prospect: Prospect,
    campaign: Campaign,
) -> str | None:
    """
    Lee el hilo Gmail y devuelve el cuerpo del último mensaje del prospecto.
    Importa a BD si aún no estaba (idempotente).
    """
    pe = _norm_email(prospect.email)
    if not pe:
        return None

    access, row = get_valid_gmail_connection(db, company_id=company_id, user_id=user_id)
    user_email = _norm_email(row.external_email)
    if not user_email:
        return None

    with httpx.Client(timeout=60.0) as client:
        tid = (prospect.gmail_thread_id or "").strip()
        if not tid:
            tid = resolve_thread_id_for_prospect(
                client, access, user_email=user_email, prospect_email=pe
            )
            if tid:
                prospect.gmail_thread_id = tid
                db.flush()

        if not tid:
            return None

        thread = fetch_thread_full(client, access, tid)
        if thread is None:
            return None

        messages = list(thread.get("messages") or [])
        if not messages:
            return None

        best_plain: str | None = None
        best_internal = -1
        best_mid: str | None = None
        best_subj = ""

        for gm in messages:
            mid = gm.get("id")
            if not mid:
                continue
            headers = _header_map(gm.get("payload") or {})
            if not _is_prospect_reply_message(
                headers,
                prospect_email=pe,
                user_email=user_email,
                thread_messages=messages,
            ):
                continue
            internal = int(gm.get("internalDate") or 0)
            plain = plain_body_from_gmail_message(gm).strip()
            if not plain:
                continue
            if internal <= best_internal:
                continue
            best_internal = internal
            best_plain = plain
            best_mid = str(mid)
            best_subj = headers.get("subject", "")

        if not best_plain or not best_mid:
            return None

        _handle_inbound_message(
            db,
            campaign=campaign,
            prospect=prospect,
            gmail_message_id=best_mid,
            inbound_plain=best_plain,
            subject=best_subj,
            trace=[],
        )
        db.flush()
        from app.services.meeting_slot_parser import strip_email_reply_quotes

        return strip_email_reply_quotes(best_plain) or best_plain
