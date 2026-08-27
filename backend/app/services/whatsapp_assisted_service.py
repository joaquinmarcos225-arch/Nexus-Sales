"""WhatsApp Assisted — cola humana vía WhatsApp Web (sin Meta Cloud API)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.whatsapp_assisted import WhatsAppAssistQueueRead, WhatsAppAssistTaskRead
from app.services import followup_engine
from app.services.multichannel_sequence import (
    _append_log,
    _day_index_one_based,
    _update_group_for_prospect,
)
from app.services.whatsapp_cloud_service import normalize_whatsapp_digits

STATUS_NONE = "none"
STATUS_SUGGESTED = "suggested"
STATUS_PREPARED = "prepared"
STATUS_OPENED = "opened"
STATUS_SENT = "sent"


def read_assist_status(prospect: Prospect) -> str:
    raw = (getattr(prospect, "whatsapp_assist_status", None) or "").strip().lower()
    if raw in {STATUS_SUGGESTED, STATUS_PREPARED, STATUS_OPENED, STATUS_SENT}:
        return raw
    if getattr(prospect, "whatsapp_sdr_marked_sent_at", None) and not (
        prospect.whatsapp_assisted_draft or ""
    ).strip():
        return STATUS_SENT
    draft = (prospect.whatsapp_assisted_draft or "").strip()
    if not draft:
        return STATUS_NONE
    if getattr(prospect, "whatsapp_last_assisted_at", None):
        return STATUS_OPENED
    return STATUS_SUGGESTED


def _set_assist_status(prospect: Prospect, status: str) -> None:
    prospect.whatsapp_assist_status = status


def _load_campaign(db: Session, prospect: Prospect) -> Campaign:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(selectinload(Campaign.product), selectinload(Campaign.seller))
    ).first()
    if campaign is None:
        raise ValueError("Campaña no encontrada")
    return campaign


def _log_activity(campaign: Campaign, message: str, *, kind: str) -> None:
    _append_log(campaign, message, kind=kind)


def _priority_for(prospect: Prospect) -> str:
    level = (getattr(prospect, "interest_level", None) or "").lower()
    if level in {"high", "alta"}:
        return "alta"
    if level in {"medium", "media"}:
        return "media"
    group = (prospect.sequence_group or "").lower()
    if group in {"proximo_follow_up", "follow_ups"}:
        return "alta"
    return "baja"


def _has_pending_followup(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(func.count())
        .select_from(OutreachTask)
        .where(
            OutreachTask.prospect_id == prospect_id,
            OutreachTask.status == "pending",
        )
    )
    return int(n or 0) > 0


def require_whatsapp_phone(prospect: Prospect) -> str:
    digits = normalize_whatsapp_digits(prospect.phone, prospect.whatsapp)
    if not digits:
        raise ValueError(
            "Este prospecto no tiene un teléfono/WhatsApp válido. "
            "Agregá un número con código de país."
        )
    return digits


def prospect_whatsapp_digits(prospect: Prospect) -> str | None:
    return normalize_whatsapp_digits(prospect.phone, prospect.whatsapp)


def wa_web_send_url(phone_digits: str, text: str = "") -> str:
    """URL oficial de WhatsApp Web (reusa sesión del Chrome del usuario)."""
    digits = re.sub(r"\D", "", phone_digits or "")
    base = f"https://web.whatsapp.com/send?phone={digits}"
    body = (text or "").strip()
    if body:
        return f"{base}&text={quote(body)}"
    return base


def wa_app_send_url(phone_digits: str, text: str = "") -> str:
    """wa.me → app de escritorio si está instalada; si no, Web."""
    digits = re.sub(r"\D", "", phone_digits or "")
    body = (text or "").strip()
    if body:
        return f"https://wa.me/{digits}?text={quote(body)}"
    return f"https://wa.me/{digits}"


def wa_desktop_protocol_url(phone_digits: str, text: str = "") -> str:
    """Deep link whatsapp:// para la app nativa."""
    digits = re.sub(r"\D", "", phone_digits or "")
    body = (text or "").strip()
    base = f"whatsapp://send?phone={digits}"
    if body:
        return f"{base}&text={quote(body)}"
    return base


def mark_draft_suggested(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    draft: str,
    *,
    log_event: bool = True,
) -> None:
    del db
    prospect.whatsapp_assisted_draft = draft
    prospect.whatsapp_sdr_marked_sent_at = None
    _set_assist_status(prospect, STATUS_SUGGESTED)
    if log_event:
        name = prospect.name or f"Prospecto #{prospect.id}"
        _log_activity(
            campaign,
            f"Borrador WhatsApp listo para enviar · {name}.",
            kind="whatsapp_draft",
        )


def prepare_whatsapp_reply_after_inbound(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> tuple[str | None, bool, dict]:
    """
    Tras inbound WhatsApp: genera réplica contextual al texto entrante.

    Returns:
        (draft, skipped_autoresponder, meta)
        skipped=True → no encolar ni fallback (autoresponder / calendar reconnect).
        meta puede incluir calendar_reconnect_required + operator_message.
    """
    require_whatsapp_phone(prospect)
    meta: dict = {}
    history = list(
        db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect.id)
            .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
        ).all()
    )
    last_inbound = next(
        (
            m
            for m in reversed(history)
            if m.direction == "inbound" and m.channel == "whatsapp"
        ),
        None,
    )
    inbound_text = ""
    if last_inbound is not None:
        inbound_text = (last_inbound.message or "").strip()
        prefix = "[WhatsApp · respuesta real]"
        if inbound_text.startswith(prefix):
            inbound_text = inbound_text.split("\n", 1)[-1].strip()
        elif inbound_text.startswith("[WhatsApp"):
            inbound_text = inbound_text.split("\n", 1)[-1].strip()

    if not inbound_text:
        return None, False, meta

    from app.services import conversation_intelligence as ci
    from app.services.inbound_turn_orchestrator import resolve_inbound_scheduling_reply
    from app.services.whatsapp_reply_compose import compose_whatsapp_inbound_reply

    # Limpiar borrador viejo: la réplica es al mensaje nuevo.
    prospect.whatsapp_assisted_draft = None
    prospect.whatsapp_assist_session_id = None
    prospect.whatsapp_last_assisted_at = None
    prospect.whatsapp_sdr_marked_sent_at = None
    if read_assist_status(prospect) != STATUS_SENT:
        _set_assist_status(prospect, STATUS_SUGGESTED)

    draft = compose_whatsapp_inbound_reply(
        db,
        prospect=prospect,
        campaign=campaign,
        inbound_text=inbound_text,
        history=history,
    )

    prior_interest = (prospect.interest_level or "low").strip() or "low"
    try:
        sig = ci.build_signals_from_keywords(
            ci.normalize_inbound_text_for_classification(inbound_text),
            prior_interest,
        )
    except Exception:
        sig = None
    response_class = "otro"
    reply_objective = "seguimiento"
    if sig is not None:
        response_class, _ = ci.classify_commercial_response(inbound_text, sig)
        reply_objective = ci.resolve_reply_objective(
            text=inbound_text,
            sig=sig,
            response_class=response_class,
        )
    decision = resolve_inbound_scheduling_reply(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text=inbound_text,
        reply_objective=reply_objective,
        sig=sig,
        suggested_reply=draft or "",
        testing=False,
    )
    if decision.action == "skip_autoresponder":
        return None, True, meta
    # Calendar necesita reconexión: no generar borrador para el prospecto.
    mb = getattr(decision, "meeting_booking", None) or {}
    if (
        getattr(decision, "notes", None) == "calendar_reconnect_required"
        or bool(mb.get("requires_calendar_reconnect"))
    ):
        alert = (getattr(decision, "skip_reason", None) or "").strip() or (
            "Google Calendar necesita reconexión. "
            "Andá a Configuración → Integraciones."
        )
        _log_activity(
            campaign,
            alert,
            kind="calendar_reconnect",
        )
        prospect.whatsapp_assisted_draft = None
        meta = {
            "calendar_reconnect_required": True,
            "operator_message": alert,
        }
        return None, True, meta
    if decision.reply_body:
        draft = decision.reply_body
    from app.services.whatsapp_reply_compose import (
        _extract_time_hint,
        _looks_like_cold_open,
        strip_whatsapp_email_signature,
        whatsapp_inbound_offline_draft,
    )

    if not (draft or "").strip() or _looks_like_cold_open(draft or ""):
        draft = whatsapp_inbound_offline_draft(
            prospect, campaign, inbound_text=inbound_text, db=db
        )
    # Si pidió hora concreta y el borrador re-pregunta “¿agendamos?”, reemplazar.
    if _extract_time_hint(inbound_text):
        low_d = (draft or "").lower()
        if any(
            p in low_d
            for p in (
                "te parece agendar",
                "agendar una reunión",
                "agendar una reunion",
                "qué día de esta semana",
                "que dia de esta semana",
            )
        ):
            draft = whatsapp_inbound_offline_draft(
                prospect, campaign, inbound_text=inbound_text, db=db
            )
    draft = strip_whatsapp_email_signature(draft or "")
    # Nunca encolar texto de operador (Integraciones / reconexión).
    low_final = draft.lower()
    if "configuraci" in low_final and "integraciones" in low_final:
        alert = (
            "Google Calendar necesita reconexión. "
            "Andá a Configuración → Integraciones."
        )
        _log_activity(campaign, alert, kind="calendar_reconnect")
        prospect.whatsapp_assisted_draft = None
        return None, True, {
            "calendar_reconnect_required": True,
            "operator_message": alert,
        }
    if not (draft or "").strip():
        return None, False, meta
    mark_draft_suggested(db, prospect, campaign, draft.strip(), log_event=True)
    return draft.strip(), False, meta


def queue_whatsapp_sequence_touch(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    draft_body: str,
    *,
    log_event: bool = True,
) -> str:
    """Encola un toque WhatsApp asistido. Devuelve 'message' o 'skip'."""
    if not prospect_whatsapp_digits(prospect):
        return "skip"
    draft = (draft_body or "").strip()
    if not draft:
        return "skip"
    # Un solo canal asistido vivo: al pasar a WA, LinkedIn sale de la bandeja.
    from app.services.prospect_sequence import _clear_assisted_live_queue

    _clear_assisted_live_queue(prospect, "linkedin")
    mark_draft_suggested(db, prospect, campaign, draft, log_event=log_event)
    return "message"


def ensure_whatsapp_draft(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    del db, campaign
    draft = (prospect.whatsapp_assisted_draft or "").strip()
    if draft:
        return draft
    raise ValueError("No hay borrador WhatsApp pendiente. Ejecutá el toque de secuencia primero.")


def begin_assist_session(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> tuple[str, str, str]:
    """Inicia sesión asistida. Retorna (draft, session_id, phone_digits)."""
    phone = require_whatsapp_phone(prospect)
    draft = ensure_whatsapp_draft(db, prospect, campaign)
    name = prospect.name or f"Prospecto #{prospect.id}"
    now = datetime.now(UTC)

    if read_assist_status(prospect) == STATUS_SUGGESTED:
        _set_assist_status(prospect, STATUS_PREPARED)
        _log_activity(
            campaign,
            f"Mensaje preparado para WhatsApp · {name}.",
            kind="whatsapp_prepared",
        )

    session_id = str(uuid.uuid4())
    prospect.whatsapp_assist_session_id = session_id
    prospect.whatsapp_last_assisted_at = now
    _set_assist_status(prospect, STATUS_OPENED)

    _log_activity(
        campaign,
        f"WhatsApp Web abierto · {name} (esperando envío manual del SDR).",
        kind="whatsapp_opened",
    )
    return draft, session_id, phone


def abandon_assist_session(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    del db
    name = prospect.name or f"Prospecto #{prospect.id}"
    if (prospect.whatsapp_assisted_draft or "").strip():
        _set_assist_status(prospect, STATUS_SUGGESTED)
    prospect.whatsapp_assist_session_id = None
    prospect.whatsapp_last_assisted_at = None
    _log_activity(
        campaign,
        f"WhatsApp sin confirmar envío · {name} (sigue en cola).",
        kind="whatsapp_pending",
    )
    return STATUS_SUGGESTED


def confirm_whatsapp_sent(db: Session, prospect: Prospect) -> str:
    require_whatsapp_phone(prospect)
    campaign = _load_campaign(db, prospect)
    draft = (prospect.whatsapp_assisted_draft or "").strip()
    if not draft:
        if read_assist_status(prospect) == STATUS_SENT or getattr(
            prospect, "whatsapp_sdr_marked_sent_at", None
        ):
            return "Envío ya confirmado en WhatsApp."
        raise ValueError("No hay borrador WhatsApp pendiente para este prospecto.")

    name = prospect.name or f"Prospecto #{prospect.id}"
    body = f"[WhatsApp · enviado por SDR]\n{draft}"

    # Idempotencia: extensión + botón UI pueden confirmar el mismo envío a la vez.
    recent_same = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "outbound",
            OutreachMessage.message == body,
        )
        .order_by(OutreachMessage.id.desc())
        .limit(1)
    ).first()
    already_logged = recent_same is not None

    # Liberar borrador ya: un segundo mark-sent concurrente no debe reinsertar.
    prospect.whatsapp_assisted_draft = None
    db.flush()

    if not already_logged:
        try:
            from app.services.lead_sourcing.cogs_runtime_metrics import record_wa_sent

            record_wa_sent(1)
        except Exception:  # noqa: BLE001
            pass
        db.add(
            OutreachMessage(
                prospect_id=prospect.id,
                campaign_id=campaign.id,
                sender_type="user",
                message=body,
                channel="whatsapp",
                direction="outbound",
            )
        )
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=draft,
        )

    if prospect.status in {
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
    }:
        prospect.status = ProspectStatus.contacted.value

    prospect.whatsapp_sdr_marked_sent_at = datetime.now(UTC)
    prospect.whatsapp_last_assisted_at = None
    prospect.whatsapp_assist_session_id = None
    _set_assist_status(prospect, STATUS_SENT)

    from app.services.prospect_sequence import complete_pending_whatsapp_sequence_touch

    complete_pending_whatsapp_sequence_touch(db, prospect=prospect)

    day = _day_index_one_based(prospect.sequence_started_at)
    _update_group_for_prospect(
        prospect,
        day,
        _has_pending_followup(db, prospect.id),
    )

    _log_activity(
        campaign,
        f"Mensaje confirmado enviado en WhatsApp · {name}.",
        kind="whatsapp_sent",
    )
    return "Envío confirmado en WhatsApp."


def is_queue_eligible(prospect: Prospect, campaign=None) -> bool:
    if not prospect_whatsapp_digits(prospect):
        return False
    if not (prospect.whatsapp_assisted_draft or "").strip():
        return False
    if read_assist_status(prospect) == STATUS_SENT:
        return False
    try:
        from app.services.prospect_sequence import (
            _sequence_held_for_conversation,
            next_executable_channel,
        )

        # Réplica tras inbound: secuencia pausada pero el borrador debe verse en cola.
        if _sequence_held_for_conversation(prospect):
            return True
        ch = next_executable_channel(prospect, campaign)
        if ch != "whatsapp":
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def build_task_read(prospect: Prospect) -> WhatsAppAssistTaskRead:
    status = read_assist_status(prospect)
    digits = prospect_whatsapp_digits(prospect) or ""
    message = (prospect.whatsapp_assisted_draft or "").strip()
    return WhatsAppAssistTaskRead(
        prospect_id=prospect.id,
        prospect_name=prospect.name or f"Prospecto #{prospect.id}",
        company_name=prospect.company_name,
        phone_digits=digits,
        phone_display=(prospect.whatsapp or prospect.phone or digits or "").strip(),
        message=message,
        assist_status=status,
        session_id=getattr(prospect, "whatsapp_assist_session_id", None),
        priority=_priority_for(prospect),
        sequence_group=getattr(prospect, "sequence_group", None),
        opened_at=getattr(prospect, "whatsapp_last_assisted_at", None),
        send_url=wa_web_send_url(digits, message) if digits else None,
        app_send_url=wa_app_send_url(digits, message) if digits else None,
        desktop_protocol_url=wa_desktop_protocol_url(digits, message) if digits else None,
    )


def build_campaign_queue(
    db: Session, campaign_id: int, viewer=None
) -> WhatsAppAssistQueueRead:
    from app.services import daily_send_limits as dsl
    from app.services import queue_day_schedule as qds
    from app.schemas.whatsapp_assisted import WhatsAppAssistDayBucket

    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    seller_id = int(getattr(campaign, "seller_id", 0) or 0)
    if viewer is not None and campaign is not None:
        from app.services.campaign_visibility import filter_prospects_for_viewer

        rows = filter_prospects_for_viewer(viewer, campaign, list(rows))
    tasks: list[WhatsAppAssistTaskRead] = []
    changed = False
    for p in rows:
        try:
            from app.services.prospect_sequence import expire_unsent_assisted_touches_for_calendar

            if expire_unsent_assisted_touches_for_calendar(
                db, prospect=p, campaign=campaign
            ):
                changed = True
            from app.services.prospect_sequence import ensure_single_assisted_live_queue

            if ensure_single_assisted_live_queue(p, campaign):
                changed = True
        except Exception:  # noqa: BLE001
            pass
        if not is_queue_eligible(p, campaign):
            continue
        tasks.append(build_task_read(p))

    if changed:
        db.commit()

    priority_order = {"alta": 0, "media": 1, "baja": 2}
    status_order = {STATUS_OPENED: 0, STATUS_PREPARED: 1, STATUS_SUGGESTED: 2}
    tasks.sort(
        key=lambda t: (
            priority_order.get(t.priority or "media", 9),
            status_order.get(t.assist_status, 9),
            (t.prospect_name or "").lower(),
        )
    )

    wa_limit = dsl.limit_for(dsl.KIND_WHATSAPP)
    wa_bonus = dsl.whatsapp_inbounds_today(db, seller_id) if seller_id else 0
    wa_effective = dsl.whatsapp_effective_limit_today(db, seller_id) if seller_id else wa_limit
    wa_remaining = dsl.remaining(db, seller_id, dsl.KIND_WHATSAPP) if seller_id else wa_limit
    day_rows = qds.schedule_single_budget(
        tasks,
        daily_limit=wa_limit,
        remaining_today=wa_remaining,
    )
    day_buckets: list[WhatsAppAssistDayBucket] = []
    for day_offset, day_tasks in day_rows:
        day_buckets.append(
            WhatsAppAssistDayBucket(
                day_offset=day_offset,
                label=qds.day_label(day_offset),
                actionable=day_offset == 0,
                limit=wa_effective if day_offset == 0 else wa_limit,
                scheduled=len(day_tasks),
                tasks=day_tasks,
            )
        )
    today_tasks = day_buckets[0].tasks if day_buckets else []
    hidden = qds.deferred_count(day_rows)

    return WhatsAppAssistQueueRead(
        campaign_id=campaign_id,
        tasks=today_tasks,
        total_pending=len(tasks),
        limit=wa_limit,
        effective_limit_today=wa_effective,
        bonus_from_replies=wa_bonus,
        remaining_today=wa_remaining,
        hidden_by_cap=hidden,
        days=day_buckets,
    )
