"""Respuesta automática a inbound Gmail: borrador inmediato o envío programado según campaña."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import InboundReplyMode, ProspectStatus
from app.models.inbound_auto_reply_receipt import InboundAutoReplyReceipt
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services import conversation_intelligence as ci
from app.services import followup_engine
from app.services import multichannel_sequence as mseq
from app.models.company import Company
from app.services.ai_behavior_policy import (
    load_behavior_policy,
    resolve_booking_priority_from_signals,
    should_inject_calendar_link,
)
from app.services.ai_decision_log import record_ai_decision, signals_payload
from app.services.ai_instruction_context import campaign_education_blob
from app.services.gmail_drafts import create_draft_for_user, get_valid_gmail_connection
from app.services.gmail_send import send_email_for_user
from app.services.openai_service import (
    generate_gmail_draft_email,
    inbound_text_needs_substantive_answer,
)
from app.services.email_deliverability import deliverable_email_skip_reason
from app.services.outreach_simulation import make_message
from app.services.real_followup_gmail import (
    _gmail_style_campaign_ctx,
    count_campaign_real_email_outbounds_last_hour,
)

logger = logging.getLogger(__name__)

AUTO_REPLY_MARKER_PREFIX = "[auto-reply:gmail:"
TASK_KIND_INBOUND_AUTO_REPLY = "inbound_auto_reply"

_TERMINAL_AUTO_REPLY_OUTCOMES = frozenset(
    {
        "sent",
        "draft",
        "skipped_closed",
        "skipped_already",
        "skipped_existing_inbound",
        "skipped_no_inbound",
        # skipped_disabled NO es terminal: si se reactiva NEXUS_INBOUND_AUTO_REPLY, reintenta.
    }
)

# No ensuciar Actividad de Nexus con estados esperados / repetitivos.
_SILENT_ACTIVITY_OUTCOMES = frozenset(
    {
        "skipped_disabled",
        "skipped_existing_inbound",
        "skipped_already",
        "skipped_no_inbound",
    }
)

_SKIP_PRIOR_PROSPECT_STATUSES = frozenset(
    {
        ProspectStatus.failed.value,
        ProspectStatus.not_interested.value,
    }
)


def inbound_auto_reply_enabled() -> bool:
    v = (os.getenv("NEXUS_INBOUND_AUTO_REPLY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _task_is_due(due_at: datetime, now: datetime | None = None) -> bool:
    ref = _as_utc(now or _utc_now())
    return _as_utc(due_at) <= ref


def _auto_send_env_snapshot() -> dict[str, Any]:
    return {
        "NEXUS_INBOUND_AUTO_REPLY": (os.getenv("NEXUS_INBOUND_AUTO_REPLY") or "1").strip(),
        "NEXUS_AUTO_SEND_ENABLED": (os.getenv("NEXUS_AUTO_SEND_ENABLED") or "").strip(),
        "auto_send_enabled": _truthy_env("NEXUS_AUTO_SEND_ENABLED"),
        "hourly_cap": int(os.getenv("NEXUS_AUTO_SEND_HOURLY_CAP", "8")),
    }


def auto_reply_marker(inbound_gmail_message_id: str) -> str:
    return f"{AUTO_REPLY_MARKER_PREFIX}{inbound_gmail_message_id}]"


def _norm_gmail_mid(gmail_message_id: str | None) -> str:
    return (gmail_message_id or "").strip()


def get_auto_reply_receipt(
    db: Session, prospect_id: int, inbound_gmail_message_id: str
) -> InboundAutoReplyReceipt | None:
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid:
        return None
    return db.scalars(
        select(InboundAutoReplyReceipt).where(
            InboundAutoReplyReceipt.prospect_id == prospect_id,
            InboundAutoReplyReceipt.inbound_gmail_message_id == mid,
        )
    ).first()


def record_auto_reply_receipt(
    db: Session,
    *,
    company_id: int,
    campaign_id: int,
    prospect_id: int,
    inbound_gmail_message_id: str,
    outcome: str,
) -> None:
    """Idempotencia estricta por gmail_message_id del inbound (no por hilo)."""
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid:
        return
    row = get_auto_reply_receipt(db, prospect_id, mid)
    if row is None:
        db.add(
            InboundAutoReplyReceipt(
                company_id=company_id,
                campaign_id=campaign_id,
                prospect_id=prospect_id,
                inbound_gmail_message_id=mid,
                outcome=outcome,
            )
        )
        db.flush()
        return
    prev = (row.outcome or "").strip()
    rank = {"scheduled": 1, "failed": 2, "draft": 3, "sent": 4}
    if rank.get(outcome, 0) >= rank.get(prev, 0):
        row.outcome = outcome


def auto_reply_is_finished(db: Session, prospect_id: int, inbound_gmail_message_id: str) -> bool:
    """True si el inbound ya tiene respuesta automática terminada o skip terminal."""
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid:
        return False
    row = get_auto_reply_receipt(db, prospect_id, mid)
    if row is None:
        return _legacy_marker_auto_replied(db, prospect_id, mid)
    return (row.outcome or "").strip() in _TERMINAL_AUTO_REPLY_OUTCOMES


def already_auto_replied(
    db: Session,
    prospect_id: int,
    inbound_gmail_message_id: str,
    *,
    executing_scheduled_task: bool = False,
) -> bool:
    """
    True si este inbound ya tiene respuesta automática terminada (sent/draft)
    o está en cola (scheduled + tarea pending) — salvo cuando el worker ejecuta esa tarea.
    """
    if executing_scheduled_task:
        return auto_reply_is_finished(db, prospect_id, inbound_gmail_message_id)
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid:
        return False
    row = get_auto_reply_receipt(db, prospect_id, mid)
    if row is None:
        return _legacy_marker_auto_replied(db, prospect_id, mid)
    outcome = (row.outcome or "").strip()
    if outcome in ("sent", "draft"):
        return True
    if outcome == "scheduled":
        return _has_pending_send_task(db, prospect_id, mid)
    return False


def _legacy_marker_auto_replied(db: Session, prospect_id: int, mid: str) -> bool:
    """Compatibilidad con registros previos al receipt (match exacto al inicio del mensaje)."""
    marker = auto_reply_marker(mid)
    rows = db.scalars(
        select(OutreachMessage).where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "outbound",
        )
    ).all()
    prospect = db.get(Prospect, prospect_id)
    for row in rows:
        text = (row.message or "").strip()
        if not text.startswith(marker):
            continue
        if prospect is not None:
            record_auto_reply_receipt(
                db,
                company_id=int(prospect.company_id),
                campaign_id=int(row.campaign_id),
                prospect_id=prospect_id,
                inbound_gmail_message_id=mid,
                outcome="sent" if "[Gmail · respuesta automática" in text else "draft",
            )
        return True
    return False


def inbound_needs_auto_reply_retry(db: Session, prospect_id: int, inbound_gmail_message_id: str) -> bool:
    """Inbound ya importado pero sin envío/borrador terminado — reintentar."""
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid:
        return False
    if auto_reply_is_finished(db, prospect_id, mid):
        return False
    rec = get_auto_reply_receipt(db, prospect_id, mid)
    if rec is not None:
        outcome = (rec.outcome or "").strip()
        if outcome == "scheduled":
            task = get_pending_send_task(db, prospect_id, mid)
            if task is None:
                return True
            if _task_is_due(task.due_at, _utc_now()):
                return True
            return False
        if outcome == "failed":
            return True
        if outcome == "skipped_disabled":
            return True
        return False
    return _get_inbound_row(db, prospect_id, mid) is not None


def try_execute_overdue_scheduled_reply(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_gmail_message_id: str,
    inbound_plain: str | None = None,
) -> str | None:
    """Si hay tarea programada vencida, ejecutar envío ahora (no esperar solo al worker)."""
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid or auto_reply_is_finished(db, prospect.id, mid):
        return None
    task = get_pending_send_task(db, prospect.id, mid)
    if task is None or not _task_is_due(task.due_at, _utc_now()):
        return None
    logger.info(
        "[inbound_auto_reply] overdue_scheduled_execute task_id=%s prospect_id=%s inbound=%s",
        task.id,
        prospect.id,
        mid,
    )
    outcome = deliver_auto_reply_for_inbound(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_gmail_message_id=mid,
        inbound_plain=inbound_plain,
        force_immediate=True,
    )
    if outcome in ("sent", "draft"):
        task.status = "completed"
    elif outcome.startswith("skipped") or outcome.startswith("error"):
        task.status = "cancelled"
    else:
        task.status = "completed"
    log_auto_reply_outcome_to_activity(
        campaign,
        prospect,
        mid,
        outcome,
        detail="ejecutado al detectar tarea vencida",
        task_id=int(task.id),
    )
    return outcome


def _get_inbound_row(
    db: Session, prospect_id: int, inbound_gmail_message_id: str
) -> OutreachMessage | None:
    return db.scalars(
        select(OutreachMessage).where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.gmail_message_id == inbound_gmail_message_id,
            OutreachMessage.direction == "inbound",
        )
    ).first()


def _inbound_reply_mode(campaign: Campaign) -> str:
    return (getattr(campaign, "inbound_reply_mode", None) or InboundReplyMode.draft_only.value).strip()


def _inbound_reply_delay_minutes(campaign: Campaign) -> int:
    """Espera humana tras detectar inbound (default 2 min)."""
    raw = int(getattr(campaign, "inbound_reply_delay_minutes", None) or 2)
    if raw < 1:
        return 2
    return min(raw, 15)


def _extract_plain_body(inbound_plain: str | None, inbound_row: OutreachMessage | None) -> str:
    body = (inbound_plain or (inbound_row.message if inbound_row else "") or "").strip()
    if "[Gmail · respuesta real]" in body:
        parts = body.split("\n\n", 1)
        body = parts[1].strip() if len(parts) > 1 else body
    return body


def should_force_draft_only(sig: ci.InboundSignals, body: str) -> bool:
    """Alta ambigüedad / bajo riesgo: no enviar automático; dejar borrador."""
    if sig.explicit_meeting_commitment or sig.prospect_wants_meeting:
        return False
    if sig.interest_level == "high":
        return False
    if sig.objection_type == "not_interested":
        return False
    if sig.asks_concrete_questions or inbound_text_needs_substantive_answer(body):
        return False
    norm = (body or "").strip()
    if len(norm) < 10:
        return True
    if sig.interest_level == "low" and not sig.objection_type:
        return True
    if sig.is_brushoff and sig.interest_level != "high":
        return True
    return False


def _task_notes(gmail_message_id: str) -> str:
    return json.dumps({"inbound_gmail_message_id": gmail_message_id, "v": 1})


def _parse_task_notes(notes: str | None) -> dict[str, Any]:
    if not notes:
        return {}
    try:
        data = json.loads(notes)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_pending_send_task(
    db: Session, prospect_id: int, inbound_gmail_message_id: str
) -> OutreachTask | None:
    mid = _norm_gmail_mid(inbound_gmail_message_id)
    if not mid:
        return None
    rows = db.scalars(
        select(OutreachTask).where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.task_kind == TASK_KIND_INBOUND_AUTO_REPLY,
            OutreachTask.status == "pending",
        )
    ).all()
    for row in rows:
        meta = _parse_task_notes(row.notes)
        if meta.get("inbound_gmail_message_id") == mid:
            return row
    return None


def _has_pending_send_task(db: Session, prospect_id: int, inbound_gmail_message_id: str) -> bool:
    return get_pending_send_task(db, prospect_id, inbound_gmail_message_id) is not None


def log_auto_reply_outcome_to_activity(
    campaign: Campaign,
    prospect: Prospect,
    inbound_gmail_message_id: str,
    outcome: str,
    *,
    detail: str = "",
    task_id: int | None = None,
) -> None:
    """Siempre visible en Actividad de Nexus — nunca dejar el flujo en silencio."""
    name = prospect.name or prospect.email or f"#{prospect.id}"
    mid_short = _norm_gmail_mid(inbound_gmail_message_id)[:14]
    tid = f"task #{task_id}" if task_id else ""
    extra = f" · {detail}" if detail else ""
    o = (outcome or "").strip()

    if o == "scheduled":
        msg = f"Envío automático programado{(' (' + tid + ')') if tid else ''}{extra} · {name}"
        kind = "outbound"
    elif o == "scheduled_duplicate":
        msg = f"Envío automático ya estaba programado ({tid or 'task pendiente'}) · {name}"
        kind = "info"
    elif o == "sent":
        msg = f"Respuesta automática enviada por Gmail · {name}"
        kind = "outbound"
    elif o == "draft":
        msg = f"Borrador automático creado en Gmail · {name}"
        kind = "outbound"
    elif o == "skipped_already":
        msg = f"Auto-respuesta omitida (ya procesada){extra} · {name} · {mid_short}"
        kind = "info"
    elif o == "skipped_existing_inbound":
        msg = f"Auto-respuesta omitida (inbound ya registrado){extra} · {name}"
        kind = "info"
    elif o == "skipped_closed":
        msg = f"Auto-respuesta omitida (prospecto cerrado){extra} · {name}"
        kind = "info"
    elif o == "skipped_disabled":
        msg = f"Auto-respuesta deshabilitada (NEXUS_INBOUND_AUTO_REPLY){extra} · {name}"
        kind = "info"
    elif o == "skipped_no_inbound":
        msg = f"Auto-respuesta omitida (sin mensaje inbound en DB){extra} · {name}"
        kind = "info"
    elif o.startswith("error"):
        msg = f"Error en auto-respuesta: {o}{extra} · {name}"
        kind = "info"
    elif o.startswith("skipped"):
        msg = f"Auto-respuesta omitida: {o}{extra} · {name}"
        kind = "info"
    else:
        msg = f"Auto-respuesta: {o}{extra} · {name} · {mid_short}"
        kind = "info"

    mseq._append_log(campaign, msg, kind=kind)


def schedule_inbound_auto_send(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_gmail_message_id: str,
) -> int | None:
    """Programa envío automático. Devuelve task_id o None si ya existía."""
    if _has_pending_send_task(db, prospect.id, inbound_gmail_message_id):
        logger.info(
            "[inbound_auto_reply] schedule skipped: pending task already exists "
            "prospect_id=%s inbound=%s",
            prospect.id,
            inbound_gmail_message_id,
        )
        return None
    delay = _inbound_reply_delay_minutes(campaign)
    due = _utc_now() + timedelta(minutes=delay)
    task = OutreachTask(
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        task_kind=TASK_KIND_INBOUND_AUTO_REPLY,
        title=f"Enviar respuesta automática ({delay} min)",
        notes=_task_notes(inbound_gmail_message_id),
        due_at=due,
        status="pending",
    )
    db.add(task)
    db.flush()
    record_auto_reply_receipt(
        db,
        company_id=int(campaign.company_id),
        campaign_id=int(campaign.id),
        prospect_id=int(prospect.id),
        inbound_gmail_message_id=inbound_gmail_message_id,
        outcome="scheduled",
    )
    logger.info(
        "[inbound_auto_reply] scheduled task_id=%s prospect_id=%s campaign_id=%s "
        "mode=%s delay_min=%s due_at=%s inbound=%s",
        task.id,
        prospect.id,
        campaign.id,
        _inbound_reply_mode(campaign),
        delay,
        due.isoformat(),
        inbound_gmail_message_id,
    )
    mseq._append_log(
        campaign,
        f"Nexus programó envío automático en {delay} min (task #{task.id}, vence {due.strftime('%H:%M')} UTC) · "
        f"{prospect.name or prospect.email}",
        kind="outbound",
    )
    return int(task.id)


def _append_worker_activity(
    campaign: Campaign,
    prospect: Prospect,
    *,
    task_id: int | None,
    outcome: str,
    inbound_gmail_message_id: str,
    detail: str = "",
) -> None:
    """Resultado visible en Actividad de Nexus tras ejecutar envío programado."""
    name = prospect.name or prospect.email or f"#{prospect.id}"
    tid = f"task #{task_id}" if task_id else "task —"
    extra = f" · {detail}" if detail else ""
    if outcome == "sent":
        msg = f"Nexus envió respuesta automática programada ({tid}) · {name}"
        kind = "outbound"
    elif outcome == "draft":
        msg = f"Nexus creó borrador tras envío programado ({tid}) · {name}"
        kind = "outbound"
    elif outcome.startswith("error"):
        msg = f"Error en envío automático programado ({tid}): {outcome}{extra} · {name}"
        kind = "info"
    else:
        msg = f"Envío automático programado ({tid}): {outcome}{extra} · {name} · inbound {inbound_gmail_message_id[:12]}…"
        kind = "info"
    mseq._append_log(campaign, msg, kind=kind)


def _classify_inbound(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    body: str,
    history_payload: list[dict[str, str]],
) -> ci.InboundSignals:
    education = campaign_education_blob(db, campaign)
    digest_lines: list[str] = []
    for item in history_payload[-18:]:
        msg = (item.get("message") or "").strip().replace("\n", " ")
        if msg:
            digest_lines.append(
                f"- {item.get('sender_type', '?')}/{item.get('direction', '?')}: {msg[:360]}"
            )
    digest = "\n".join(digest_lines) if digest_lines else "(vacío)"
    return ci.classify_inbound_full(
        inbound_text=body,
        prior_interest=getattr(prospect, "interest_level", None),
        conversation_digest=digest,
        education=education,
    )


def _generate_reply_content(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    body: str,
    sig: ci.InboundSignals,
    history_payload: list[dict[str, str]],
) -> tuple[str, str, bool, bool]:
    education = campaign_education_blob(db, campaign)
    policy = load_behavior_policy(db, campaign.company_id)
    norm_in = ci.normalize_inbound_text_for_classification(body)
    response_class, _ = ci.classify_commercial_response(body, sig)
    reply_objective = ci.resolve_reply_objective(
        text=body,
        sig=sig,
        response_class=response_class,
    )
    booking_priority = resolve_booking_priority_from_signals(
        policy,
        inbound_text=norm_in or body,
        explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
        prospect_wants_meeting=bool(sig.prospect_wants_meeting),
        interest_level=sig.interest_level,
    )
    if reply_objective == "rechazo" or response_class in ("no_interesado", "contactar_mas_adelante"):
        booking_priority = False
    timing_soft = (
        sig.objection_type != "not_interested"
        and ci.timing_deferral_should_apply(sig, inbound_text=body)
        and not booking_priority
    )

    campaign_ctx = _gmail_style_campaign_ctx(campaign)
    product_ctx = followup_engine._product_dict(campaign)
    to_addr = (prospect.email or "").strip()
    prospect_ctx = {
        "name": prospect.name,
        "company_name": prospect.company_name,
        "role": prospect.role or "",
        "industry": prospect.industry or "",
        "country": prospect.country or "",
        "email": to_addr,
    }

    substantive = bool(
        sig.asks_concrete_questions
        or sig.objection_type in ("send_info", "other")
        or inbound_text_needs_substantive_answer(norm_in or body)
    )

    subject, reply_body = generate_gmail_draft_email(
        prospect=prospect_ctx,
        campaign=campaign_ctx,
        product=product_ctx,
        tone=campaign.tone,
        education=education,
        conversation_history=history_payload,
        last_prospect_inbound=norm_in or body,
        prospect_timing_soft=timing_soft,
        prospect_booking_priority=booking_priority,
        prospect_substantive_questions=substantive,
        ai_policy=policy,
        interest_level=sig.interest_level,
        prospect_wants_meeting=bool(sig.prospect_wants_meeting),
        explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
        reply_objective=reply_objective,
        response_class=response_class,
    )
    return subject, reply_body, timing_soft, booking_priority


def _apply_post_reply_pipeline(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    sig: ci.InboundSignals,
    body: str,
    timing_soft: bool,
    booking_priority: bool,
) -> None:
    if mseq.prospect_in_meeting_priority(db, prospect):
        mseq.enforce_meeting_priority_over_sequence(db, prospect, campaign)
    elif sig.objection_type == "not_interested":
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

    from app.services import pipeline_sync

    pipeline_sync.sync_pipeline_from_status(prospect)


def _execute_delivery(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_gmail_message_id: str,
    subject: str,
    reply_body: str,
    delivery: Literal["draft", "send"],
    timing_soft: bool,
) -> str:
    to_addr = (prospect.email or "").strip()
    cid = int(campaign.company_id)
    thread_id = (prospect.gmail_thread_id or "").strip() or None
    marker = auto_reply_marker(inbound_gmail_message_id)
    from app.services.outbound_text_normalize import normalize_outbound_email_body

    reply_body = normalize_outbound_email_body(reply_body)
    subject = (subject or "").strip() or "Re:"

    # Misma cuenta Gmail que outbound/inbound sync (seller o fallback de empresa).
    from app.models.user import User
    from app.services.manual_sequence_kickoff import try_find_gmail_operator

    preferred = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
    operator = try_find_gmail_operator(db, company_id=cid, preferred=preferred)
    if operator is None:
        logger.warning(
            "[inbound_auto_reply] gmail skipped: no company gmail prospect_id=%s seller=%s",
            prospect.id,
            campaign.seller_id,
        )
        return "skipped"
    uid = int(operator.id)

    if delivery == "send":
        skip = deliverable_email_skip_reason(to_addr)
        if skip:
            logger.warning(
                "[inbound_auto_reply] gmail send skipped: undeliverable email prospect_id=%s reason=%s",
                prospect.id,
                skip,
            )
            return "skipped"
        logger.info(
            "[inbound_auto_reply] sending email prospect_id=%s campaign_id=%s to=%s thread=%s",
            prospect.id,
            campaign.id,
            to_addr,
            thread_id or "—",
        )
        try:
            _, row = get_valid_gmail_connection(db, company_id=cid, user_id=uid)
            from_addr = (row.external_email or "").strip()
            if not from_addr:
                logger.warning(
                    "[inbound_auto_reply] gmail send skipped: no from_addr prospect_id=%s",
                    prospect.id,
                )
                return "skipped"
            out = send_email_for_user(
                db,
                company_id=cid,
                user_id=uid,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subject,
                body=reply_body,
                thread_id=thread_id,
            )
            gid = (out.get("gmail_message_id") or "").strip() or None
            if not gid:
                raise RuntimeError("Gmail send returned empty message id")
            tid = (out.get("thread_id") or "").strip() or None
            if tid:
                prospect.gmail_thread_id = tid
            logger.info(
                "[inbound_auto_reply] gmail send success prospect_id=%s gmail_message_id=%s",
                prospect.id,
                gid,
            )
        except Exception as exc:
            logger.exception(
                "[inbound_auto_reply] gmail send fail prospect_id=%s: %s — fallback to draft",
                prospect.id,
                exc,
            )
            mseq._append_log(
                campaign,
                f"Envío automático falló; Nexus crea borrador · {prospect.name or prospect.email}",
                kind="info",
            )
            return _execute_delivery(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_gmail_message_id=inbound_gmail_message_id,
                subject=subject,
                reply_body=reply_body,
                delivery="draft",
                timing_soft=timing_soft,
            )
        hist_text = f"{marker}\n[Gmail · respuesta automática Nexus]\nAsunto: {subject}\n\n{reply_body}"
        db.add(
            make_message(
                prospect_id=prospect.id,
                campaign_id=campaign.id,
                sender_type="ai",
                message=hist_text,
                channel="email",
                direction="outbound",
                gmail_message_id=gid,
            )
        )
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=reply_body,
        )
        if prospect.status not in (
            ProspectStatus.meeting_booked.value,
            ProspectStatus.not_interested.value,
        ):
            prospect.status = ProspectStatus.replied.value
        mseq._append_log(
            campaign,
            f"Nexus envió respuesta automática por Gmail · {prospect.name or prospect.email}",
            kind="outbound",
        )
        record_auto_reply_receipt(
            db,
            company_id=cid,
            campaign_id=int(campaign.id),
            prospect_id=int(prospect.id),
            inbound_gmail_message_id=inbound_gmail_message_id,
            outcome="sent",
        )
        _maybe_schedule_followup(db, campaign, prospect, timing_soft=timing_soft)
        return "sent"

    logger.info(
        "[inbound_auto_reply] creating gmail draft prospect_id=%s campaign_id=%s to=%s",
        prospect.id,
        campaign.id,
        to_addr,
    )
    out = create_draft_for_user(
        db,
        company_id=cid,
        user_id=uid,
        to_addr=to_addr,
        subject=subject,
        body=reply_body,
    )
    tid = (out.get("thread_id") or "").strip()
    if tid:
        prospect.gmail_thread_id = tid
    hist_text = (
        f"{marker}\n[Borrador Gmail · respuesta automática a inbound]\nAsunto: {subject}\n\n{reply_body}"
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
    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=reply_body,
    )
    if prospect.status in (
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.contacted.value,
    ):
        prospect.status = ProspectStatus.replied.value
    mseq._append_log(
        campaign,
        f"Nexus creó borrador automático tras respuesta · {prospect.name or prospect.email}",
        kind="outbound",
    )
    record_auto_reply_receipt(
        db,
        company_id=cid,
        campaign_id=int(campaign.id),
        prospect_id=int(prospect.id),
        inbound_gmail_message_id=inbound_gmail_message_id,
        outcome="draft",
    )
    _maybe_schedule_followup(db, campaign, prospect, timing_soft=timing_soft)
    return "draft"


def deliver_auto_reply_for_inbound(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_gmail_message_id: str,
    inbound_plain: str | None = None,
    force_immediate: bool = False,
    prior_prospect_status: str | None = None,
) -> str:
    """
    Tras inbound persistido: clasifica y borrador inmediato o programa envío.
    Devuelve: draft | sent | scheduled | skipped* 
    """
    mid = _norm_gmail_mid(inbound_gmail_message_id)

    def _skip(reason: str, detail: str = "") -> str:
        prev = get_auto_reply_receipt(db, prospect.id, mid)
        first_seen = prev is None
        if reason.startswith("skipped") or reason in _TERMINAL_AUTO_REPLY_OUTCOMES:
            record_auto_reply_receipt(
                db,
                company_id=int(campaign.company_id),
                campaign_id=int(campaign.id),
                prospect_id=int(prospect.id),
                inbound_gmail_message_id=mid,
                outcome=reason,
            )
        if first_seen and reason not in _SILENT_ACTIVITY_OUTCOMES:
            log_auto_reply_outcome_to_activity(
                campaign, prospect, mid, reason, detail=detail
            )
        return reason

    if not inbound_auto_reply_enabled():
        return _skip("skipped_disabled")

    if getattr(campaign, "automation_paused", False):
        return _skip("skipped", "campaña pausada")

    company = db.get(Company, campaign.company_id)
    if company and getattr(company, "global_automation_stop", False):
        record_ai_decision(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            event_type="inbound_skip",
            decision="skipped_global_stop",
            summary="No respondió: parada global de automatización activa",
            payload={"inbound_gmail_message_id": mid},
        )
        return _skip("skipped_disabled", "parada global")

    if getattr(prospect, "ai_paused", False):
        record_ai_decision(
            db,
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            event_type="inbound_skip",
            decision="skipped_ai_paused",
            summary="No respondió: agente IA pausado para este prospecto",
            payload={"inbound_gmail_message_id": mid},
        )
        return _skip("skipped", "IA pausada para prospecto")

    if not campaign.seller_id:
        return _skip("skipped", "sin vendedor asignado")

    inbound_row = _get_inbound_row(db, prospect.id, mid)
    if inbound_row is None:
        return _skip("skipped_no_inbound")

    if already_auto_replied(
        db,
        prospect.id,
        mid,
        executing_scheduled_task=force_immediate,
    ):
        logger.info(
            "[inbound_auto_reply] skipped_already prospect_id=%s inbound=%s force_immediate=%s",
            prospect.id,
            mid,
            force_immediate,
        )
        task = get_pending_send_task(db, prospect.id, mid)
        detail = "pendiente en cola" if task else "ya finalizado"
        return _skip("skipped_already", detail)

    if not force_immediate:
        prior = (prior_prospect_status or "").strip()
        if prior and prior in _SKIP_PRIOR_PROSPECT_STATUSES:
            logger.info(
                "[inbound_auto_reply] skipped_closed prior_status=%s prospect_id=%s",
                prior,
                prospect.id,
            )
            return _skip("skipped_closed", f"estado previo={prior}")

    to_addr = (prospect.email or "").strip()
    if not to_addr or "@" not in to_addr:
        return _skip("skipped", "email inválido")

    allowed = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    if allowed and "email" not in allowed:
        return _skip("skipped", "canal email no permitido en campaña")

    body = _extract_plain_body(inbound_plain, inbound_row)
    if len(body) < 2:
        return _skip("skipped", "cuerpo inbound vacío")

    history_rows = followup_engine._messages_desc(db, prospect.id)
    history_payload = followup_engine._payload(history_rows)

    sig = _classify_inbound(
        db, campaign=campaign, prospect=prospect, body=body, history_payload=history_payload
    )
    interest = (sig.interest_level or prospect.interest_level or "low").lower()
    objection = sig.objection_type or prospect.objection_type or "none"

    mseq._append_log(
        campaign,
        f"Nexus clasificó respuesta · {prospect.name or prospect.email}: "
        f"interés {interest}, objeción {objection}",
        kind="inbound",
    )
    record_ai_decision(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        event_type="inbound_classify",
        decision=f"interest_{interest}",
        summary=(
            f"Clasificó inbound: interés {interest}, objeción {objection}"
            + (" · pide reunión" if sig.prospect_wants_meeting else "")
            + (" · preguntas concretas" if sig.asks_concrete_questions else "")
        ),
        payload={
            "signals": signals_payload(sig),
            "inbound_snippet": body[:500],
        },
        confidence=0.82 if sig.interest_level in ("high", "medium") else 0.65,
    )

    followup_engine.apply_inbound_signals(
        db,
        prospect,
        objection_type=sig.objection_type,
        interest_level=sig.interest_level,
    )
    prospect.status = ci.prospect_status_from_inbound_signals(prospect.status, sig)

    response_class, _ = ci.classify_commercial_response(body, sig)
    reply_objective = ci.resolve_reply_objective(
        text=body,
        sig=sig,
        response_class=response_class,
    )
    explicit_slot = ci.inbound_has_explicit_meeting_slot(body)
    if explicit_slot:
        subject = "Re:"
        reply_body = ""
        timing_soft = False
        booking_priority = True
    else:
        subject, reply_body, timing_soft, booking_priority = _generate_reply_content(
            db,
            campaign=campaign,
            prospect=prospect,
            body=body,
            sig=sig,
            history_payload=history_payload,
        )

    from app.services.inbound_turn_orchestrator import resolve_inbound_scheduling_reply

    decision = resolve_inbound_scheduling_reply(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text=body,
        reply_objective=reply_objective,
        sig=sig,
        suggested_reply=reply_body,
        testing=False,
    )
    if decision.action == "skip_autoresponder":
        return _skip("skipped", decision.skip_reason or "autoresponder")
    if decision.reply_body:
        reply_body = decision.reply_body
    meeting_booking = decision.meeting_booking
    reply_objective = decision.reply_objective or reply_objective

    policy = load_behavior_policy(db, campaign.company_id)
    cal_url = (campaign.calendar_link or "").strip()
    inject_cal, cal_mandatory = should_inject_calendar_link(
        policy,
        calendar_url=cal_url,
        inbound_text=body,
        timing_soft=timing_soft,
        booking_priority=booking_priority or decision.action in (
            "booked",
            "alternatives",
            "offer_hours",
            "calendar_link",
        ),
        interest_level=sig.interest_level,
        prospect_wants_meeting=bool(sig.prospect_wants_meeting),
        explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
        substantive_questions=bool(
            sig.asks_concrete_questions or sig.objection_type in ("send_info", "other")
        ),
    )
    record_ai_decision(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        event_type="inbound_schedule_decision",
        decision=decision.action,
        summary=(
            f"Decisión de agenda: {decision.action}"
            + (f" · {decision.notes}" if decision.notes else "")
        ),
        payload={
            "action": decision.action,
            "reply_objective": reply_objective,
            "response_class": decision.response_class,
            "offered_slots": decision.offered_slots,
            "meeting_booking": bool(meeting_booking),
        },
    )
    record_ai_decision(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        event_type="inbound_compose",
        decision="calendar_link_yes" if inject_cal else "calendar_link_no",
        summary=(
            "Incluirá link de agenda en la respuesta"
            if inject_cal and cal_mandatory
            else (
                "Puede sugerir agenda al cierre"
                if inject_cal
                else "Sin link de calendario (valor primero / sin intención de agenda)"
            )
        ),
        payload={
            "booking_priority": booking_priority,
            "timing_soft": timing_soft,
            "calendar_mandatory": cal_mandatory,
            "policy_calendar_link": policy.calendar_link,
        },
    )
    _apply_post_reply_pipeline(
        db,
        campaign=campaign,
        prospect=prospect,
        sig=sig,
        body=body,
        timing_soft=timing_soft,
        booking_priority=booking_priority,
    )

    mode = _inbound_reply_mode(campaign)
    want_auto_send = mode == InboundReplyMode.auto_send.value and _truthy_env("NEXUS_AUTO_SEND_ENABLED")
    booking_confirmed = bool(meeting_booking and meeting_booking.get("confirmation_reply"))
    force_draft = should_force_draft_only(sig, body) or not want_auto_send
    if booking_confirmed and want_auto_send:
        force_draft = False
    hourly_cap = int(os.getenv("NEXUS_AUTO_SEND_HOURLY_CAP", "8"))
    at_hourly_cap = count_campaign_real_email_outbounds_last_hour(db, campaign.id) >= hourly_cap

    logger.info(
        "[inbound_auto_reply] deliver decision prospect_id=%s campaign_id=%s mode=%s "
        "want_auto_send=%s force_immediate=%s force_draft=%s at_hourly_cap=%s env=%s",
        prospect.id,
        campaign.id,
        mode,
        want_auto_send,
        force_immediate,
        force_draft,
        at_hourly_cap,
        _auto_send_env_snapshot(),
    )

    if at_hourly_cap and want_auto_send and not force_draft:
        mseq._append_log(
            campaign,
            f"Tope horario: borrador en lugar de envío automático · {prospect.name or prospect.email}",
            kind="info",
        )
        force_draft = True

    record_ai_decision(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        event_type="inbound_deliver",
        decision="pending_route",
        summary=(
            f"Ruta: mode={mode}, auto_send={want_auto_send}, draft_forzado={force_draft}, "
            f"cap_horario={at_hourly_cap}"
        ),
        payload={
            "mode": mode,
            "want_auto_send": want_auto_send,
            "force_draft": force_draft,
            "force_immediate": force_immediate,
            "at_hourly_cap": at_hourly_cap,
        },
    )

    if want_auto_send and not force_draft and not force_immediate and not at_hourly_cap:
        task_id = schedule_inbound_auto_send(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_gmail_message_id=mid,
        )
        if task_id is not None:
            return "scheduled"
        pending = get_pending_send_task(db, prospect.id, mid)
        log_auto_reply_outcome_to_activity(
            campaign,
            prospect,
            mid,
            "scheduled_duplicate",
            task_id=int(pending.id) if pending else None,
        )
        return "scheduled_duplicate"

    if want_auto_send and not force_draft and force_immediate:
        return _execute_delivery(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_gmail_message_id=inbound_gmail_message_id,
            subject=subject,
            reply_body=reply_body,
            delivery="send",
            timing_soft=timing_soft,
        )

    delivery: Literal["draft", "send"] = "send" if want_auto_send and not force_draft else "draft"
    outcome = _execute_delivery(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_gmail_message_id=inbound_gmail_message_id,
        subject=subject,
        reply_body=reply_body,
        delivery=delivery,
        timing_soft=timing_soft,
    )
    logger.info(
        "inbound auto-reply prospect_id=%s inbound=%s delivery=%s outcome=%s",
        prospect.id,
        mid,
        delivery,
        outcome,
    )
    if outcome in ("sent", "draft"):
        log_auto_reply_outcome_to_activity(campaign, prospect, mid, outcome)
    return outcome


def count_inbound_auto_reply_tasks(db: Session) -> dict[str, int]:
    """Conteos para debug del worker."""
    pending_all = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(
                OutreachTask.task_kind == TASK_KIND_INBOUND_AUTO_REPLY,
                OutreachTask.status == "pending",
            )
        )
        or 0
    )
    now = _utc_now()
    pending_due = 0
    pending_future = 0
    for row in db.scalars(
        select(OutreachTask)
        .where(
            OutreachTask.task_kind == TASK_KIND_INBOUND_AUTO_REPLY,
            OutreachTask.status == "pending",
        )
        .limit(200)
    ).all():
        if _task_is_due(row.due_at, now):
            pending_due += 1
        else:
            pending_future += 1
    return {
        "pending_all": pending_all,
        "pending_due": pending_due,
        "pending_future": pending_future,
    }


def process_due_inbound_auto_reply_tasks(db: Session) -> dict[str, Any]:
    """Ejecuta envíos programados cuyo delay ya venció."""
    now = _utc_now()
    env = _auto_send_env_snapshot()
    counts = count_inbound_auto_reply_tasks(db)

    logger.info(
        "[inbound_auto_reply] tick process_due started now=%s env=%s counts=%s",
        now.isoformat(),
        env,
        counts,
    )

    pending_rows = db.scalars(
        select(OutreachTask)
        .where(
            OutreachTask.task_kind == TASK_KIND_INBOUND_AUTO_REPLY,
            OutreachTask.status == "pending",
        )
        .order_by(OutreachTask.due_at.asc())
        .limit(80)
    ).all()
    rows = [r for r in pending_rows if _task_is_due(r.due_at, now)][:40]

    logger.info(
        "[inbound_auto_reply] pending_tasks_found total_pending=%s due_now=%s future=%s",
        counts["pending_all"],
        len(rows),
        counts["pending_future"],
    )

    processed = sent = drafted = skipped = errors = 0
    task_log: list[dict[str, Any]] = []

    for task in rows:
        meta = _parse_task_notes(task.notes)
        gid = str(meta.get("inbound_gmail_message_id") or "").strip()
        due_utc = _as_utc(task.due_at).isoformat()
        delay_ok = _task_is_due(task.due_at, now)

        if not gid or task.prospect_id is None:
            logger.warning(
                "[inbound_auto_reply] task cancelled invalid task_id=%s gid=%s prospect_id=%s",
                task.id,
                gid or "—",
                task.prospect_id,
            )
            task.status = "cancelled"
            skipped += 1
            task_log.append({"task_id": task.id, "outcome": "cancelled_invalid"})
            continue

        prospect = db.get(Prospect, task.prospect_id)
        campaign = db.get(Campaign, task.campaign_id)
        if prospect is None or campaign is None:
            logger.warning(
                "[inbound_auto_reply] task cancelled missing entities task_id=%s",
                task.id,
            )
            task.status = "cancelled"
            skipped += 1
            task_log.append({"task_id": task.id, "outcome": "cancelled_missing"})
            continue

        db.refresh(campaign)
        camp_mode = _inbound_reply_mode(campaign)
        logger.info(
            "[inbound_auto_reply] executing task_id=%s prospect_id=%s campaign_id=%s "
            "campaign_mode=%s due_at=%s delay_ok=%s inbound=%s",
            task.id,
            prospect.id,
            campaign.id,
            camp_mode,
            due_utc,
            delay_ok,
            gid,
        )

        if getattr(campaign, "automation_paused", False):
            logger.info(
                "[inbound_auto_reply] task cancelled campaign paused task_id=%s campaign_id=%s",
                task.id,
                campaign.id,
            )
            task.status = "cancelled"
            skipped += 1
            task_log.append({"task_id": task.id, "outcome": "cancelled_paused"})
            continue

        if auto_reply_is_finished(db, prospect.id, gid):
            logger.info(
                "[inbound_auto_reply] task done (receipt sent/draft) task_id=%s prospect_id=%s",
                task.id,
                prospect.id,
            )
            task.status = "completed"
            skipped += 1
            task_log.append({"task_id": task.id, "outcome": "already_finished"})
            continue

        receipt = get_auto_reply_receipt(db, prospect.id, gid)
        overdue_sec = int((now - _as_utc(task.due_at)).total_seconds())
        logger.info(
            "[inbound_auto_reply] picked_by_worker task_id=%s prospect_id=%s due_at=%s "
            "overdue_sec=%s receipt_outcome=%s inbound=%s",
            task.id,
            prospect.id,
            due_utc,
            max(0, overdue_sec),
            (receipt.outcome if receipt else None),
            gid,
        )

        try:
            outcome = deliver_auto_reply_for_inbound(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_gmail_message_id=gid,
                force_immediate=True,
            )
            processed += 1
            logger.info(
                "[inbound_auto_reply] worker finished task_id=%s outcome=%s",
                task.id,
                outcome,
            )

            if outcome == "sent":
                sent += 1
                task.status = "completed"
                _append_worker_activity(
                    campaign, prospect, task_id=task.id, outcome=outcome, inbound_gmail_message_id=gid
                )
            elif outcome == "draft":
                drafted += 1
                task.status = "completed"
                _append_worker_activity(
                    campaign, prospect, task_id=task.id, outcome=outcome, inbound_gmail_message_id=gid
                )
            elif outcome.startswith("skipped"):
                skipped += 1
                task.status = "cancelled"
                record_auto_reply_receipt(
                    db,
                    company_id=int(campaign.company_id),
                    campaign_id=int(campaign.id),
                    prospect_id=int(prospect.id),
                    inbound_gmail_message_id=gid,
                    outcome="failed",
                )
                _append_worker_activity(
                    campaign,
                    prospect,
                    task_id=task.id,
                    outcome=outcome,
                    inbound_gmail_message_id=gid,
                )
            else:
                task.status = "completed"
                _append_worker_activity(
                    campaign, prospect, task_id=task.id, outcome=outcome, inbound_gmail_message_id=gid
                )
            task_log.append({"task_id": task.id, "outcome": outcome})
        except Exception as exc:
            errors += 1
            task.status = "pending"
            record_auto_reply_receipt(
                db,
                company_id=int(campaign.company_id),
                campaign_id=int(campaign.id),
                prospect_id=int(prospect.id),
                inbound_gmail_message_id=gid,
                outcome="failed",
            )
            logger.exception(
                "[inbound_auto_reply] task error task_id=%s prospect_id=%s: %s",
                task.id,
                task.prospect_id,
                exc,
            )
            _append_worker_activity(
                campaign,
                prospect,
                task_id=task.id,
                outcome=f"error:{type(exc).__name__}",
                inbound_gmail_message_id=gid,
                detail=str(exc)[:200],
            )
            task_log.append({"task_id": task.id, "outcome": f"error:{type(exc).__name__}"})

    result = {
        "now": now.isoformat(),
        "env": env,
        **counts,
        "due_tasks": len(rows),
        "processed": processed,
        "sent": sent,
        "drafted": drafted,
        "skipped": skipped,
        "errors": errors,
        "task_log": task_log[:24],
    }
    logger.info("[inbound_auto_reply] tick process_due finished %s", result)
    return result


def _maybe_schedule_followup(
    db: Session,
    campaign: Campaign,
    prospect: Prospect,
    *,
    timing_soft: bool,
) -> None:
    if timing_soft:
        return
    if prospect.status in (
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
    ):
        return
    followup_engine.schedule_followup_task(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        campaign=campaign,
        title="Seguimiento tras respuesta automática",
    )


def ensure_auto_reply_for_gmail_message(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    gmail_message_id: str,
    inbound_plain: str | None = None,
    prior_prospect_status: str | None = None,
) -> str:
    return deliver_auto_reply_for_inbound(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_gmail_message_id=gmail_message_id,
        inbound_plain=inbound_plain,
        prior_prospect_status=prior_prospect_status,
    )
