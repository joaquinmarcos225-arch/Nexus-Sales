"""
Cerebro compartido de inbound: entender → decidir → armar respuesta de agenda.

Usado por Gmail auto-reply y LinkedIn (cola asistida). WhatsApp puede engancharse igual.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services import conversation_intelligence as ci
from app.services.ai_behavior_policy import prospect_requests_calendar_link
from app.services.google_calendar_availability import format_slot_local

logger = logging.getLogger(__name__)

InboundAction = Literal[
    "skip_autoresponder",
    "booked",
    "alternatives",
    "booking_failed",
    "offer_hours",
    "calendar_link",
    "normal_reply",
]

_HOURS_ASK_RE = re.compile(
    r"\b("
    r"que\s+horarios?|cuales?\s+horarios?|horarios?\s+(tenes|tienes|tenés|disponibles?)|"
    r"cuando\s+(podes|puedes|podés|te\s+queda|estas\s+libre|estás\s+libre)|"
    r"disponibilidad|huecos?|slots?|"
    r"que\s+dias?\s+(tenes|tienes|tenés)|"
    r"mandame\s+horarios?|pasame\s+horarios?"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class InboundTurnDecision:
    action: InboundAction
    reply_objective: str
    response_class: str
    reply_body: str | None = None
    meeting_booking: dict[str, Any] | None = None
    skip_reason: str | None = None
    offered_slots: list[str] = field(default_factory=list)
    notes: str = ""


def _booking_failure_decision(
    *,
    booking: dict[str, Any],
    objective: str,
    response_class: str,
) -> InboundTurnDecision | None:
    """Fallo de booking: reconexión = sin reply al prospecto; otro fallo = reply seguro."""
    if booking.get("requires_calendar_reconnect"):
        return InboundTurnDecision(
            action="booking_failed",
            reply_objective=objective,
            response_class=response_class,
            reply_body=None,
            meeting_booking=booking,
            skip_reason=str(
                booking.get("operator_alert")
                or "Google Calendar necesita reconexión."
            ),
            notes="calendar_reconnect_required",
        )
    if booking.get("booking_failed_reply"):
        return InboundTurnDecision(
            action="booking_failed",
            reply_objective=objective,
            response_class=response_class,
            reply_body=str(booking["booking_failed_reply"]),
            meeting_booking=booking,
        )
    return None


def inbound_asks_for_available_hours(text: str | None) -> bool:
    """El prospecto pide horarios libres (no necesariamente el link)."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw))
    return bool(_HOURS_ASK_RE.search(folded))


_ALREADY_BOOKED_ACK_RE = re.compile(
    r"\b("
    r"ya\s+agend[eéó]|ya\s+reserv[eéó]|ya\s+lo\s+agend|"
    r"nos\s+vemos\s+(el|el\s+\w+|mañana|el\s+viernes)|"
    r"quedamos\s+(entonces|para|el|el\s+\w+)|"
    r"confirmad[oa]\s+(la\s+)?(reuni[oó]n|demo|cita)"
    r")\b",
    re.IGNORECASE,
)

_REFERS_PRIOR_SCHEDULE_RE = re.compile(
    r"("
    r"ya\s+te\s+(di(?:je|go)|hab[ií]a|habias)|"
    r"te\s+hab[ií]a\s+dich|"
    r"te\s+lo\s+di(?:je|go)|"
    r"como\s+te\s+(di(?:je|go)|coment)|"
    r"lo\s+que\s+te\s+di|"
    r"hab[ií]amos\s+qued|"
    r"quedamos\s+en|"
    r"mismo\s+(d[ií]a|horario|hora)|"
    r"misma\s+hora|"
    r"mantenemos|"
    r"d[ií]a\s+y\s+(la\s+)?duraci[oó]n|"
    r"el\s+d[ií]a\s+y|"
    r"duraci[oó]n\s+que\s+quer[ií]a|"
    r"horario\s+que\s+(te\s+)?(dije|pas[eé])|"
    r"ya\s+te\s+confirm"
    r")",
    re.IGNORECASE,
)


def inbound_refers_to_prior_schedule(text: str | None) -> bool:
    """El prospecto remite a día/hora/duración ya dichos en el hilo."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw))
    return bool(_REFERS_PRIOR_SCHEDULE_RE.search(folded))


def inbound_acknowledges_existing_booking(text: str | None) -> bool:
    """El prospecto confirma que ya reservó / quedó agendado (no pedir link ni horarios de nuevo)."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw))
    return bool(_ALREADY_BOOKED_ACK_RE.search(folded))


def build_proactive_availability_reply(
    *,
    prospect_name: str | None,
    alternatives: list[datetime],
    timezone: str,
    calendar_link: str | None = None,
    existing_reply: str | None = None,
) -> str:
    """Ofrece horarios libres O link de calendar — nunca ambos."""
    from app.services.meeting_booking import _first_name

    first = _first_name(prospect_name)
    link = (calendar_link or "").strip()
    # existing_reply se ignora a propósito: evita pegar "¿qué día?" + horarios/link.

    if alternatives:
        bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
        return (
            f"Genial {first}.\n\n"
            f"Estos horarios me quedan libres:\n{bullets}\n\n"
            f"¿Cuál preferís?"
        )
    if link:
        return (
            f"Genial {first}.\n\n"
            f"Podés elegir el horario que te quede bien acá:\n{link}\n\n"
            f"Cuando reserves, te llega la invitación automáticamente."
        )
    return (
        f"Genial {first}.\n\n"
        f"Decime qué día y franja te quedan bien (ej. mañana a las 15) "
        f"y te confirmo al toque."
    )


def resolve_inbound_scheduling_reply(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    reply_objective: str | None,
    sig: Any | None,
    suggested_reply: str = "",
    testing: bool = False,
) -> InboundTurnDecision:
    """
    Decide qué hacer con un inbound ya clasificado (agenda / horarios / link / normal).

    Orden:
    1) Autoresponder → skip
    2) Slot concreto → auto-book / alternativas / fallo Calendar
    3) Quiere agendar / pide horarios / pide link → horarios libres o calendar link
    4) Sino → reply normal (suggested)
    """
    from app.services.meeting_booking import (
        _first_name,
        _seller_google_calendar_ready,
        attempt_auto_book_from_message,
        attempt_duration_only_change,
        attempt_fulfill_from_conversation_context,
        build_calendar_link_reply,
        prepare_agendar_reply_with_calendar_link,
        prospect_has_pending_meeting,
        prospect_has_verified_calendar_meeting,
        usable_calendar_booking_link,
    )
    from app.services.meeting_slot_parser import inbound_is_duration_only_change

    body = (inbound_text or "").strip()
    if sig is not None:
        response_class, _ = ci.classify_commercial_response(body, sig)
        objective = (reply_objective or "").strip().lower() or ci.resolve_reply_objective(
            text=body,
            sig=sig,
            response_class=response_class,
        )
    else:
        response_class = "interesado" if ci.inbound_requests_meeting_or_demo(body) else "otro"
        objective = (reply_objective or "").strip().lower() or (
            "agendar" if ci.inbound_requests_meeting_or_demo(body) else "seguimiento"
        )

    if response_class == "respuesta_automatica":
        return InboundTurnDecision(
            action="skip_autoresponder",
            reply_objective=objective,
            response_class=response_class,
            skip_reason="respuesta automática / fuera de oficina",
            notes="No responder a autoresponders",
        )

    # Rechazo / no interesa: no intentar parsear slots ni agendar.
    if objective == "rechazo" or response_class in ("no_interesado", "contactar_mas_adelante"):
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective or "rechazo",
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Rechazo: cierre cortés sin agenda",
        )

    # Solo cambiar duración de reunión ya agendada (ej. 30 → 15): no re-ofrecer slots.
    if inbound_is_duration_only_change(body) is not None:
        duration_result = attempt_duration_only_change(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=body,
            testing=testing,
        ) or {}
        if duration_result.get("confirmation_reply"):
            changed = bool(
                duration_result.get("duration_change") or duration_result.get("already_same")
            )
            return InboundTurnDecision(
                action="booked" if changed else "normal_reply",
                reply_objective=objective or "agendar",
                response_class=response_class,
                reply_body=str(duration_result["confirmation_reply"]),
                meeting_booking=duration_result if changed else None,
                notes="Cambio de duración manteniendo día/hora",
            )
        if duration_result.get("alternatives_reply"):
            return InboundTurnDecision(
                action="alternatives",
                reply_objective=objective or "agendar",
                response_class=response_class,
                reply_body=str(duration_result["alternatives_reply"]),
                meeting_booking=duration_result,
                notes="Alargar duración: tramo extra ocupado",
            )
        fail = _booking_failure_decision(
            booking=duration_result,
            objective=objective or "agendar",
            response_class=response_class,
        )
        if fail is not None:
            return fail
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective,
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Intención de duración sin resultado de agenda",
        )

    meeting_booking = attempt_auto_book_from_message(
        db,
        campaign=campaign,
        prospect=prospect,
        inbound_text=body,
        reply_objective=objective,
        sig=sig,
        testing=testing,
    )
    if meeting_booking:
        if meeting_booking.get("confirmation_reply"):
            return InboundTurnDecision(
                action="booked",
                reply_objective=objective,
                response_class=response_class,
                reply_body=str(meeting_booking["confirmation_reply"]),
                meeting_booking=meeting_booking,
            )
        if meeting_booking.get("alternatives_reply"):
            return InboundTurnDecision(
                action="alternatives",
                reply_objective=objective,
                response_class=response_class,
                reply_body=str(meeting_booking["alternatives_reply"]),
                meeting_booking=meeting_booking,
            )
        fail = _booking_failure_decision(
            booking=meeting_booking,
            objective=objective,
            response_class=response_class,
        )
        if fail is not None:
            return fail

    refers_prior = inbound_refers_to_prior_schedule(body)
    # Sin slot en este mensaje: recuperar día/hora/duración del hilo o reunión activa.
    context_booking = None
    if db is not None and (
        refers_prior
        or objective == "agendar"
        or (
            sig is not None
            and (sig.prospect_wants_meeting or sig.explicit_meeting_commitment)
        )
    ):
        try:
            context_booking = attempt_fulfill_from_conversation_context(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_text=body,
                testing=testing,
            )
        except Exception:
            logger.exception(
                "fulfill_from_conversation_context failed prospect_id=%s",
                getattr(prospect, "id", None),
            )
            context_booking = None
        if context_booking:
            if context_booking.get("confirmation_reply"):
                return InboundTurnDecision(
                    action="booked",
                    reply_objective=objective or "agendar",
                    response_class=response_class,
                    reply_body=str(context_booking["confirmation_reply"]),
                    meeting_booking=context_booking,
                    notes="Cumplió agenda desde contexto del hilo / reunión activa",
                )
            if context_booking.get("alternatives_reply"):
                return InboundTurnDecision(
                    action="alternatives",
                    reply_objective=objective or "agendar",
                    response_class=response_class,
                    reply_body=str(context_booking["alternatives_reply"]),
                    meeting_booking=context_booking,
                )
            fail = _booking_failure_decision(
                booking=context_booking,
                objective=objective or "agendar",
                response_class=response_class,
            )
            if fail is not None:
                return fail

    if inbound_acknowledges_existing_booking(body) or refers_prior:
        verified = False
        try:
            verified = bool(prospect_has_verified_calendar_meeting(db, prospect.id))
        except Exception:
            verified = False
        if verified:
            first = _first_name(prospect.name)
            return InboundTurnDecision(
                action="booked",
                reply_objective=objective or "agendar",
                response_class=response_class,
                reply_body=(
                    f"Perfecto {first}, gracias por confirmar. "
                    f"Quedamos entonces; cualquier cosa me avisás por acá."
                ),
                notes="Prospecto confirma reserva con Meeting+Calendar verificados",
            )
            # Sin evento real: no fingir agendado; seguir al flujo de horarios/auto-book.

    wants_meeting = bool(
        objective == "agendar"
        or ci.inbound_requests_meeting_or_demo(body)
        or (sig is not None and (sig.prospect_wants_meeting or sig.explicit_meeting_commitment))
        or inbound_acknowledges_existing_booking(body)
        or refers_prior
    )
    asks_hours = inbound_asks_for_available_hours(body)
    asks_link = prospect_requests_calendar_link(body)
    cal_link = usable_calendar_booking_link(getattr(campaign, "calendar_link", None))

    if not (wants_meeting or asks_hours or asks_link):
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective,
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Sin intención de agenda detectada",
        )

    # Ya hay reunión: no reiniciar con horarios libres salvo que pidan horarios explícitos.
    if (
        db is not None
        and not asks_hours
        and not asks_link
        and (
            prospect_has_verified_calendar_meeting(db, prospect.id)
            or prospect_has_pending_meeting(db, prospect)
        )
    ):
        try:
            context_booking = attempt_fulfill_from_conversation_context(
                db,
                campaign=campaign,
                prospect=prospect,
                inbound_text=body,
                testing=testing,
            )
        except Exception:
            logger.exception(
                "fulfill existing meeting failed prospect_id=%s",
                getattr(prospect, "id", None),
            )
            context_booking = None
        if context_booking and context_booking.get("confirmation_reply"):
            return InboundTurnDecision(
                action="booked",
                reply_objective=objective or "agendar",
                response_class=response_class,
                reply_body=str(context_booking["confirmation_reply"]),
                meeting_booking=context_booking,
                notes="Reunión existente: reconfirmó en vez de ofrecer slots",
            )

    if refers_prior and not asks_hours:
        first = _first_name(prospect.name)
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective or "agendar",
            response_class=response_class,
            reply_body=(
                f"Perdón {first}, se me escapó.\n\n"
                "Confirmame de nuevo día, hora y duración (ej. martes 10:00, 15 min) "
                "y lo dejo agendado al toque."
            ),
            notes="Refería al hilo pero no había slot/reunión recuperable",
        )

    live_calendar = _seller_google_calendar_ready(db, campaign)
    # Horarios libres: si los pide, o quiere agendar sin contexto previo recuperable.
    prefer_hours = bool(live_calendar and asks_hours)
    if live_calendar and wants_meeting and not asks_link and not asks_hours:
        prefer_hours = True

    if prefer_hours:
        slots = _fetch_proactive_slots(db, campaign=campaign)
        if slots:
            reply = build_proactive_availability_reply(
                prospect_name=prospect.name,
                alternatives=slots,
                timezone=(getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires"),
                calendar_link=None,
                existing_reply=None,
            )
            return InboundTurnDecision(
                action="offer_hours",
                reply_objective=objective or "agendar",
                response_class=response_class,
                reply_body=reply,
                offered_slots=[s.isoformat() for s in slots],
                notes="Horarios libres desde Google Calendar freeBusy (sin link)",
            )

    if cal_link and (objective == "agendar" or wants_meeting or asks_link):
        reply = build_calendar_link_reply(
            prospect_name=prospect.name,
            calendar_link=cal_link,
            existing_reply=None,
        )
        return InboundTurnDecision(
            action="calendar_link",
            reply_objective=objective or "agendar",
            response_class=response_class,
            reply_body=reply,
            notes="Solo link de calendar usable (sin pedir franja aparte)",
        )

    if cal_link:
        reply = prepare_agendar_reply_with_calendar_link(
            prospect=prospect,
            campaign=campaign,
            reply_objective=objective,
            suggested_reply="",
        )
        if reply:
            return InboundTurnDecision(
                action="calendar_link",
                reply_objective=objective,
                response_class=response_class,
                reply_body=reply,
            )

    if wants_meeting or asks_hours:
        reply = build_proactive_availability_reply(
            prospect_name=prospect.name,
            alternatives=[],
            timezone=(getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires"),
            calendar_link=None,
            existing_reply=None,
        )
        return InboundTurnDecision(
            action="offer_hours",
            reply_objective=objective or "agendar",
            response_class=response_class,
            reply_body=reply,
            notes="Sin freeBusy ni link usable: pedir franja al prospecto",
        )

    return InboundTurnDecision(
        action="normal_reply",
        reply_objective=objective,
        response_class=response_class,
        reply_body=suggested_reply or None,
    )


def _fetch_proactive_slots(db: Session, *, campaign: Campaign) -> list[datetime]:
    from app.services.google_calendar_availability import find_available_slots
    from app.services.meeting_booking import resolve_campaign_calendar_user_id

    uid = resolve_campaign_calendar_user_id(db, campaign)
    if not uid:
        return []
    tz_name = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    around = datetime.now(ZoneInfo(tz_name)) + timedelta(hours=24)
    try:
        return find_available_slots(
            db,
            company_id=int(campaign.company_id),
            seller_user_id=int(uid),
            around=around.astimezone(UTC),
            duration_minutes=30,
            count=3,
            timezone=tz_name,
            available_hours=getattr(campaign, "available_hours", None),
            same_day_only=False,
        )
    except Exception as exc:
        logger.warning(
            "proactive_slots_failed campaign_id=%s err=%s",
            campaign.id,
            str(exc)[:200],
        )
        return []
