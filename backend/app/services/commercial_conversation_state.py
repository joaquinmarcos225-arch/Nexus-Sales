"""
Estado comercial estructurado (persistido en prospect.meeting_scheduling_state["commercial"]).

Complementa prospect.email / pending_slot: recuerda intención de reunión, datos ya dados
y evita re-preguntar mail/horario cuando el hilo ya los tiene.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services import conversation_intelligence as ci
from app.services import meeting_scheduling_extras as extras

_COMM_KEY = "commercial"

# Mail inventado por baterías controladas. Nunca es de un prospecto real.
_INVENTED_TEST_EMAILS = frozenset({"prueba.controlada@costguard.com.ar"})


def normalize_stated_email(email: str | None) -> str | None:
    em = (email or "").strip().lower()
    return em if em and "@" in em else None


def persistable_stated_email(email: str | None, prospect: Any | None = None) -> str | None:
    """Mail dicho por el prospecto. No persiste fixtures inventados por pruebas."""
    del prospect
    em = normalize_stated_email(email)
    if not em or em in _INVENTED_TEST_EMAILS:
        return None
    return em


def apply_stated_email(prospect: Any, email: str | None) -> str | None:
    """Persiste un mail dicho por el prospecto. No inventa ni pega fixtures QA."""
    em = persistable_stated_email(email, prospect)
    if not em:
        return None
    current = (getattr(prospect, "email", None) or "").strip().lower()
    if em != current:
        prospect.email = em
    prospect.email_source = "prospect"
    return em


def clear_stated_email(prospect: Any) -> None:
    prospect.email = None
    if hasattr(prospect, "email_source"):
        prospect.email_source = None
    block = _commercial(prospect)
    if "email" in block or "email_collected_at" in block:
        block.pop("email", None)
        block.pop("email_collected_at", None)
        _save_commercial(prospect, block)


def sequence_send_cycle(prospect: Any) -> int:
    try:
        return max(0, int(_root(prospect).get("send_cycle") or 0))
    except (TypeError, ValueError):
        return 0


def bump_sequence_send_cycle(prospect: Any) -> int:
    """Nueva tanda (reinsert / otra campaña): permite un toque 1 nuevo, sin reintento doble."""
    root = _root(prospect)
    n = sequence_send_cycle(prospect) + 1
    root["send_cycle"] = n
    root["send_cycle_at"] = datetime.now(UTC).isoformat()
    _write_root(prospect, root)
    return n


def clear_commercial_conversation(prospect: Any) -> None:
    """Tira intención/mail/horario de una tanda anterior. La secuencia nueva arranca limpia."""
    root = _root(prospect)
    if _COMM_KEY in root:
        root.pop(_COMM_KEY, None)
        _write_root(prospect, root)

# Solo palabras de agenda real. NO incluir «dale/ok/perfecto»:
# «Dale, pero la pregunta era otra: ¿es piloto?» NO es intención de reunión.
_MEETING_INTENT_RE = re.compile(
    r"\b("
    r"agend\w*|reuni[oó]n|llamada|charla|hablemos|hablar|"
    r"coordin\w*|demo\b|videollamada|"
    r"semana\s+que\s+viene|pr[oó]xima\s+semana|"
    r"15\s*min"
    r")\b",
    re.IGNORECASE,
)

# Saludo / ack al inicio («Dale,», «Perfecto.»). Nunca define la intención sola.
_LEADING_SOFT_ACK_RE = re.compile(
    r"^(?:"
    r"dale|de\s+una|ok|okay|okey|bueno|perfecto|genial|listo|va|vamos|"
    r"s[ií]|claro|entiendo|gracias|thank(?:s| you)?"
    r")"
    r"(?:\s*[,!.…:;]+|\s+)+",
    re.IGNORECASE,
)


def substantive_inbound_body(text: str | None) -> str:
    """
    Cuerpo útil del inbound: saca acks/saludos iniciales y lee el resto.
    Regla dura: la intención se decide por TODO el mensaje, no por la 1ª palabra.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    body = raw
    for _ in range(3):
        nxt = _LEADING_SOFT_ACK_RE.sub("", body, count=1).strip()
        if nxt == body:
            break
        body = nxt
    return body or raw


def inbound_has_meeting_intent(text: str | None) -> bool:
    """True solo si el cuerpo sustantivo (sin ack inicial) habla de agenda."""
    core = substantive_inbound_body(text)
    if not core:
        return False
    return bool(_MEETING_INTENT_RE.search(ci.fold_accents(core.lower())))

_ALREADY_GAVE_DATA_RE = re.compile(
    r"\b("
    r"ya\s+te\s+lo\s+pas[eé]|ya\s+te\s+lo\s+di|ya\s+te\s+lo\s+mand[eé]|"
    r"ya\s+te\s+lo\s+envi[eé]|te\s+lo\s+di(?:je|go)\s+(?:antes|reci[eé]n)?|"
    r"ya\s+te\s+d(?:i|ije)\s+el\s+mail|ya\s+est[aá]\s+el\s+mail"
    r")\b",
    re.IGNORECASE,
)

_INBOUND_PENDING_AGENDA_RE = re.compile(
    r"\b(qued[oó]\s+algo\s+pendiente|algo\s+pendiente|me\s+fui\s+un\s+rato|"
    r"volviendo\s+a\s+la\s+reuni[oó]n|retomando\s+la\s+reuni[oó]n)\b",
    re.IGNORECASE,
)


def _root(prospect: Any) -> dict[str, Any]:
    raw = getattr(prospect, "meeting_scheduling_state", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_root(prospect: Any, root: dict[str, Any]) -> None:
    prospect.meeting_scheduling_state = json.dumps(root) if root else None


def _commercial(prospect: Any) -> dict[str, Any]:
    block = _root(prospect).get(_COMM_KEY)
    return dict(block) if isinstance(block, dict) else {}


def _save_commercial(prospect: Any, block: dict[str, Any]) -> None:
    root = _root(prospect)
    if block:
        root[_COMM_KEY] = block
    elif _COMM_KEY in root:
        root.pop(_COMM_KEY, None)
    _write_root(prospect, root)


def prospect_typed_chat_email(prospect: Prospect) -> str | None:
    """Mail que el prospecto escribió en el hilo (no el del CRM/enrichment)."""
    return persistable_stated_email(_commercial(prospect).get("email"), prospect)


def prospect_booking_email(prospect: Prospect) -> str | None:
    """Mail válido para invitado Calendar (solo si lo dio el prospecto o quedó en estado)."""
    em = persistable_stated_email(getattr(prospect, "email", None), prospect)
    if em and (getattr(prospect, "email_source", None) or "") == "prospect":
        return em
    stored = prospect_typed_chat_email(prospect)
    if stored:
        return stored
    return persistable_stated_email(extras.proposed_invite_email(prospect), prospect)


def _slot_from_commercial_block(prospect: Prospect, timezone: str) -> datetime | None:
    """Horario acordado en mensajes anteriores (estado commercial.slot_at)."""
    from zoneinfo import ZoneInfo

    raw = (_commercial(prospect).get("slot_at") or "").strip()
    if not raw:
        return None
    try:
        slot = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if slot.tzinfo is None:
        slot = slot.replace(tzinfo=ZoneInfo(timezone))
    return slot


def sync_commercial_facts(
    prospect: Prospect,
    inbound_text: str | None,
    *,
    timezone: str,
) -> dict[str, Any]:
    """Extrae y persiste email / intención / horario parcial del inbound."""
    body = (inbound_text or "").strip()
    block = _commercial(prospect)
    was_booked = block.get("status") == "booked"

    extracted = apply_stated_email(prospect, extras.extract_email(body))
    if extracted:
        block["email"] = extracted
        block["email_collected_at"] = datetime.now(UTC).isoformat()

    if body and inbound_has_meeting_intent(body):
        block["meeting_intent"] = True

    if extras.pending_slot(prospect) or extras.reserved_slot_for_email_booking(prospect):
        if not was_booked:
            block["status"] = "collecting_email"
    elif block.get("email") and not block.get("slot_at"):
        if not was_booked:
            block["status"] = "collecting_time"
    elif block.get("email") and block.get("slot_at"):
        if not was_booked:
            block["status"] = "ready_to_book"

    from app.services.meeting_slot_parser import parse_meeting_slot

    from app.services.prospect_timezone import timezone_hint_from_text

    hinted = timezone_hint_from_text(body)
    if hinted:
        block["slot_timezone"] = hinted
    slot = parse_meeting_slot(body, timezone=timezone) if body else None
    if slot is not None:
        block["slot_at"] = slot.isoformat()
        if not was_booked:
            block["status"] = "ready_to_book" if block.get("email") else "collecting_email"

    if block.get("meeting_intent") and not block.get("status"):
        block["status"] = "collecting_details"

    _save_commercial(prospect, block)
    return block


def inbound_already_gave_data_complaint(text: str | None) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw))
    return bool(_ALREADY_GAVE_DATA_RE.search(folded))


def inbound_requires_orchestrator(prospect: Prospect, inbound_text: str | None) -> bool:
    """True → no usar LLM aislado; pasar por resolve_inbound_scheduling_reply."""
    body = (inbound_text or "").strip()
    if not body:
        return False
    if extras.extract_email(body):
        return True
    from app.services.inbound_turn_orchestrator import inbound_mentions_scheduling, inbound_refers_to_prior_schedule

    try:
        from app.services.whatsapp_reply_compose import inbound_is_info_question

        info_q = inbound_is_info_question(body)
    except Exception:
        info_q = False
    # Pregunta de info (onboarding/CRM/etc.): no forzar orquestador de agenda.
    if not info_q and (
        inbound_mentions_scheduling(body) or inbound_refers_to_prior_schedule(body)
    ):
        return True
    if inbound_already_gave_data_complaint(body):
        return True
    if extras.inbound_confirms_proposed_email(prospect, body) or extras.inbound_confirms_email(body):
        return True
    if extras.pending_slot(prospect) or extras.reserved_slot_for_email_booking(prospect):
        # Pedimos el mail o hay horario en espera: no dejar que el LLM arranque de cero.
        return True
    block = _commercial(prospect)
    if block.get("meeting_intent") and block.get("status") in {
        "collecting_time",
        "collecting_email",
        "collecting_details",
        "ready_to_book",
    }:
        if (
            inbound_mentions_scheduling(body)
            or inbound_refers_to_prior_schedule(body)
            or extras.extract_email(body)
            or inbound_already_gave_data_complaint(body)
            or _INBOUND_PENDING_AGENDA_RE.search(body)
        ):
            return True
        return False
    if prospect_booking_email(prospect) and block.get("meeting_intent"):
        if (
            inbound_mentions_scheduling(body)
            or inbound_refers_to_prior_schedule(body)
            or extras.extract_email(body)
        ):
            return True
        return False
    if inbound_has_meeting_intent(body):
        return True
    from app.services.meeting_slot_parser import prospect_proposed_meeting_slot

    if prospect_proposed_meeting_slot(body):
        return True
    return False


def build_ask_time_only_reply(*, prospect_name: str | None, email: str) -> str:
    em = (email or "").strip()
    return (
        f"Perfecto, anoto {em}. "
        "¿Qué día y horario te queda bien para una charla de 15 min?"
    )


def build_ask_slot_for_meeting_intent(*, prospect_name: str | None) -> str:
    return (
        "Perfecto. "
        "¿Qué día y horario te quedarían cómodos para una charla de 15 min?"
    )


def build_ack_email_have_need_time(*, prospect_name: str | None, email: str) -> str:
    return (
        f"Tenés razón, ya tengo tu mail ({email.strip()}). "
        "¿Qué día y horario te queda bien para la charla?"
    )


def _stored_slot_phrase_for_reply(prospect: Prospect, campaign: Campaign, timezone: str) -> str | None:
    slot = _slot_from_commercial_block(prospect, timezone)
    if slot is None:
        return None
    from app.services.meeting_booking import _format_slot_for_confirmation

    return _format_slot_for_confirmation(slot, timezone)


def _recover_slot_from_ask_email_outbound(
    db: Session, *, prospect: Prospect, timezone: str
) -> datetime | None:
    """Si el último outbound pidió mail tras confirmar horario, parsea ese horario."""
    from sqlalchemy import select

    from app.models.outreach import OutreachMessage
    from app.services.meeting_slot_parser import parse_meeting_slot

    rows = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect.id),
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "whatsapp",
        )
        .order_by(OutreachMessage.id.desc())
        .limit(6)
    ).all()
    for row in rows:
        plain = (getattr(row, "message", None) or "").strip()
        if not plain:
            continue
        low = plain.lower()
        if "entonces" not in low:
            continue
        if "mail" not in low and "invitaci" not in low:
            continue
        # Quitar prefijos de auto-reply / meta.
        for marker in ("\n\n", "\n"):
            if "[WhatsApp" in plain:
                plain = plain.split(marker, 1)[-1].strip()
        slot = parse_meeting_slot(plain, timezone=timezone)
        if slot is not None:
            return slot
    return None


def mark_meeting_booked_in_state(
    prospect: Prospect,
    *,
    calendar_event_id: str | None,
    slot_iso: str | None = None,
) -> None:
    """
    Calendar ya tiene el evento. NO cierra el chat ni pasa a «agendado» de producto:
    eso corre recién cuando sale el outbound de confirmación (invite / nos vemos).
    """
    block = _commercial(prospect)
    block["status"] = "booked"
    block["calendar_event_id"] = calendar_event_id
    if slot_iso:
        block["slot_at"] = slot_iso
    block["booked_at"] = datetime.now(UTC).isoformat()
    # Explicit: keep chat open so confirmation auto-reply can send.
    block.pop("auto_reply_closed", None)
    block.pop("closed_at", None)
    _save_commercial(prospect, block)


def try_advance_meeting_booking(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    testing: bool = False,
) -> Any | None:
    """
    Si ya hay mail del prospecto + horario (mensaje o estado), intenta book real.
    Devuelve InboundTurnDecision o None.
    """
    from app.services.inbound_turn_orchestrator import (
        InboundTurnDecision,
        _booking_decision,
        inbound_mentions_scheduling,
        inbound_refers_to_prior_schedule,
    )
    from app.services.meeting_booking import (
        _active_meeting_row,
        _book_with_calendar_availability,
        resolve_meeting_booking_from_prospect_thread,
    )
    from app.services.meeting_slot_parser import parse_meeting_duration_minutes, parse_meeting_slot

    from app.services.prospect_timezone import operator_timezone, timezone_hint_from_text

    hinted = timezone_hint_from_text(inbound_text) or (
        _commercial(prospect).get("slot_timezone") or ""
    ).strip() or None
    tz = hinted or operator_timezone(
        user=getattr(campaign, "seller", None), campaign=campaign
    )
    cal_link = (getattr(campaign, "calendar_link", None) or "").strip() or None
    body = (inbound_text or "").strip()

    meeting = _active_meeting_row(db, prospect)
    comm_before = _commercial(prospect)
    already_booked = (
        extras.chat_closed_after_booking(prospect)
        or bool(comm_before.get("status") == "booked" and comm_before.get("calendar_event_id"))
        or (getattr(prospect, "status", None) or "") == "meeting_booked"
    )
    if already_booked:
        return InboundTurnDecision(
            action="skip_autoresponder",
            reply_objective="agendar",
            response_class="interesado",
            skip_reason="reunión ya agendada",
            notes="Ya hay reunión: no contestar más inbound",
        )

    sync_commercial_facts(prospect, body, timezone=tz)

    email = prospect_booking_email(prospect)
    if email:
        apply_stated_email(prospect, email)

    parsed_from_message = parse_meeting_slot(body, timezone=tz) if body else None
    slot = parsed_from_message
    duration = parse_meeting_duration_minutes(body) if body else 30
    is_reschedule = meeting is not None

    offered = extras.offered_slots(prospect)
    picked_offered = extras.pick_offered_slot(body, offered, timezone=tz) if offered else None
    confirming_mail = bool(
        extras.extract_email(body) or extras.inbound_confirms_proposed_email(prospect, body)
    )
    if picked_offered is not None and not confirming_mail:
        slot = picked_offered
        parsed_from_message = picked_offered
        extras.clear_pending_slot(prospect)
    elif offered and parsed_from_message is None and not confirming_mail:
        # Le acabamos de ofrecer horarios: un «dale» no reusa el horario ocupado anterior.
        reserved = None
    else:
        reserved = extras.reserved_slot_for_email_booking(prospect)
        if slot is None and reserved is not None:
            slot = reserved["at"]
            duration = int(reserved.get("duration") or duration)
            is_reschedule = bool(reserved.get("is_reschedule"))

        if slot is None:
            slot = _slot_from_commercial_block(prospect, tz)

    if slot is None and email and (
        inbound_refers_to_prior_schedule(body) or inbound_already_gave_data_complaint(body)
    ):
        if hasattr(db, "scalars"):
            thread_slot, thread_dur = resolve_meeting_booking_from_prospect_thread(
                db, prospect=prospect, campaign=campaign
            )
            if thread_slot is not None:
                slot = thread_slot
                duration = thread_dur

    if inbound_already_gave_data_complaint(body) and email and slot is None:
        stored = _slot_from_commercial_block(prospect, tz)
        if stored is None:
            return InboundTurnDecision(
                action="ask_slot",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=build_ack_email_have_need_time(prospect_name=prospect.name, email=email),
                notes="Prospecto reclama dato ya dado; no re-pedir mail",
            )
        slot = stored

    should_check_calendar = bool(
        slot is not None
        and (
            parsed_from_message is not None
            or email
            or             extras.extract_email(body)
            or extras.inbound_confirms_email(body)
            or extras.inbound_confirms_proposed_email(prospect, body)
            or extras.inbound_refuses_email(body)
            or inbound_mentions_scheduling(body)
        )
    )
    if should_check_calendar:
        just_gave_email = bool(extras.extract_email(body)) or extras.inbound_confirms_proposed_email(
            prospect, body
        )
        if just_gave_email:
            extras.mark_chat_email_confirmed(prospect)
        typed_in_chat = bool(prospect_typed_chat_email(prospect))
        booking = _book_with_calendar_availability(
            db,
            campaign=campaign,
            prospect=prospect,
            slot=slot,
            tz=tz,
            cal_link=cal_link,
            testing=testing,
            is_reschedule=is_reschedule,
            duration_minutes=duration,
            invite_just_given=bool(just_gave_email or typed_in_chat),
        )
        decision = _booking_decision(
            booking,
            objective="agendar",
            response_class="interesado",
            notes="Agenda: Calendar decide si el horario está libre",
        )
        if decision is not None and decision.action == "booked":
            mark_meeting_booked_in_state(
                prospect,
                calendar_event_id=str((booking or {}).get("google_calendar_event_id") or ""),
                slot_iso=slot.isoformat(),
            )
        return decision

    if extras.extract_email(body) and not slot and not reserved:
        if meeting is not None and getattr(meeting, "google_calendar_event_id", None):
            return None
        em = prospect_booking_email(prospect) or extras.extract_email(body)
        if em:
            apply_stated_email(prospect, em)
            extras.mark_chat_email_confirmed(prospect)
            # Si acabamos de pedir mail («Genial, … entonces»), retomar ese horario: no re-pedir día.
            recovered = _slot_from_commercial_block(prospect, tz)
            if recovered is None and hasattr(db, "scalars"):
                recovered = _recover_slot_from_ask_email_outbound(db, prospect=prospect, timezone=tz)
            if recovered is not None:
                booking = _book_with_calendar_availability(
                    db,
                    campaign=campaign,
                    prospect=prospect,
                    slot=recovered,
                    tz=tz,
                    cal_link=cal_link,
                    testing=testing,
                    is_reschedule=is_reschedule,
                    duration_minutes=duration,
                    invite_just_given=True,
                )
                decision = _booking_decision(
                    booking,
                    objective="agendar",
                    response_class="interesado",
                    notes="Mail recibido: retomé el horario pendiente/comercial",
                )
                if decision is not None and decision.action == "booked":
                    mark_meeting_booked_in_state(
                        prospect,
                        calendar_event_id=str((booking or {}).get("google_calendar_event_id") or ""),
                        slot_iso=recovered.isoformat(),
                    )
                if decision is not None:
                    return decision
            return InboundTurnDecision(
                action="ask_slot",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=build_ask_time_only_reply(prospect_name=prospect.name, email=em),
                notes="Mail guardado; falta horario",
            )

    block = _commercial(prospect)
    stored_slot = _slot_from_commercial_block(prospect, tz)

    if block.get("meeting_intent") and block.get("status") in {
        "collecting_details",
        "collecting_time",
    } and not stored_slot and not slot:
        from app.services.inbound_turn_orchestrator import inbound_mentions_scheduling
        from app.services.whatsapp_reply_compose import inbound_is_product_aside

        # Pregunta de producto/adopción: no pisar con «¿qué día te queda?»
        if inbound_is_product_aside(body):
            return None
        if inbound_mentions_scheduling(body) or inbound_has_meeting_intent(body):
            return InboundTurnDecision(
                action="ask_slot",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=build_ask_slot_for_meeting_intent(prospect_name=prospect.name),
                notes="Intención de reunión; pedir día y hora concretos",
            )

    return None
