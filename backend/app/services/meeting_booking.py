"""Reserva de reuniones: fila Nexus + evento Google Calendar cuando corresponde."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import MeetingStatus, PipelineStage, ProspectStatus
from app.models.meeting import Meeting
from app.models.prospect import Prospect
from app.services import meeting_calendar_prep as cal_prep
from app.services import multichannel_sequence as mseq
from app.services import pipeline_sync
from app.services import prospect_commercial_state as pcs

logger = logging.getLogger(__name__)

CREATION_CALENDAR_LINK = "calendar_link"
CREATION_AUTO_NEXUS = "auto_agendada_por_nexus"
CREATION_MANUAL = "manual"
CREATION_SYNC = "calendar_sync"


def default_meeting_title(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
) -> str:
    """Título canónico Calendar: «Empresa prospecto & Empresa usuario»."""
    prospect_co = (getattr(prospect, "company_name", None) or "").strip()
    if not prospect_co:
        prospect_co = (prospect.name or "Prospecto").strip() or "Prospecto"

    seller_co = ""
    company_id = getattr(campaign, "company_id", None)
    if company_id:
        from app.models.company import Company

        row = db.get(Company, int(company_id))
        if row is not None:
            seller_co = (row.name or "").strip()
    if not seller_co:
        seller_co = (getattr(campaign, "name", None) or "").strip() or "Nexus"

    return f"{prospect_co} & {seller_co}"[:255]


def prospect_has_calendar_confirmed_meeting(db: Session, prospect: Prospect) -> bool:
    """Reunión futura con evento real en Google Calendar."""
    now = datetime.now(UTC)
    row = db.scalars(
        select(Meeting)
        .where(
            Meeting.prospect_id == prospect.id,
            Meeting.scheduled_for >= now,
            Meeting.meeting_status != MeetingStatus.canceled.value,
            Meeting.google_calendar_event_id.isnot(None),
        )
        .order_by(Meeting.scheduled_for.asc())
        .limit(1)
    ).first()
    return row is not None


def prospect_has_pending_meeting(db: Session, prospect: Prospect) -> bool:
    """Reunión registrada en Nexus sin evento Calendar confirmado."""
    now = datetime.now(UTC)
    row = db.scalars(
        select(Meeting)
        .where(
            Meeting.prospect_id == prospect.id,
            Meeting.scheduled_for >= now,
            Meeting.meeting_status != MeetingStatus.canceled.value,
            Meeting.google_calendar_event_id.is_(None),
        )
        .order_by(Meeting.scheduled_for.asc())
        .limit(1)
    ).first()
    return row is not None


def ensure_simulated_meeting_for_booked_prospect(
    db: Session, campaign: Campaign, prospect: Prospect
) -> bool:
    _ = db, campaign, prospect
    return False


def _first_name(name: str | None) -> str:
    raw = (name or "").strip()
    return raw.split()[0] if raw else "ahí"


def build_meeting_confirmation_reply(
    *,
    prospect_name: str | None,
    scheduled_for: datetime,
    html_link: str | None,
    timezone: str,
    is_reschedule: bool = False,
) -> str:
    slot = _format_slot_for_confirmation(scheduled_for, timezone)
    first = _first_name(prospect_name)
    if is_reschedule:
        lines = [
            f"Listo {first}, moví la reunión para {slot}.",
        ]
    else:
        lines = [
            "Perfecto.",
            f"Te agendé para {slot}.",
        ]
    if html_link:
        lines.append(f"Acá tenés la invitación:\n{html_link}")
        lines.append("Nos vemos ahí.")
    else:
        lines.append("Te comparto la invitación en cuanto esté disponible.")
    return "\n\n".join(lines)


def _format_slot_for_confirmation(scheduled_for: datetime, timezone: str) -> str:
    from zoneinfo import ZoneInfo

    from app.services.google_calendar_availability import format_slot_local

    tz = ZoneInfo(timezone)
    local = scheduled_for.astimezone(tz)
    now_local = datetime.now(UTC).astimezone(tz)
    time_str = local.strftime("%H:%M")
    delta_days = (local.date() - now_local.date()).days
    if delta_days == 0:
        return f"hoy a las {time_str}"
    if delta_days == 1:
        return f"mañana a las {time_str}"
    return format_slot_local(scheduled_for, timezone)


def calendar_error_needs_reconnect(calendar_error: str | None) -> bool:
    err = (calendar_error or "").strip().lower()
    if not err:
        return False
    return (
        "401" in err
        or "reconect" in err
        or "reconnect" in err
        or "token" in err
        or "invalid_grant" in err
    )


CALENDAR_RECONNECT_OPERATOR_ALERT = (
    "Google Calendar necesita reconexión. "
    "Andá a Configuración → Integraciones y volvé a conectar Google Calendar "
    "antes de generar la confirmación al prospecto."
)


def build_meeting_not_created_reply(
    *,
    prospect_name: str | None,
    calendar_link: str | None = None,
    calendar_error: str | None = None,
) -> str:
    """Respuesta al prospecto cuando no hay evento verificable (nunca texto de Configuración)."""
    # Reconexión = asunto del operador, no copy para el prospecto.
    if calendar_error_needs_reconnect(calendar_error):
        return ""
    link = (calendar_link or "").strip()
    if link:
        return (
            "No pude crear la reunión automáticamente.\n\n"
            f"Te comparto mi link de agenda:\n{link}"
        )
    first = _first_name(prospect_name)
    return (
        f"Gracias {first}. Necesito confirmar disponibilidad "
        "antes de crear la reunión."
    )


def _attach_booking_reply(
    booking: dict[str, Any],
    *,
    prospect_name: str | None,
    scheduled_for: datetime,
    timezone: str,
    calendar_link: str | None,
    is_reschedule: bool = False,
) -> dict[str, Any]:
    """Solo confirma agendamiento si calendar_created y hay event_id."""
    booking.pop("confirmation_reply", None)
    booking.pop("booking_failed_reply", None)
    booking.pop("requires_calendar_reconnect", None)
    booking.pop("operator_alert", None)
    if booking.get("calendar_created") and booking.get("google_calendar_event_id"):
        booking["confirmation_reply"] = build_meeting_confirmation_reply(
            prospect_name=prospect_name,
            scheduled_for=scheduled_for,
            html_link=booking.get("google_calendar_html_link"),
            timezone=timezone,
            is_reschedule=is_reschedule,
        )
    elif calendar_error_needs_reconnect(booking.get("calendar_error")):
        booking["requires_calendar_reconnect"] = True
        booking["operator_alert"] = CALENDAR_RECONNECT_OPERATOR_ALERT
    else:
        failed = build_meeting_not_created_reply(
            prospect_name=prospect_name,
            calendar_link=calendar_link,
            calendar_error=booking.get("calendar_error"),
        )
        if failed.strip():
            booking["booking_failed_reply"] = failed
    return booking


def build_slot_alternatives_reply(
    *,
    prospect_name: str | None,
    alternatives: list[datetime],
    timezone: str,
    requested_slot: datetime | None = None,
) -> str:
    from zoneinfo import ZoneInfo

    from app.services.google_calendar_availability import format_day_label, format_slot_local

    if not alternatives:
        # No inventar “no hay nada” ni empujar un link: pedimos otra franja concreta.
        return (
            "Ese horario concreto no me quedó libre según el calendario.\n\n"
            "¿Me proponés otro día y hora (ej. mañana a las 15) y te confirmo al toque?"
        )
    tz = ZoneInfo(timezone)
    if requested_slot is None:
        bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
        return f"Ese horario no está disponible.\n\nTengo:\n{bullets}\n\n¿Cuál preferís?"

    req_local = requested_slot.astimezone(tz)
    req_day = req_local.date()
    req_day_label = format_day_label(requested_slot, timezone)
    req_time_label = req_local.strftime("%H:%M")
    now_local = datetime.now(UTC).astimezone(tz)
    is_today = req_day == now_local.date()

    same_day = [a for a in alternatives if a.astimezone(tz).date() == req_day]

    if same_day:
        times = ", ".join(a.astimezone(tz).strftime("%H:%M") for a in same_day)
        if is_today:
            return (
                f"Hoy no tengo a las {req_time_label}, "
                f"pero sí a las {times}.\n\n¿Te sirve alguno?"
            )
        return (
            f"El {req_day_label} no tengo a las {req_time_label}, "
            f"pero sí a las {times}.\n\n¿Te sirve alguno?"
        )

    # Hay huecos reales en otros días: listarlos (no decir “no tengo” a secas).
    bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
    day_phrase = "Hoy" if is_today else f"El {req_day_label}"
    return (
        f"{day_phrase} a las {req_time_label} no me quedó libre.\n\n"
        f"Estos horarios sí están disponibles:\n{bullets}\n\n¿Cuál preferís?"
    )


def find_real_alternative_slots(
    db: Session,
    *,
    campaign: Campaign,
    calendar_user_id: int,
    around: datetime,
    duration_minutes: int = 30,
    count: int = 3,
) -> list[datetime]:
    """
    Alternativas reales vía freeBusy: mismo día primero; si no hay, otros días.
    Nunca inventa huecos: si Calendar falla, lista vacía.
    """
    from app.services.google_calendar_availability import find_available_slots

    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    hours_text = getattr(campaign, "available_hours", None)
    try:
        same_day = find_available_slots(
            db,
            company_id=int(campaign.company_id),
            seller_user_id=int(calendar_user_id),
            around=around,
            duration_minutes=duration_minutes,
            count=count,
            timezone=tz,
            available_hours=hours_text,
            same_day_only=True,
        )
        if same_day:
            return same_day
        return find_available_slots(
            db,
            company_id=int(campaign.company_id),
            seller_user_id=int(calendar_user_id),
            around=around,
            duration_minutes=duration_minutes,
            count=count,
            timezone=tz,
            available_hours=hours_text,
            same_day_only=False,
        )
    except Exception as exc:
        logger.warning(
            "find_real_alternative_slots failed campaign_id=%s err=%s",
            campaign.id,
            str(exc)[:200],
        )
        return []


def build_calendar_link_reply(
    *,
    prospect_name: str | None,
    calendar_link: str,
    existing_reply: str | None = None,
) -> str:
    """Opción link de calendar — un solo CTA (sin pedir franja aparte en el mismo mensaje)."""
    link = (calendar_link or "").strip()
    first = _first_name(prospect_name)
    base = (existing_reply or "").strip()
    if link and link in base and "agendar" in base.lower():
        # Ya es un mensaje de link; no duplicar.
        return base
    intro = (
        f"Genial {first}, me alegra el interés.\n\n"
        f"Para que elijas el horario que te quede cómodo, podés agendar acá:\n{link}\n\n"
        f"Cuando reserves, recibís la invitación automáticamente."
    )
    # No concatenar con replies que ya piden día/horario o confirman slot (evita doble oferta).
    if base and len(base) > 40:
        low = base.lower()
        dual_markers = (
            "qué día",
            "que dia",
            "qué horario",
            "que horario",
            "te quedan",
            "agendar",
            "calendar",
            "http",
            "quedamos",
            "viernes",
            "demo",
        )
        if any(m in low for m in dual_markers):
            return intro
        return f"{base}\n\n{intro}"
    return intro


def resolve_campaign_calendar_user_id(db: Session, campaign: Campaign) -> int | None:
    """
    Usuario con Google Calendar OAuth válido para la campaña.
    Preferí el seller; si no tiene Calendar, usá otro de la empresa con token válido.
    """
    from app.models.user import User
    from app.services.gmail_drafts import get_valid_google_calendar_connection
    from app.services.manual_sequence_kickoff import try_find_gmail_operator

    preferred = db.get(User, int(campaign.seller_id)) if campaign.seller_id else None
    operator = try_find_gmail_operator(
        db, company_id=int(campaign.company_id), preferred=preferred
    )
    if operator is None:
        return None
    try:
        get_valid_google_calendar_connection(
            db,
            company_id=int(campaign.company_id),
            user_id=int(operator.id),
        )
        return int(operator.id)
    except Exception:
        logger.warning(
            "campaign calendar operator has no valid Calendar OAuth campaign_id=%s user_id=%s",
            campaign.id,
            operator.id,
        )
        return None


def _seller_google_calendar_ready(db: Session, campaign: Campaign) -> bool:
    """True solo si hay token Google Calendar usable (no alcanza con Gmail solo)."""
    return resolve_campaign_calendar_user_id(db, campaign) is not None


def usable_calendar_booking_link(url: str | None) -> str | None:
    """
    Link de agenda usable para que el prospecto reserve.
    La UI genérica de Google Calendar (/calendar/u/0/r) NO sirve para agendar.
    """
    link = (url or "").strip()
    if not link or "://" not in link:
        return None
    low = link.lower().split("?", 1)[0].rstrip("/")
    if re.search(r"calendar\.google\.com/calendar/u/\d+/r$", low):
        return None
    if low.endswith("calendar.google.com") or low.endswith("calendar.google.com/calendar"):
        return None
    return link


def prospect_has_verified_calendar_meeting(db: Session, prospect_id: int) -> bool:
    """True si hay Meeting activo con evento real en Google Calendar."""
    row = db.scalars(
        select(Meeting).where(
            Meeting.prospect_id == int(prospect_id),
            Meeting.meeting_status != MeetingStatus.canceled.value,
            Meeting.google_calendar_event_id.isnot(None),
        )
    ).first()
    return row is not None


def _cancel_superseded_meetings(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    keep_slot: datetime | None,
    seller_id: int | None,
) -> int:
    """
    Al reagendar, cancela otras reuniones activas del prospecto y borra el evento en Google Calendar.
    """
    rows = db.scalars(
        select(Meeting).where(
            Meeting.prospect_id == prospect.id,
            Meeting.meeting_status != MeetingStatus.canceled.value,
        )
    ).all()
    canceled = 0
    for row in rows:
        if keep_slot is not None and row.scheduled_for and _slot_matches_meeting(row.scheduled_for, keep_slot):
            continue
        if row.google_calendar_event_id and seller_id:
            try:
                from app.services.google_calendar_create import delete_calendar_event

                delete_calendar_event(
                    db,
                    company_id=campaign.company_id,
                    seller_user_id=int(seller_id),
                    event_id=row.google_calendar_event_id,
                )
            except Exception as exc:
                logger.warning(
                    "cancel_superseded_meeting calendar_delete_failed meeting_id=%s err=%s",
                    row.id,
                    str(exc)[:200],
                )
        row.meeting_status = MeetingStatus.canceled.value
        canceled += 1
    if canceled:
        logger.info(
            "cancel_superseded_meetings prospect_id=%s canceled=%s keep_slot=%s",
            prospect.id,
            canceled,
            keep_slot.isoformat() if keep_slot else None,
        )
    return canceled


def book_prospect_meeting(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    scheduled_for: datetime | None = None,
    title: str | None = None,
    description: str | None = None,
    duration_minutes: int = 30,
    create_google_event: bool = True,
    testing: bool = False,
    creation_method: str = CREATION_MANUAL,
    created_by_user_id: int | None = None,
    require_scheduled_for: bool = False,
    check_availability: bool = True,
) -> dict[str, Any]:
    """
    Crea reunión en Nexus y (si corresponde) evento en Google Calendar.

    - Sin `scheduled_for` y `require_scheduled_for=True` → error (no inventa +72h).
    - Si se pide Calendar y falla / no hay conexión → no crea Meeting fantasma.
    - Con `check_availability` valida available_hours + freeBusy antes de crear.
    """
    if scheduled_for is None and require_scheduled_for:
        return {
            "meeting_id": None,
            "scheduled_for": None,
            "google_calendar_event_id": None,
            "google_calendar_html_link": None,
            "calendar_created": False,
            "calendar_error": (
                "No hay un horario concreto en la conversación. "
                "Pedile al prospecto día y hora (ej. mañana a las 15)."
            ),
            "creation_method": creation_method,
            "created_by_user_id": created_by_user_id or campaign.seller_id,
            "needs_slot": True,
        }

    anchor = scheduled_for or cal_prep.default_scheduled_anchor(72)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    duration = max(15, min(int(duration_minutes), 240))
    slots = cal_prep.build_placeholder_slots(anchor=anchor, duration_minutes=duration)
    meeting_title = (title or "").strip() or default_meeting_title(
        db, prospect=prospect, campaign=campaign
    )
    meeting_desc = (description or "Coordinada desde conversación Nexus.").strip()
    if testing:
        meeting_desc = f"[testing]\n{meeting_desc}"

    google_event_id: str | None = None
    google_html_link: str | None = None
    calendar_error: str | None = None
    calendar_uid = resolve_campaign_calendar_user_id(db, campaign)
    seller_id = created_by_user_id or calendar_uid or campaign.seller_id

    if (
        check_availability
        and create_google_event
        and seller_id
        and scheduled_for is not None
        and not testing
    ):
        from app.services.available_hours import slot_within_available_hours
        from app.services.google_calendar_availability import (
            fetch_busy_intervals,
            slot_is_free,
        )

        hours_text = getattr(campaign, "available_hours", None)
        if not slot_within_available_hours(anchor, timezone=tz, available_hours=hours_text):
            alts = find_real_alternative_slots(
                db,
                campaign=campaign,
                calendar_user_id=int(seller_id),
                around=anchor,
                duration_minutes=duration,
            )
            return {
                "meeting_id": None,
                "scheduled_for": anchor.isoformat(),
                "google_calendar_event_id": None,
                "google_calendar_html_link": None,
                "calendar_created": False,
                "calendar_error": "El horario está fuera de la ventana disponible de la campaña.",
                "outside_available_hours": True,
                "alternatives_reply": build_slot_alternatives_reply(
                    prospect_name=prospect.name,
                    alternatives=alts,
                    timezone=tz,
                    requested_slot=anchor,
                ),
                "alternative_slots": [a.isoformat() for a in alts],
                "creation_method": creation_method,
                "created_by_user_id": seller_id,
            }
        try:
            busy = fetch_busy_intervals(
                db,
                company_id=campaign.company_id,
                seller_user_id=int(seller_id),
                time_min=anchor - timedelta(hours=2),
                time_max=anchor + timedelta(hours=4),
            )
            end = anchor + timedelta(minutes=duration)
            if not slot_is_free(busy, start=anchor, end=end):
                alts = find_real_alternative_slots(
                    db,
                    campaign=campaign,
                    calendar_user_id=int(seller_id),
                    around=anchor,
                    duration_minutes=duration,
                )
                return {
                    "meeting_id": None,
                    "scheduled_for": anchor.isoformat(),
                    "google_calendar_event_id": None,
                    "google_calendar_html_link": None,
                    "calendar_created": False,
                    "calendar_error": "Ese horario ya está ocupado en Google Calendar.",
                    "slot_busy": True,
                    "alternatives_reply": build_slot_alternatives_reply(
                        prospect_name=prospect.name,
                        alternatives=alts,
                        timezone=tz,
                        requested_slot=anchor,
                    ),
                    "alternative_slots": [a.isoformat() for a in alts],
                    "creation_method": creation_method,
                    "created_by_user_id": seller_id,
                }
        except Exception as exc:
            # Si freeBusy falla, seguimos e intentamos crear (Calendar decide).
            logger.warning(
                "book_prospect_meeting freebusy_check_failed prospect_id=%s err=%s",
                prospect.id,
                str(exc)[:200],
            )

    _cancel_superseded_meetings(
        db,
        campaign=campaign,
        prospect=prospect,
        keep_slot=anchor,
        seller_id=int(seller_id) if seller_id else None,
    )

    if create_google_event and seller_id:
        try:
            from app.services.google_calendar_create import create_calendar_event

            created = create_calendar_event(
                db,
                company_id=campaign.company_id,
                seller_user_id=int(seller_id),
                title=meeting_title,
                description=meeting_desc,
                start_at=anchor,
                duration_minutes=duration,
                attendee_email=prospect.email,
                timezone=tz,
            )
            google_event_id = created.get("event_id")
            google_html_link = created.get("html_link")
        except Exception as exc:
            calendar_error = str(exc)[:300]
            logger.warning(
                "book_prospect_meeting calendar_create_failed prospect_id=%s err=%s",
                prospect.id,
                calendar_error,
            )

    # Sin evento Calendar real: no crear Meeting fantasma (salvo testing local).
    if create_google_event and not google_event_id and not testing:
        return {
            "meeting_id": None,
            "scheduled_for": anchor.isoformat(),
            "google_calendar_event_id": None,
            "google_calendar_html_link": None,
            "calendar_created": False,
            "calendar_error": calendar_error
            or "No se pudo crear el evento en Google Calendar. Reconectá Calendar en Integraciones.",
            "creation_method": creation_method,
            "created_by_user_id": seller_id,
        }

    if not create_google_event and not testing:
        return {
            "meeting_id": None,
            "scheduled_for": anchor.isoformat(),
            "google_calendar_event_id": None,
            "google_calendar_html_link": None,
            "calendar_created": False,
            "calendar_error": calendar_error
            or "Google Calendar no está conectado. Reconectá en Integraciones.",
            "creation_method": creation_method,
            "created_by_user_id": seller_id,
        }

    row = Meeting(
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        title=meeting_title,
        description=meeting_desc,
        scheduled_for=anchor,
        meeting_status=MeetingStatus.pending.value,
        timezone=tz,
        suggested_slots=[s["start"] for s in slots],
        duration_minutes=duration,
        google_calendar_event_id=google_event_id,
        google_calendar_html_link=google_html_link,
        creation_method=creation_method,
        created_by_user_id=int(seller_id) if seller_id else None,
    )
    db.add(row)
    db.flush()

    prospect.meeting_suggestion_pending = False
    if google_event_id:
        prospect.status = ProspectStatus.meeting_booked.value
        prospect.pipeline_stage = PipelineStage.reunion_agendada.value
        mseq.enforce_meeting_priority_over_sequence(db, prospect, campaign)
        prospect.commercial_state = pcs.COMMERCIAL_REUNION_AGENDADA
    else:
        if prospect.status not in (
            ProspectStatus.meeting_booked.value,
            ProspectStatus.failed.value,
        ):
            prospect.status = ProspectStatus.interested.value
        if prospect.pipeline_stage != PipelineStage.reunion_agendada.value:
            prospect.pipeline_stage = PipelineStage.interesado.value
        prospect.commercial_state = pcs.COMMERCIAL_REUNION_PENDIENTE

    pipeline_sync.sync_pipeline_from_status(prospect)
    pcs.apply_commercial_state(
        prospect,
        response_class="interesado",
        reply_objective="agendar",
        db=db,
        testing=testing,
    )
    if google_event_id:
        prospect.commercial_state = pcs.COMMERCIAL_REUNION_AGENDADA
    else:
        prospect.commercial_state = pcs.COMMERCIAL_REUNION_PENDIENTE
    prospect.commercial_state_is_testing = bool(testing)

    db.flush()
    if google_event_id:
        try:
            from app.services.crm import sync as crm_sync

            crm_sync.sync_meeting_booked(
                db,
                prospect=prospect,
                meeting_id=int(row.id),
                scheduled_for=anchor,
                title=meeting_title,
            )
        except Exception:
            logger.exception("crm meeting sync failed prospect_id=%s meeting_id=%s", prospect.id, row.id)
    return {
        "meeting_id": row.id,
        "scheduled_for": anchor.isoformat(),
        "google_calendar_event_id": google_event_id,
        "google_calendar_html_link": google_html_link,
        "calendar_created": bool(google_event_id),
        "calendar_error": calendar_error,
        "creation_method": creation_method,
        "created_by_user_id": seller_id,
        "commercial_state": prospect.commercial_state,
        "commercial_state_label": pcs.commercial_state_label(prospect.commercial_state),
    }


def resolve_meeting_slot_from_prospect_thread(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
) -> datetime | None:
    """Busca el último inbound del hilo con un horario parseable."""
    slot, _duration = resolve_meeting_booking_from_prospect_thread(
        db, prospect=prospect, campaign=campaign
    )
    return slot


def resolve_meeting_booking_from_prospect_thread(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
) -> tuple[datetime | None, int]:
    """
    Slot más reciente del hilo + duración más reciente mencionada (default 30).
    Sirve para «ya te dije el día y la duración» sin repetir datos en el mensaje.
    """
    from app.models.outreach import OutreachMessage
    from app.services.meeting_slot_parser import (
        inbound_has_explicit_day_anchor,
        parse_meeting_duration_minutes,
        parse_meeting_slot,
    )

    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    meeting_ctx = _active_meeting_scheduled_for(db, prospect)
    conv_ctx = _conversation_day_context(db, prospect, timezone=tz, current_inbound=None)

    rows = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.direction == "inbound",
        )
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(16)
    ).all()

    slot: datetime | None = None
    duration_from_slot = 30
    duration_recent: int | None = None

    for row in rows:
        plain = _outreach_message_plain(row.message)
        if not plain:
            continue
        if duration_recent is None and _inbound_mentions_duration(plain):
            duration_recent = parse_meeting_duration_minutes(plain, default=30)
        if slot is None:
            explicit = inbound_has_explicit_day_anchor(plain)
            day_ctx = None if explicit else (conv_ctx if conv_ctx is not None else meeting_ctx)
            parsed = parse_meeting_slot(plain, timezone=tz, context_meeting_at=day_ctx)
            if parsed is not None:
                slot = parsed
                duration_from_slot = parse_meeting_duration_minutes(plain, default=30)

    if slot is None:
        return None, int(duration_recent or 30)
    return slot, int(duration_recent or duration_from_slot)


def _inbound_mentions_duration(plain: str) -> bool:
    low = (plain or "").lower()
    if "min" in low or "hora" in low:
        return True
    return bool(re.search(r"\b\d{1,3}\s*'", plain or ""))


def build_context_recall_confirmation_reply(
    *,
    prospect_name: str | None,
    scheduled_for: datetime,
    duration_minutes: int,
    html_link: str | None,
    timezone: str,
) -> str:
    first = _first_name(prospect_name)
    slot = _format_slot_for_confirmation(scheduled_for, timezone)
    mins = max(15, min(int(duration_minutes), 240))
    lines = [
        f"Tenés razón {first}, perdón.",
        f"Quedamos en {slot}, {mins} minutos.",
    ]
    if html_link:
        lines.append(f"Acá la invitación:\n{html_link}")
    return "\n\n".join(lines)


def attempt_fulfill_from_conversation_context(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str | None = None,
    testing: bool = False,
) -> dict[str, Any] | None:
    """
    Recupera día/hora/duración del hilo o confirma la reunión activa.
    Evita volver a listar horarios libres cuando el prospecto ya los dijo.
    """
    _ = inbound_text
    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    cal_link = (getattr(campaign, "calendar_link", None) or "").strip() or None
    slot, duration = resolve_meeting_booking_from_prospect_thread(
        db, prospect=prospect, campaign=campaign
    )
    meeting = _active_meeting_row(db, prospect)

    if meeting is not None and meeting.scheduled_for is not None:
        scheduled = meeting.scheduled_for
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)
        same_slot = slot is None or _slot_matches_meeting(scheduled, slot)
        if same_slot:
            old_dur = max(15, min(int(meeting.duration_minutes or 30), 240))
            new_dur = max(15, min(int(duration), 240))
            if new_dur != old_dur:
                synthetic = f"cambiar de {old_dur} a {new_dur} min"
                changed = attempt_duration_only_change(
                    db,
                    campaign=campaign,
                    prospect=prospect,
                    inbound_text=synthetic,
                    testing=testing,
                )
                if changed and (
                    changed.get("confirmation_reply")
                    or changed.get("alternatives_reply")
                    or changed.get("booking_failed_reply")
                ):
                    return changed
            html = (meeting.google_calendar_html_link or "").strip() or None
            return {
                "calendar_created": bool(meeting.google_calendar_event_id),
                "meeting_id": meeting.id,
                "from_conversation_context": True,
                "duration_minutes": old_dur,
                "confirmation_reply": build_context_recall_confirmation_reply(
                    prospect_name=prospect.name,
                    scheduled_for=scheduled,
                    duration_minutes=old_dur,
                    html_link=html,
                    timezone=tz,
                ),
            }

    if slot is None:
        return None

    meeting_ctx = meeting.scheduled_for if meeting is not None else None
    is_reschedule = meeting_ctx is not None
    existing = _existing_booking_result_for_slot(
        db,
        campaign=campaign,
        prospect=prospect,
        slot=slot,
        tz=tz,
        cal_link=cal_link,
        is_reschedule=is_reschedule,
        duration_minutes=duration,
    )
    if existing is not None:
        return {**existing, "from_conversation_context": True}

    live_calendar = _seller_google_calendar_ready(db, campaign)
    if live_calendar:
        try:
            result = _book_with_calendar_availability(
                db,
                campaign=campaign,
                prospect=prospect,
                slot=slot,
                tz=tz,
                cal_link=cal_link,
                testing=testing,
                is_reschedule=is_reschedule,
                duration_minutes=duration,
            )
            if result is not None:
                result["from_conversation_context"] = True
            return result
        except Exception as exc:
            logger.warning(
                "fulfill_from_conversation_context book_failed prospect_id=%s err=%s",
                prospect.id,
                str(exc)[:200],
            )
            return {
                "calendar_created": False,
                "calendar_error": str(exc)[:300],
                "from_conversation_context": True,
                "booking_failed_reply": build_meeting_not_created_reply(
                    prospect_name=prospect.name,
                    calendar_link=cal_link,
                    calendar_error=str(exc)[:300],
                ),
            }

    booking = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=slot,
        create_google_event=False,
        testing=testing,
        creation_method=CREATION_AUTO_NEXUS,
        duration_minutes=duration,
        check_availability=False,
    )
    attached = _attach_booking_reply(
        booking,
        prospect_name=prospect.name,
        scheduled_for=slot,
        timezone=tz,
        calendar_link=cal_link,
        is_reschedule=is_reschedule,
    )
    attached["from_conversation_context"] = True
    return attached


def _slot_matches_meeting(scheduled_for: datetime, slot: datetime, *, tolerance_sec: int = 120) -> bool:
    a = scheduled_for
    b = slot
    if a.tzinfo is None:
        a = a.replace(tzinfo=UTC)
    if b.tzinfo is None:
        b = b.replace(tzinfo=UTC)
    return abs((a - b).total_seconds()) <= tolerance_sec


def _existing_booking_result_for_slot(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    slot: datetime,
    tz: str,
    cal_link: str | None,
    is_reschedule: bool = False,
    duration_minutes: int | None = None,
) -> dict[str, Any] | None:
    """Reutiliza reunión ya creada para el mismo horario (p. ej. re-ejecución del worker programado)."""
    now = datetime.now(UTC)
    rows = db.scalars(
        select(Meeting)
        .where(
            Meeting.prospect_id == prospect.id,
            Meeting.meeting_status != MeetingStatus.canceled.value,
            Meeting.scheduled_for >= now - timedelta(hours=1),
        )
        .order_by(Meeting.scheduled_for.desc())
    ).all()
    for row in rows:
        existing = row.scheduled_for
        if existing is None:
            continue
        if not _slot_matches_meeting(existing, slot):
            continue
        if duration_minutes is not None and int(row.duration_minutes or 30) != int(duration_minutes):
            continue
        booking: dict[str, Any] = {
            "meeting_id": row.id,
            "scheduled_for": existing.isoformat(),
            "google_calendar_event_id": row.google_calendar_event_id,
            "google_calendar_html_link": row.google_calendar_html_link,
            "calendar_created": bool(row.google_calendar_event_id),
            "creation_method": row.creation_method,
            "reused_existing": True,
        }
        if row.google_calendar_event_id:
            return _attach_booking_reply(
                booking,
                prospect_name=prospect.name,
                scheduled_for=existing if existing.tzinfo else existing.replace(tzinfo=UTC),
                timezone=tz,
                calendar_link=cal_link,
                is_reschedule=is_reschedule,
            )
    return None


def _book_with_calendar_availability(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    slot: datetime,
    tz: str,
    cal_link: str | None,
    testing: bool,
    is_reschedule: bool = False,
    duration_minutes: int = 30,
) -> dict[str, Any]:
    duration = max(15, min(int(duration_minutes), 240))
    end = slot + timedelta(minutes=duration)
    from app.services.available_hours import slot_within_available_hours
    from app.services.google_calendar_availability import (
        fetch_busy_intervals,
        slot_is_free,
    )

    hours_text = getattr(campaign, "available_hours", None)
    calendar_uid = resolve_campaign_calendar_user_id(db, campaign) or int(campaign.seller_id or 0)
    if not calendar_uid:
        return {
            "calendar_created": False,
            "slot_busy": False,
            "requested_slot": slot.isoformat(),
            "requires_calendar_reconnect": True,
            "operator_alert": CALENDAR_RECONNECT_OPERATOR_ALERT,
        }
    if not slot_within_available_hours(slot, timezone=tz, available_hours=hours_text):
        alts = find_real_alternative_slots(
            db,
            campaign=campaign,
            calendar_user_id=int(calendar_uid),
            around=slot,
            duration_minutes=duration,
        )
        return {
            "calendar_created": False,
            "slot_busy": True,
            "requested_slot": slot.isoformat(),
            "alternatives_reply": build_slot_alternatives_reply(
                prospect_name=prospect.name,
                alternatives=alts,
                timezone=tz,
                requested_slot=slot,
            ),
            "alternative_slots": [a.isoformat() for a in alts],
            "outside_available_hours": True,
        }

    busy = fetch_busy_intervals(
        db,
        company_id=campaign.company_id,
        seller_user_id=int(calendar_uid),
        time_min=slot - timedelta(hours=2),
        time_max=slot + timedelta(hours=4),
    )
    if slot_is_free(busy, start=slot, end=end):
        booking = book_prospect_meeting(
            db,
            campaign=campaign,
            prospect=prospect,
            scheduled_for=slot,
            create_google_event=True,
            testing=testing,
            creation_method=CREATION_AUTO_NEXUS,
            created_by_user_id=calendar_uid,
            duration_minutes=duration,
            check_availability=False,
        )
        return _attach_booking_reply(
            booking,
            prospect_name=prospect.name,
            scheduled_for=slot,
            timezone=tz,
            calendar_link=cal_link,
            is_reschedule=is_reschedule,
        )

    alts = find_real_alternative_slots(
        db,
        campaign=campaign,
        calendar_user_id=int(calendar_uid),
        around=slot,
        duration_minutes=duration,
    )
    return {
        "calendar_created": False,
        "slot_busy": True,
        "requested_slot": slot.isoformat(),
        "alternatives_reply": build_slot_alternatives_reply(
            prospect_name=prospect.name,
            alternatives=alts,
            timezone=tz,
            requested_slot=slot,
        ),
        "alternative_slots": [a.isoformat() for a in alts],
    }


def _active_meeting_scheduled_for(db: Session, prospect: Prospect) -> datetime | None:
    """Reunión activa más reciente del prospecto (para reagendar sin repetir el día)."""
    row = _active_meeting_row(db, prospect)
    return row.scheduled_for if row else None


def _active_meeting_row(db: Session, prospect: Prospect) -> Meeting | None:
    now = datetime.now(UTC)
    return db.scalars(
        select(Meeting)
        .where(
            Meeting.prospect_id == prospect.id,
            Meeting.meeting_status != MeetingStatus.canceled.value,
            Meeting.scheduled_for >= now - timedelta(hours=2),
        )
        .order_by(Meeting.scheduled_for.desc())
    ).first()


def build_duration_change_confirmation_reply(
    *,
    prospect_name: str | None,
    scheduled_for: datetime,
    duration_minutes: int,
    html_link: str | None,
    timezone: str,
) -> str:
    slot = _format_slot_for_confirmation(scheduled_for, timezone)
    first = _first_name(prospect_name)
    mins = max(15, min(int(duration_minutes), 240))
    lines = [
        f"Perfecto {first}.",
        f"Dejé la reunión en {mins} minutos, mismo horario ({slot}).",
    ]
    if html_link:
        lines.append(f"Invitación actualizada:\n{html_link}")
    return "\n\n".join(lines)


def attempt_duration_only_change(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    testing: bool = False,
) -> dict[str, Any] | None:
    """
    Prospecto pide solo acortar/alargar la reunión ya agendada (sin nuevo slot).
    Actualiza Meeting + evento Calendar; no ofrece horarios nuevos.
    """
    from app.services.meeting_slot_parser import inbound_is_duration_only_change

    new_duration = inbound_is_duration_only_change(inbound_text)
    if new_duration is None:
        return None

    meeting = _active_meeting_row(db, prospect)
    if meeting is None or meeting.scheduled_for is None:
        return {
            "duration_change": False,
            "no_meeting": True,
            "confirmation_reply": (
                f"Todavía no tengo una reunión agendada con {_first_name(prospect.name)}. "
                "Decime día y hora y la armamos (ej. martes a las 15, 15 min)."
            ),
        }

    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    old_duration = max(15, min(int(meeting.duration_minutes or 30), 240))
    new_duration = max(15, min(int(new_duration), 240))
    scheduled = meeting.scheduled_for
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)

    html_link = (meeting.google_calendar_html_link or "").strip() or None
    if new_duration == old_duration:
        return {
            "duration_change": False,
            "already_same": True,
            "meeting_id": meeting.id,
            "duration_minutes": new_duration,
            "confirmation_reply": build_duration_change_confirmation_reply(
                prospect_name=prospect.name,
                scheduled_for=scheduled,
                duration_minutes=new_duration,
                html_link=html_link,
                timezone=tz,
            ),
        }

    calendar_uid = resolve_campaign_calendar_user_id(db, campaign) or int(
        campaign.seller_id or 0
    )

    # Al alargar: chequear que el tramo extra esté libre.
    if new_duration > old_duration and calendar_uid and not testing:
        from app.services.google_calendar_availability import (
            fetch_busy_intervals,
            slot_is_free,
        )

        old_end = scheduled + timedelta(minutes=old_duration)
        new_end = scheduled + timedelta(minutes=new_duration)
        try:
            busy = fetch_busy_intervals(
                db,
                company_id=campaign.company_id,
                seller_user_id=int(calendar_uid),
                time_min=old_end - timedelta(minutes=1),
                time_max=new_end + timedelta(minutes=1),
            )
            if not slot_is_free(busy, start=old_end, end=new_end):
                alts = find_real_alternative_slots(
                    db,
                    campaign=campaign,
                    calendar_user_id=int(calendar_uid),
                    around=scheduled,
                    duration_minutes=new_duration,
                )
                return {
                    "duration_change": False,
                    "slot_busy": True,
                    "alternatives_reply": build_slot_alternatives_reply(
                        prospect_name=prospect.name,
                        alternatives=alts,
                        timezone=tz,
                        requested_slot=scheduled,
                    ),
                    "alternative_slots": [a.isoformat() for a in alts],
                }
        except Exception as exc:
            logger.warning(
                "duration_change freebusy_failed prospect_id=%s err=%s",
                prospect.id,
                str(exc)[:200],
            )

    if meeting.google_calendar_event_id and calendar_uid and not testing:
        from app.services.google_calendar_create import update_calendar_event_duration

        try:
            updated = update_calendar_event_duration(
                db,
                company_id=int(campaign.company_id),
                seller_user_id=int(calendar_uid),
                event_id=str(meeting.google_calendar_event_id),
                start_at=scheduled,
                duration_minutes=new_duration,
                timezone=tz,
                title=meeting.title,
            )
            html_link = updated.get("html_link") or html_link
            if updated.get("html_link"):
                meeting.google_calendar_html_link = updated["html_link"]
        except Exception as exc:
            logger.exception(
                "duration_change calendar_patch_failed prospect_id=%s meeting_id=%s",
                prospect.id,
                meeting.id,
            )
            return {
                "duration_change": False,
                "calendar_error": str(exc)[:300],
                "booking_failed_reply": (
                    f"Quise dejarla en {new_duration} minutos pero falló Calendar. "
                    "¿Reintentamos en un momento?"
                ),
            }

    meeting.duration_minutes = new_duration
    db.flush()

    logger.info(
        "duration_only_change prospect_id=%s meeting_id=%s %s→%s min",
        prospect.id,
        meeting.id,
        old_duration,
        new_duration,
    )
    return {
        "duration_change": True,
        "meeting_id": meeting.id,
        "duration_minutes": new_duration,
        "scheduled_for": scheduled.isoformat(),
        "google_calendar_event_id": meeting.google_calendar_event_id,
        "google_calendar_html_link": html_link,
        "calendar_created": bool(meeting.google_calendar_event_id),
        "confirmation_reply": build_duration_change_confirmation_reply(
            prospect_name=prospect.name,
            scheduled_for=scheduled,
            duration_minutes=new_duration,
            html_link=html_link,
            timezone=tz,
        ),
    }


def _outreach_message_plain(stored: str | None) -> str:
    from app.services.gmail_inbound_sync import extract_prospect_inbound_plain

    return extract_prospect_inbound_plain(stored) or (stored or "").strip()


def _outbound_is_slot_alternatives(plain: str) -> bool:
    low = plain.lower()
    if any(
        x in low
        for x in (
            "listo, mov",
            "listo, moví",
            "te agend",
            "te agendé",
            "invitacion",
            "invitación",
            "nos vemos ahí",
            "nos vemos ahi",
        )
    ):
        return False
    return any(
        x in low
        for x in (
            "pero si a las",
            "pero sí a las",
            "no tengo a las",
            "te sirve alguno",
            "cual preferis",
            "cuál preferís",
        )
    )


def _conversation_day_context(
    db: Session,
    prospect: Prospect,
    *,
    timezone: str,
    current_inbound: str | None = None,
) -> datetime | None:
    """
    Día acordado en la negociación (p. ej. viernes del primer inbound o alternativas del SDR).
    Ignora confirmaciones de reunión ya enviadas (evita heredar un día equivocado).
    """
    from app.models.outreach import OutreachMessage
    from app.services.meeting_slot_parser import infer_meeting_day_context

    rows = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect.id)
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(24)
    ).all()
    current_norm = (current_inbound or "").strip().lower()

    def _skip_current_inbound(plain: str, direction: str) -> bool:
        return (
            direction == "inbound"
            and bool(current_norm)
            and plain.strip().lower() == current_norm
        )

    for row in rows:
        if row.direction != "inbound":
            continue
        plain = _outreach_message_plain(row.message)
        if not plain or _skip_current_inbound(plain, row.direction):
            continue
        ctx = infer_meeting_day_context(plain, timezone=timezone)
        if ctx is not None:
            return ctx

    for row in rows:
        if row.direction != "outbound":
            continue
        plain = _outreach_message_plain(row.message)
        if not plain or not _outbound_is_slot_alternatives(plain):
            continue
        ctx = infer_meeting_day_context(plain, timezone=timezone)
        if ctx is not None:
            return ctx
    return None


def attempt_auto_book_from_message(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    reply_objective: str | None,
    sig: Any | None = None,
    testing: bool = False,
) -> dict[str, Any] | None:
    """
    Opción 2 — horario por mensaje: parsea, verifica disponibilidad y agenda o propone alternativas.
    """
    from app.services.meeting_slot_parser import (
        inbound_has_explicit_day_anchor,
        parse_meeting_duration_minutes,
        parse_meeting_slot,
    )

    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    meeting_ctx = _active_meeting_scheduled_for(db, prospect)
    is_reschedule = meeting_ctx is not None
    conv_ctx = _conversation_day_context(
        db,
        prospect,
        timezone=tz,
        current_inbound=inbound_text,
    )
    explicit_day_in_message = inbound_has_explicit_day_anchor(inbound_text)
    if explicit_day_in_message:
        day_ctx = None
    else:
        day_ctx = conv_ctx if conv_ctx is not None else (meeting_ctx if is_reschedule else None)
    slot = parse_meeting_slot(
        inbound_text,
        timezone=tz,
        context_meeting_at=day_ctx,
    )
    if slot is None:
        return None

    duration_minutes = parse_meeting_duration_minutes(inbound_text)

    existing = _existing_booking_result_for_slot(
        db,
        campaign=campaign,
        prospect=prospect,
        slot=slot,
        tz=tz,
        cal_link=(getattr(campaign, "calendar_link", None) or "").strip() or None,
        is_reschedule=is_reschedule,
        duration_minutes=duration_minutes,
    )
    if (
        existing is not None
        and is_reschedule
        and meeting_ctx is not None
        and not _slot_matches_meeting(meeting_ctx, slot)
    ):
        existing = None
    if existing is not None:
        logger.info(
            "attempt_auto_book_from_message reused existing meeting prospect_id=%s slot=%s",
            prospect.id,
            slot.isoformat(),
        )
        return existing

    cal_link = (getattr(campaign, "calendar_link", None) or "").strip() or None
    live_calendar = _seller_google_calendar_ready(db, campaign)

    logger.info(
        "attempt_auto_book_from_message prospect_id=%s slot=%s duration_min=%s testing=%s live_calendar=%s",
        prospect.id,
        slot.isoformat(),
        duration_minutes,
        testing,
        live_calendar,
    )

    if live_calendar:
        try:
            return _book_with_calendar_availability(
                db,
                campaign=campaign,
                prospect=prospect,
                slot=slot,
                tz=tz,
                cal_link=cal_link,
                testing=testing,
                is_reschedule=is_reschedule,
                duration_minutes=duration_minutes,
            )
        except Exception as exc:
            logger.warning(
                "attempt_auto_book_from_message failed prospect_id=%s err=%s",
                prospect.id,
                str(exc)[:200],
            )
            err = str(exc)[:300]
            if calendar_error_needs_reconnect(err):
                return {
                    "calendar_created": False,
                    "calendar_error": err,
                    "requires_calendar_reconnect": True,
                    "operator_alert": CALENDAR_RECONNECT_OPERATOR_ALERT,
                }
            failed = build_meeting_not_created_reply(
                prospect_name=prospect.name,
                calendar_link=cal_link,
                calendar_error=err,
            )
            out: dict[str, Any] = {
                "calendar_created": False,
                "calendar_error": err,
            }
            if failed.strip():
                out["booking_failed_reply"] = failed
            return out

    if not live_calendar:
        # Sin Calendar conectado: no crear Meeting fantasma; bloquear para el operador.
        return {
            "calendar_created": False,
            "calendar_error": (
                "Google Calendar no está conectado o el token venció. Reconectá en Integraciones."
            ),
            "requested_slot": slot.isoformat(),
            "requires_calendar_reconnect": True,
            "operator_alert": CALENDAR_RECONNECT_OPERATOR_ALERT,
        }

    return None



def prepare_agendar_reply_with_calendar_link(
    *,
    prospect: Prospect,
    campaign: Campaign,
    reply_objective: str | None,
    suggested_reply: str,
) -> str:
    """Comparte link de agenda usable; si el link de campaña no sirve, no inventa CTA."""
    if (reply_objective or "").strip().lower() != "agendar":
        return suggested_reply
    link = usable_calendar_booking_link(getattr(campaign, "calendar_link", None))
    if not link:
        return suggested_reply
    return build_calendar_link_reply(
        prospect_name=prospect.name,
        calendar_link=link,
        existing_reply=suggested_reply,
    )
