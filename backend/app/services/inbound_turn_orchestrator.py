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
from app.services.commercial_conversation_state import apply_stated_email, persistable_stated_email
from app.services.google_calendar_availability import format_slot_local

logger = logging.getLogger(__name__)

InboundAction = Literal[
    "skip_autoresponder",
    "booked",
    "alternatives",
    "booking_failed",
    "offer_hours",
    "calendar_link",
    "ask_email",
    "ask_slot",
    "canceled",
    "referral",
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
        ack = (booking.get("pending_ack_reply") or "").strip()
        return InboundTurnDecision(
            action="booking_failed",
            reply_objective=objective,
            response_class=response_class,
            reply_body=ack or None,
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


def _ask_email_decision(
    booking: dict[str, Any], *, objective: str, response_class: str
) -> InboundTurnDecision | None:
    if not booking.get("ask_email_reply"):
        return None
    return InboundTurnDecision(
        action="ask_email",
        reply_objective=objective or "agendar",
        response_class=response_class,
        reply_body=str(booking["ask_email_reply"]),
        meeting_booking=booking,
        notes="Horario OK: falta mail del prospecto (o que diga que no lo pasa) para crear Meet",
    )


def _booking_decision(
    booking: dict[str, Any] | None, *, objective: str, response_class: str, notes: str = ""
) -> InboundTurnDecision | None:
    if not booking:
        return None
    ask = _ask_email_decision(booking, objective=objective, response_class=response_class)
    if ask is not None:
        return ask
    if booking.get("confirmation_reply"):
        return InboundTurnDecision(
            action="booked",
            reply_objective=objective or "agendar",
            response_class=response_class,
            reply_body=str(booking["confirmation_reply"]),
            meeting_booking=booking,
            notes=notes,
        )
    if booking.get("alternatives_reply"):
        return InboundTurnDecision(
            action="alternatives",
            reply_objective=objective or "agendar",
            response_class=response_class,
            reply_body=str(booking["alternatives_reply"]),
            meeting_booking=booking,
            notes=notes,
        )
    return _booking_failure_decision(
        booking=booking, objective=objective or "agendar", response_class=response_class
    )


def _slots_on_other_days(
    db: Session,
    *,
    campaign: Campaign,
    calendar_user_id: int,
    skip: datetime | None,
    tz: str,
    count: int = 3,
) -> list[datetime]:
    """Un horario libre por día, sin el día que el prospecto dijo que no puede."""
    from zoneinfo import ZoneInfo

    from app.services.google_calendar_availability import find_available_slots

    zone = ZoneInfo(tz)
    skip_date = None
    if skip is not None:
        skip_date = (skip if skip.tzinfo else skip.replace(tzinfo=UTC)).astimezone(zone).date()
    try:
        slots = find_available_slots(
            db,
            company_id=int(campaign.company_id),
            seller_user_id=int(calendar_user_id),
            around=datetime.now(UTC) + timedelta(hours=24),
            duration_minutes=30,
            count=60,
            timezone=tz,
            available_hours=getattr(campaign, "available_hours", None),
            same_day_only=False,
        )
    except Exception as exc:
        logger.warning("slots_on_other_days_failed campaign_id=%s err=%s", campaign.id, str(exc)[:200])
        return []
    out: list[datetime] = []
    seen: set = set()
    for slot in sorted(slots):
        day = slot.astimezone(zone).date()
        if day == skip_date or day in seen:
            continue
        seen.add(day)
        out.append(slot)
        if len(out) >= count:
            break
    return out


def _scheduling_extras_turn(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    body: str,
    testing: bool,
) -> InboundTurnDecision | None:
    """Mail para la invitación, cancelar, o elegir uno de los horarios ofrecidos."""
    from app.services import meeting_scheduling_extras as extras
    from app.services.meeting_booking import (
        _active_meeting_row,
        _book_with_calendar_availability,
        _cancel_superseded_meetings,
        _seller_google_calendar_ready,
        find_real_alternative_slots,
        resolve_campaign_calendar_user_id,
    )
    from app.services.meeting_slot_parser import parse_meeting_slot

    if db is None:
        return None
    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    cal_link = (getattr(campaign, "calendar_link", None) or "").strip() or None
    meeting = _active_meeting_row(db, prospect)

    email = persistable_stated_email(extras.extract_email(body), prospect)
    email_before = (getattr(prospect, "email", None) or "").strip().lower()
    if email:
        extras.mark_chat_email_confirmed(prospect)

    if meeting is not None and getattr(meeting, "id", None):
        from app.services import meeting_confirmation as mconf

        if mconf.confirm_ping_pending(prospect, meeting.id) and mconf.inbound_confirms_meeting(body):
            mconf.finish_sequence_for_meeting(db, prospect, campaign, meeting, reason="confirmó la reunión")
            return InboundTurnDecision(
                action="booked",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=mconf.build_confirmed_reply(prospect, meeting, campaign),
                notes="Confirmó la reunión tras el aviso previo: secuencia terminada",
            )

    from app.services.commercial_conversation_state import try_advance_meeting_booking

    advanced = try_advance_meeting_booking(
        db, campaign=campaign, prospect=prospect, inbound_text=body, testing=testing
    )
    if advanced is not None:
        return advanced

    waiting = extras.pending_slot(prospect)
    if waiting is not None:
        from app.services.meeting_slot_parser import parse_meeting_slots
        from app.services.whatsapp_reply_compose import inbound_is_product_aside

        if inbound_is_product_aside(body):
            return None
        proposed_again = parse_meeting_slots(body, timezone=tz)
        if proposed_again:
            # Corrigió el horario: actualizar slot. El mail YA se pidió — no repetir la pregunta.
            new_slot = proposed_again[0]
            duration = int(waiting.get("duration") or 30)
            is_reschedule = bool(waiting.get("is_reschedule"))
            booking = _book_with_calendar_availability(
                db,
                campaign=campaign,
                prospect=prospect,
                slot=new_slot,
                tz=tz,
                cal_link=cal_link,
                testing=testing,
                is_reschedule=is_reschedule,
                duration_minutes=duration,
            )
            if booking.get("ask_email_reply") or booking.get("needs_email"):
                known = (getattr(prospect, "email", None) or "").strip() or None
                return InboundTurnDecision(
                    action="ask_email",
                    reply_objective="agendar",
                    response_class="interesado",
                    reply_body=extras.build_ask_email_reply(
                        prospect_name=prospect.name,
                        slot=new_slot,
                        timezone=tz,
                        known_email=known,
                        already_asked=True,
                    ),
                    meeting_booking=booking,
                    notes="Actualizó horario; mail ya pedido — sin repetir la pregunta",
                )
            return _booking_decision(
                booking,
                objective="agendar",
                response_class="interesado",
                notes="Actualizó horario con pending de mail",
            )
        if extras.offered_slots(prospect) and extras.pick_offered_slot(
            body, extras.offered_slots(prospect), timezone=tz
        ) is not None:
            extras.clear_pending_slot(prospect)
            waiting = None
    if waiting is not None:
        invite_to = email
        if not invite_to and extras.inbound_confirms_proposed_email(prospect, body):
            invite_to = extras.proposed_invite_email(prospect) or (prospect.email or "").strip().lower() or None
        elif not invite_to and extras.inbound_confirms_email(body) and not extras.chat_channel_active():
            invite_to = (prospect.email or "").strip().lower() or None
        invite_to = persistable_stated_email(invite_to, prospect)
        if invite_to:
            apply_stated_email(prospect, invite_to)
            extras.mark_chat_email_confirmed(prospect)
            extras.clear_pending_slot(prospect)
            extras.clear_proposed_invite_email(prospect)
            booking = _book_with_calendar_availability(
                db,
                campaign=campaign,
                prospect=prospect,
                slot=waiting["at"],
                tz=tz,
                cal_link=cal_link,
                testing=testing,
                is_reschedule=waiting["is_reschedule"],
                duration_minutes=waiting["duration"],
                invite_just_given=True,
            )
            return _booking_decision(
                booking,
                objective="agendar",
                response_class="interesado",
                notes="Reunión creada con el mail que pasó/confirmó el prospecto",
            )
        if extras.inbound_refuses_email(body):
            return InboundTurnDecision(
                action="ask_email",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=extras.build_mail_required_reply(
                    prospect_name=prospect.name,
                    slot=waiting["at"],
                    timezone=tz,
                ),
                notes="Sin mail no se crea reunión en Calendar",
            )
        # Ya pedimos el mail: no re-enviar la misma pregunta en cada ack vago.
        return InboundTurnDecision(
            action="skip_autoresponder",
            reply_objective="agendar",
            response_class="interesado",
            reply_body=None,
            skip_reason="waiting_for_email",
            notes="Horario pendiente de mail: sin re-pedir el mail",
        )

    pending_id = extras.invite_pending_meeting_id(prospect)
    pending_invite = (
        pending_id is not None
        and meeting is not None
        and meeting.google_calendar_event_id
        and pending_id == getattr(meeting, "id", None)
    )
    if pending_invite:
        invite_to = email
        if not invite_to and extras.inbound_confirms_proposed_email(prospect, body):
            invite_to = extras.proposed_invite_email(prospect) or (prospect.email or "").strip().lower() or None
        elif not invite_to and extras.inbound_confirms_email(body) and not extras.chat_channel_active():
            invite_to = (prospect.email or "").strip().lower() or None
        invite_to = persistable_stated_email(invite_to, prospect)
        if invite_to:
            apply_stated_email(prospect, invite_to)
            uid = resolve_campaign_calendar_user_id(db, campaign)
            if uid and not testing:
                try:
                    from app.services.google_calendar_create import update_calendar_event_attendee

                    update_calendar_event_attendee(
                        db,
                        company_id=int(campaign.company_id),
                        seller_user_id=int(uid),
                        event_id=str(meeting.google_calendar_event_id),
                        attendee_email=invite_to,
                    )
                except Exception as exc:
                    logger.warning(
                        "invite_attendee_failed prospect_id=%s err=%s",
                        getattr(prospect, "id", None),
                        str(exc)[:200],
                    )
                    return None
            extras.clear_invite_pending(prospect)
            return InboundTurnDecision(
                action="booked",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=extras.build_email_updated_reply(
                    prospect_name=prospect.name, email=invite_to, resent=False
                ),
                notes="Invitación enviada al mail que pasó/confirmó el prospecto",
            )

    if email:
        current = email_before
        apply_stated_email(prospect, email)

        slot_ctx = extras.reserved_slot_for_email_booking(prospect)
        has_calendar_meeting = (
            meeting is not None and bool(getattr(meeting, "google_calendar_event_id", None))
        )
        if slot_ctx is not None and not has_calendar_meeting:
            extras.clear_pending_slot(prospect)
            extras.clear_proposed_invite_email(prospect)
            booking = _book_with_calendar_availability(
                db,
                campaign=campaign,
                prospect=prospect,
                slot=slot_ctx["at"],
                tz=tz,
                cal_link=cal_link,
                testing=testing,
                is_reschedule=slot_ctx["is_reschedule"],
                duration_minutes=slot_ctx["duration"],
                invite_just_given=True,
            )
            decision = _booking_decision(
                booking,
                objective="agendar",
                response_class="interesado",
                notes="Mail recibido: reunión creada en Calendar con invitado",
            )
            if decision is not None:
                return decision

        if not has_calendar_meeting and slot_ctx is None:
            from app.services.commercial_conversation_state import build_ask_time_only_reply

            return InboundTurnDecision(
                action="ask_slot",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=build_ask_time_only_reply(prospect_name=prospect.name, email=email),
                notes="Mail recibido; falta horario acordado",
            )

        if meeting is not None and meeting.google_calendar_event_id and email != email_before:
            uid = resolve_campaign_calendar_user_id(db, campaign)
            if uid and not testing:
                try:
                    from app.services.google_calendar_create import update_calendar_event_attendee

                    update_calendar_event_attendee(
                        db,
                        company_id=int(campaign.company_id),
                        seller_user_id=int(uid),
                        event_id=str(meeting.google_calendar_event_id),
                        attendee_email=email,
                    )
                except Exception as exc:
                    logger.warning(
                        "update_attendee_failed prospect_id=%s err=%s",
                        getattr(prospect, "id", None),
                        str(exc)[:200],
                    )
                    return None
            return InboundTurnDecision(
                action="booked",
                reply_objective="agendar",
                response_class="interesado",
                reply_body=extras.build_email_updated_reply(
                    prospect_name=prospect.name, email=email, resent=bool(current)
                ),
                notes="Invitación reenviada al mail que pasó el prospecto",
            )

    if meeting is not None and extras.inbound_cancels_meeting(body):
        if parse_meeting_slot(body, timezone=tz, context_meeting_at=None) is None:
            reschedule = extras.inbound_asks_reschedule(body)
            skip_day = meeting.scheduled_for
            uid = resolve_campaign_calendar_user_id(db, campaign) or campaign.seller_id
            _cancel_superseded_meetings(
                db,
                campaign=campaign,
                prospect=prospect,
                keep_slot=None,
                seller_id=int(uid) if uid else None,
            )
            from app.models.enums import PipelineStage, ProspectStatus

            prospect.status = ProspectStatus.interested.value
            prospect.pipeline_stage = PipelineStage.interesado.value
            alts: list[datetime] = []
            if uid and reschedule:
                alts = _slots_on_other_days(
                    db, campaign=campaign, calendar_user_id=int(uid), skip=skip_day, tz=tz
                )
            elif uid:
                alts = find_real_alternative_slots(
                    db,
                    campaign=campaign,
                    calendar_user_id=int(uid),
                    around=datetime.now(UTC) + timedelta(hours=24),
                )
            if alts:
                extras.remember_offered_slots(prospect, alts)
            return InboundTurnDecision(
                action="canceled",
                reply_objective="agendar",
                response_class="otro",
                reply_body=extras.build_canceled_reply(
                    prospect_name=prospect.name, alternatives=alts, timezone=tz, reschedule=reschedule
                ),
                offered_slots=[a.isoformat() for a in alts],
                notes="Reunión cancelada a pedido del prospecto",
            )

    offered = extras.offered_slots(prospect)
    if not offered:
        offered = extras.recover_offered_slots_from_thread(db, prospect, timezone=tz)
    picked = extras.pick_offered_slot(body, offered, timezone=tz)
    if picked is not None and _seller_google_calendar_ready(db, campaign):
        from app.services.commercial_conversation_state import prospect_typed_chat_email

        typed = prospect_typed_chat_email(prospect)
        if typed:
            apply_stated_email(prospect, typed)
        booking = _book_with_calendar_availability(
            db,
            campaign=campaign,
            prospect=prospect,
            slot=picked,
            tz=tz,
            cal_link=cal_link,
            testing=testing,
            is_reschedule=meeting is not None,
            invite_just_given=bool(typed or email),
        )
        return _booking_decision(
            booking,
            objective="agendar",
            response_class="interesado",
            notes="Eligió uno de los horarios ofrecidos",
        )
    return None


def _referral_turn(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    body: str,
    sig: Any | None,
) -> InboundTurnDecision | None:
    """«Hablá con X»: pedir el contacto si falta; si está, crear a X en la campaña para escribirle."""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import prospect_referral as ref

    if db is None:
        return None
    if sig is not None:
        is_referral = ci.classify_commercial_response(body, sig)[0] == "derivar_a_otra_persona"
    else:
        is_referral = ci._referral_probe(ci.normalize_inbound_text_for_classification(body))
    pending = ref.pending_referral(prospect)
    if not is_referral and pending is None:
        return None

    exclude = {(prospect.email or "").lower()}
    exclude |= {
        (e or "").lower()
        for e in db.scalars(select(User.email).where(User.company_id == campaign.company_id)).all()
    }
    info = ref.extract_referral(body, exclude_emails=exclude)
    if pending:
        info = {**pending, **info}
    if not is_referral and not ref.has_contact(info):
        return None

    ref.stop_referrer_sequence(db, prospect)
    if ref.has_contact(info):
        row, reason = ref.create_referred_prospect(db, campaign=campaign, referrer=prospect, info=info)
        ref.log_referral(campaign, referrer=prospect, referred=row, info=info, reason=reason)
        if row is not None or reason == "duplicate":
            reply = ref.build_will_write_reply(
                referrer_name=prospect.name, referred_name=(row.name if row else info.get("name"))
            )
            notes = (
                f"Derivado creado en la campaña (prospecto #{row.id})"
                if row is not None
                else "El derivado ya estaba en Nexus: no se duplica"
            )
        else:
            first = (prospect.name or "").split()[0] if prospect.name else "ahí"
            reply = f"¡Gracias {first}! Le paso el contacto al equipo para que le escriba."
            notes = f"No se pudo crear el derivado: {reason[:160]}"
        return InboundTurnDecision(
            action="referral",
            reply_objective="referir",
            response_class="derivar_a_otra_persona",
            reply_body=reply,
            notes=notes,
        )

    ref.remember_pending(prospect, info)
    if info.get("name") or info.get("role"):
        reply = ref.build_ask_contact_reply(referrer_name=prospect.name, referred_name=info.get("name"))
    else:
        reply = ref.build_ask_who_reply(referrer_name=prospect.name)
    return InboundTurnDecision(
        action="referral",
        reply_objective="referir",
        response_class="derivar_a_otra_persona",
        reply_body=reply,
        notes="Derivación: se pidió el contacto de la persona indicada",
    )


def _remember_offered(prospect: Prospect, decision: InboundTurnDecision) -> None:
    from app.services import meeting_scheduling_extras as extras

    slots = list(decision.offered_slots or [])
    if not slots and decision.meeting_booking:
        slots = list(decision.meeting_booking.get("alternative_slots") or [])
    if slots and decision.action in ("offer_hours", "alternatives", "canceled"):
        extras.clear_pending_slot(prospect)
        extras.clear_proposed_invite_email(prospect)
        extras.remember_offered_slots(prospect, slots)


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
    channel: str = "email",
) -> InboundTurnDecision:
    from app.services.meeting_scheduling_extras import booking_channel, sdr_country

    country = ""
    try:
        from app.services.user_outreach_style import style_record_for_user

        st = style_record_for_user(db, getattr(campaign, "seller_id", None))
        country = (getattr(st, "country", None) or "").strip() if st is not None else ""
    except Exception:
        country = ""
    with booking_channel(channel), sdr_country(country):
        return _resolve_inbound_scheduling_reply(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=inbound_text,
            reply_objective=reply_objective,
            sig=sig,
            suggested_reply=suggested_reply,
            testing=testing,
        )


def _resolve_inbound_scheduling_reply(
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
    from app.services.meeting_scheduling_extras import chat_closed_after_booking

    # Post-agenda: ya se mandó el cierre (invite + gracias). No seguir el chat.
    if chat_closed_after_booking(prospect):
        return InboundTurnDecision(
            action="skip_autoresponder",
            reply_objective=(reply_objective or "agendar"),
            response_class="interesado",
            skip_reason="reunión ya agendada",
            notes="Chat cerrado tras invite: no auto-contestar más",
        )

    body = (inbound_text or "").strip()
    decision = None
    if body and not (sig is not None and ci.classify_commercial_response(body, sig)[0] == "respuesta_automatica"):
        try:
            decision = _referral_turn(db, campaign=campaign, prospect=prospect, body=body, sig=sig)
        except Exception:
            logger.exception("referral_turn failed prospect_id=%s", getattr(prospect, "id", None))
            decision = None
        if decision is None:
            try:
                decision = _scheduling_extras_turn(
                    db, campaign=campaign, prospect=prospect, body=body, testing=testing
                )
            except Exception:
                logger.exception(
                    "scheduling_extras_turn failed prospect_id=%s", getattr(prospect, "id", None)
                )
                decision = None
    if decision is None:
        decision = _resolve_scheduling_core(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_text=inbound_text,
            reply_objective=reply_objective,
            sig=sig,
            suggested_reply=suggested_reply,
            testing=testing,
        )
    _remember_offered(prospect, decision)
    return decision


def inbound_asks_for_available_hours(text: str | None) -> bool:
    """El prospecto pide horarios libres (no necesariamente el link)."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw))
    return bool(_HOURS_ASK_RE.search(folded))


def prefer_explicit_day_hours_over_stale_alternatives(
    text: str | None,
    *,
    timezone: str,
) -> bool:
    """
    True si pide horarios de un día concreto (o rechaza uno y pide otro).
    En ese caso no re-pegar alternativas del día viejo (ej. lunes tras «el lunes no»).
    También True si propone día+hora concretos («martes a las 10 esta bien»).
    """
    raw = (text or "").strip()
    if not raw:
        return False
    from app.services.meeting_slot_parser import (
        inbound_has_explicit_day_anchor,
        parse_meeting_day_preference,
    )

    day_pref = parse_meeting_day_preference(raw, timezone=timezone)
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw))
    if day_pref is None:
        # day_pref es None si hay clock time; igual no hay que re-pegar el día viejo.
        if inbound_has_explicit_day_anchor(raw) and re.search(
            r"\ba\s+las\b|\b\d{1,2}\s*(?:[:.]\d{2})?\s*(?:hs|hrs|h|am|pm)\b",
            folded,
            re.I,
        ):
            return True
        return False
    if inbound_asks_for_available_hours(raw):
        return True
    # «el lunes no … el martes…» sin la palabra «horarios».
    if re.search(
        r"\b(?:lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo)\s+no\b",
        folded,
        re.I,
    ):
        return True
    return False


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
    r"ya\s+te\s+lo\s+pas[eé]|ya\s+te\s+lo\s+di(?:je|go)|"
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


# Reloj 0–23 (no “24hs” / SLA). “hora/horas” sueltas mataban “onboarding en 24 horas”.
_SCHEDULING_WORDS_RE = re.compile(
    r"\b(?:reunion\w*|llamada\w*|call|meet|zoom|videollamada|agend\w*|agenda|horario\w*|"
    r"a\s+que\s+hora|que\s+hora|"
    r"dia|dias|fecha|lunes|martes|miercoles|jueves|viernes|sabado|domingo|manana|hoy|"
    r"invitacion|calendario|confirm\w*|quedamos|nos\s+vemos)\b"
    r"|\b(?:[01]?\d|2[0-3])(?::\d{2})?\s*(?:hs?|am|pm)\b"
    r"|\ba\s+las?\s+(?:[01]?\d|2[0-3])\b"
)

# “¿Hacen onboarding 24hs?” ≠ elegir horario.
_PRODUCT_CAPABILITY_Q_RE = re.compile(
    r"\b(?:hacen|tienen|soportan|incluye|incluyen|ofrecen|onboarding|implement\w*|"
    r"setup|despliegue|capacitaci\w*|sla|api|integraci)\b",
    re.I,
)
_CLEAR_MEETING_INTENT_RE = re.compile(
    r"\b(?:reunion\w*|llamada\w*|call|meet|zoom|agend\w*|quedamos|nos\s+vemos|"
    r"lunes|martes|miercoles|jueves|viernes|invitacion|calendario)\b",
    re.I,
)


def inbound_text_supports_scheduling_turn(text: str | None) -> bool:
    """
    El mensaje actual justifica ofrecer horarios o pedir franja.
    No basta interés del clasificador/hilo si el inbound es solo números (tel/CUIT/15 suelto).
    """
    from app.services.meeting_slot_parser import (
        inbound_lacks_scheduling_content,
        strip_email_reply_quotes,
    )

    raw = strip_email_reply_quotes(text or "").strip()
    if not raw or inbound_lacks_scheduling_content(raw):
        return False
    return bool(
        inbound_mentions_scheduling(raw)
        or ci.inbound_has_explicit_meeting_slot(raw)
        or ci.inbound_requests_meeting_or_demo(raw)
    )


def inbound_mentions_scheduling(text: str | None) -> bool:
    """El mensaje habla de la reunión, días u horarios (no es solo una pregunta sobre el producto)."""
    raw = (text or "").strip()
    if not raw:
        return False
    folded = ci.fold_accents(ci.normalize_inbound_text_for_classification(raw).lower())
    # Capacidad/producto con “?” (ej. onboarding 24hs) no es slot de agenda.
    if ("?" in raw or "¿" in raw) and _PRODUCT_CAPABILITY_Q_RE.search(folded):
        if not _CLEAR_MEETING_INTENT_RE.search(folded):
            return False
    # «Explícame más antes de la reunión» = info de producto, no pedir día/hora.
    if re.search(
        r"\b(antes|previo|previa)\b.{0,40}\b(reunion|llamada|call|demo|charla)\b",
        folded,
    ):
        try:
            from app.services.whatsapp_reply_compose import (
                inbound_is_info_question,
                inbound_is_open_ended_more_info,
            )

            if inbound_is_open_ended_more_info(raw) or inbound_is_info_question(raw):
                return False
        except Exception:
            pass
    return bool(_SCHEDULING_WORDS_RE.search(folded))


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
    preference_label: str | None = None,
) -> str:
    """Ofrece horarios libres O link de calendar — nunca ambos."""
    from app.services.meeting_booking import _first_name

    first = _first_name(prospect_name)
    link = (calendar_link or "").strip()
    # existing_reply se ignora a propósito: evita pegar "¿qué día?" + horarios/link.

    if alternatives:
        bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
        pref = (preference_label or "").strip()
        if pref:
            intro = f"Para {pref} me quedan libres estos horarios:"
        else:
            intro = "Estos horarios me quedan libres:"
        return (
            f"Genial {first}.\n\n"
            f"{intro}\n{bullets}\n\n"
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


def _resolve_scheduling_core(
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

    from app.services.whatsapp_reply_compose import inbound_is_product_aside

    # Rechazo / no interesa: no intentar parsear slots ni agendar.
    if objective == "rechazo" or response_class in ("no_interesado", "contactar_mas_adelante"):
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective or "rechazo",
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Rechazo: cierre cortés sin agenda",
        )

    # Solo cambiar duración de reunión ya agendada (ej. 30 → 15): antes que product-aside
    # (extract_competitor a veces confunde "30 a 15min" con un tool).
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

    if inbound_is_product_aside(body):
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective="informar",
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Pregunta de producto; no re-pedir mail ni slot",
        )

    tz_name_early = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    skip_stale_day_alts = prefer_explicit_day_hours_over_stale_alternatives(
        body, timezone=tz_name_early
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
        ask = _ask_email_decision(meeting_booking, objective=objective, response_class=response_class)
        if ask is not None:
            return ask
        if meeting_booking.get("confirmation_reply"):
            return InboundTurnDecision(
                action="booked",
                reply_objective=objective,
                response_class=response_class,
                reply_body=str(meeting_booking["confirmation_reply"]),
                meeting_booking=meeting_booking,
            )
        if meeting_booking.get("alternatives_reply") and not skip_stale_day_alts:
            return InboundTurnDecision(
                action="alternatives",
                reply_objective=objective,
                response_class=response_class,
                reply_body=str(meeting_booking["alternatives_reply"]),
                meeting_booking=meeting_booking,
            )
        if not (meeting_booking.get("alternatives_reply") and skip_stale_day_alts):
            fail = _booking_failure_decision(
                booking=meeting_booking,
                objective=objective,
                response_class=response_class,
            )
            if fail is not None:
                return fail

    refers_prior = inbound_refers_to_prior_schedule(body)
    if (
        db is not None
        and not refers_prior
        and not inbound_acknowledges_existing_booking(body)
        and not inbound_mentions_scheduling(body)
        and (
            prospect_has_verified_calendar_meeting(db, prospect.id)
            or prospect_has_pending_meeting(db, prospect)
        )
    ):
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective,
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Ya hay reunión y el mensaje no habla de agenda",
        )
    # Sin slot en este mensaje: recuperar día/hora/duración del hilo o reunión activa.
    context_booking = None
    if db is not None and inbound_mentions_scheduling(body) and (
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
            ask = _ask_email_decision(context_booking, objective=objective, response_class=response_class)
            if ask is not None:
                return ask
            if context_booking.get("confirmation_reply"):
                return InboundTurnDecision(
                    action="booked",
                    reply_objective=objective or "agendar",
                    response_class=response_class,
                    reply_body=str(context_booking["confirmation_reply"]),
                    meeting_booking=context_booking,
                    notes="Cumplió agenda desde contexto del hilo / reunión activa",
                )
            if context_booking.get("alternatives_reply") and not skip_stale_day_alts:
                return InboundTurnDecision(
                    action="alternatives",
                    reply_objective=objective or "agendar",
                    response_class=response_class,
                    reply_body=str(context_booking["alternatives_reply"]),
                    meeting_booking=context_booking,
                )
            if not (context_booking.get("alternatives_reply") and skip_stale_day_alts):
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

    from app.services.meeting_slot_parser import parse_meeting_day_preference

    tz_name = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    day_pref = parse_meeting_day_preference(body, timezone=tz_name)
    scheduling_in_body = inbound_text_supports_scheduling_turn(body)

    wants_meeting = bool(
        objective == "agendar"
        or ci.inbound_requests_meeting_or_demo(body)
        or (sig is not None and (sig.prospect_wants_meeting or sig.explicit_meeting_commitment))
        or inbound_acknowledges_existing_booking(body)
        or refers_prior
        or day_pref is not None
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
        if context_booking:
            ask = _ask_email_decision(context_booking, objective=objective, response_class=response_class)
            if ask is not None:
                return ask
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

    # Día/franja sin hora («¿jueves?», «viernes a la tarde») → huecos reales ese día.
    if live_calendar and day_pref is not None and not asks_link:
        slots = _fetch_slots_for_day_preference(db, campaign=campaign, preference=day_pref)
        if slots:
            reply = build_proactive_availability_reply(
                prospect_name=prospect.name,
                alternatives=slots,
                timezone=tz_name,
                calendar_link=None,
                existing_reply=None,
                preference_label=day_pref.label or None,
            )
            return InboundTurnDecision(
                action="offer_hours",
                reply_objective=objective or "agendar",
                response_class=response_class,
                reply_body=reply,
                offered_slots=[s.isoformat() for s in slots],
                notes=f"Horarios libres para preferencia «{day_pref.label or 'día'}» (freeBusy)",
            )

    # Horarios libres: si los pide, o quiere agendar con evidencia en este mensaje.
    prefer_hours = bool(live_calendar and asks_hours)
    if live_calendar and wants_meeting and not asks_link and not asks_hours and scheduling_in_body:
        prefer_hours = True

    if prefer_hours:
        slots = _fetch_proactive_slots(db, campaign=campaign)
        if slots:
            reply = build_proactive_availability_reply(
                prospect_name=prospect.name,
                alternatives=slots,
                timezone=tz_name,
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

    if cal_link and (asks_link or scheduling_in_body):
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

    if asks_hours or (wants_meeting and scheduling_in_body):
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

    if wants_meeting and not scheduling_in_body:
        return InboundTurnDecision(
            action="normal_reply",
            reply_objective=objective if objective != "agendar" else "seguimiento",
            response_class=response_class,
            reply_body=suggested_reply or None,
            notes="Interés de agenda en hilo/clasificador; inbound sin texto de coordinación",
        )

    return InboundTurnDecision(
        action="normal_reply",
        reply_objective=objective,
        response_class=response_class,
        reply_body=suggested_reply or None,
    )


def _fetch_proactive_slots(db: Session, *, campaign: Campaign) -> list[datetime]:
    from app.services.meeting_booking import resolve_campaign_calendar_user_id

    uid = resolve_campaign_calendar_user_id(db, campaign)
    if not uid:
        return []
    tz_name = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    return _slots_on_other_days(db, campaign=campaign, calendar_user_id=int(uid), skip=None, tz=tz_name)


def _filter_slots_by_window(
    slots: list[datetime],
    *,
    window: str,
    timezone: str,
) -> list[datetime]:
    if window in ("", "any", None):
        return list(slots)
    tz = ZoneInfo(timezone)
    out: list[datetime] = []
    for slot in slots:
        hour = slot.astimezone(tz).hour
        if window == "morning" and hour < 13:
            out.append(slot)
        elif window == "afternoon" and hour >= 13:
            out.append(slot)
    return out


def _fetch_slots_for_day_preference(
    db: Session,
    *,
    campaign: Campaign,
    preference: Any,
    count: int = 3,
) -> list[datetime]:
    """Huecos reales para un día/franja pedido sin hora exacta."""
    from app.services.meeting_booking import find_real_alternative_slots, resolve_campaign_calendar_user_id
    from app.services.google_calendar_availability import find_available_slots

    try:
        uid = resolve_campaign_calendar_user_id(db, campaign) if db is not None else None
    except Exception:
        uid = None
    if not uid:
        return []
    tz_name = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    window = getattr(preference, "window", "any") or "any"
    around = getattr(preference, "around", None)
    if around is None:
        return []
    same_day_only = bool(getattr(preference, "same_day_only", True))

    try:
        raw = find_available_slots(
            db,
            company_id=int(campaign.company_id),
            seller_user_id=int(uid),
            around=around,
            duration_minutes=30,
            count=max(count * 4, 12),
            timezone=tz_name,
            available_hours=getattr(campaign, "available_hours", None),
            same_day_only=same_day_only,
        )
    except Exception as exc:
        logger.warning(
            "day_preference_slots_failed campaign_id=%s err=%s",
            getattr(campaign, "id", None),
            str(exc)[:200],
        )
        raw = []

    filtered = _filter_slots_by_window(raw, window=window, timezone=tz_name)
    if filtered:
        return filtered[:count]
    if same_day_only and raw:
        # Ese día tiene huecos fuera de la franja: ofrecerlos igual.
        return raw[:count]
    if same_day_only:
        # Día pedido lleno → alternativas reales otros días (misma franja si se puede).
        try:
            alts = find_real_alternative_slots(
                db,
                campaign=campaign,
                calendar_user_id=int(uid),
                around=around,
                duration_minutes=30,
                count=max(count * 3, 9),
            )
        except Exception as exc:
            logger.warning(
                "day_preference_alts_failed campaign_id=%s err=%s",
                getattr(campaign, "id", None),
                str(exc)[:200],
            )
            alts = []
        filtered_alts = _filter_slots_by_window(alts, window=window, timezone=tz_name)
        return (filtered_alts or alts)[:count]
    return raw[:count]
