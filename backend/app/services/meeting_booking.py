"""Reserva de reuniones: fila Nexus + evento Google Calendar cuando corresponde."""

from __future__ import annotations

import logging
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
) -> str:
    slot = _format_slot_for_confirmation(scheduled_for, timezone)
    lines = [
        "Perfecto.",
        f"Te agendé para {slot}.",
    ]
    if html_link:
        lines.append(f"Te comparto la invitación:\n{html_link}")
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


def build_meeting_not_created_reply(
    *,
    prospect_name: str | None,
    calendar_link: str | None = None,
) -> str:
    """Respuesta honesta cuando no hay evento verificable en Google Calendar."""
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
) -> dict[str, Any]:
    """Solo confirma agendamiento si calendar_created y hay event_id."""
    booking.pop("confirmation_reply", None)
    booking.pop("booking_failed_reply", None)
    if booking.get("calendar_created") and booking.get("google_calendar_event_id"):
        booking["confirmation_reply"] = build_meeting_confirmation_reply(
            prospect_name=prospect_name,
            scheduled_for=scheduled_for,
            html_link=booking.get("google_calendar_html_link"),
            timezone=timezone,
        )
    else:
        booking["booking_failed_reply"] = build_meeting_not_created_reply(
            prospect_name=prospect_name,
            calendar_link=calendar_link,
        )
    return booking


def build_slot_alternatives_reply(
    *,
    prospect_name: str | None,
    alternatives: list[datetime],
    timezone: str,
    requested_slot: datetime | None = None,
) -> str:
    from zoneinfo import ZoneInfo

    from app.services.google_calendar_availability import format_slot_local

    first = _first_name(prospect_name)
    if not alternatives:
        return (
            f"Ese horario no está disponible.\n\n"
            f"¿Podés elegir otro desde el link de agenda que te compartí?"
        )
    tz = ZoneInfo(timezone)
    req_day = requested_slot.astimezone(tz).date() if requested_slot else None
    same_day = req_day is not None and all(
        alt.astimezone(tz).date() == req_day for alt in alternatives
    )
    if same_day:
        bullets = "\n".join(alt.astimezone(tz).strftime("%H:%M") for alt in alternatives)
    else:
        bullets = "\n".join(f"• {format_slot_local(s, timezone)}" for s in alternatives)
    return (
        f"Ese horario no está disponible.\n\n"
        f"Tengo:\n{bullets}\n\n"
        f"¿Cuál preferís?"
    )


def build_calendar_link_reply(
    *,
    prospect_name: str | None,
    calendar_link: str,
    existing_reply: str | None = None,
) -> str:
    """Opción 1 — compartir link de calendar como flujo preferido."""
    link = (calendar_link or "").strip()
    first = _first_name(prospect_name)
    base = (existing_reply or "").strip()
    if link and link in base:
        return base
    intro = (
        f"Genial {first}, me alegra el interés.\n\n"
        f"Para que elijas el horario que te quede cómodo, podés agendar acá:\n{link}\n\n"
        f"Cuando reserves, recibís la invitación automáticamente."
    )
    if base and len(base) > 40:
        return f"{base}\n\n{intro}"
    return intro


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
) -> dict[str, Any]:
    anchor = scheduled_for or cal_prep.default_scheduled_anchor(72)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    slots = cal_prep.build_placeholder_slots(anchor=anchor, duration_minutes=duration_minutes)
    meeting_title = (title or f"Reunión · {prospect.name}").strip()
    meeting_desc = (description or "Coordinada desde conversación Nexus.").strip()
    if testing:
        meeting_desc = f"[testing]\n{meeting_desc}"

    google_event_id: str | None = None
    google_html_link: str | None = None
    calendar_error: str | None = None
    seller_id = created_by_user_id or campaign.seller_id

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
                duration_minutes=duration_minutes,
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
        duration_minutes=duration_minutes,
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
    else:
        if prospect.status not in (
            ProspectStatus.meeting_booked.value,
            ProspectStatus.failed.value,
        ):
            prospect.status = ProspectStatus.interested.value
        if prospect.pipeline_stage != PipelineStage.reunion_agendada.value:
            prospect.pipeline_stage = PipelineStage.interesado.value

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


def _seller_google_calendar_ready(db: Session, campaign: Campaign) -> bool:
    """True si el SDR de la campaña tiene Google conectado (Gmail/Calendar OAuth)."""
    if not campaign.seller_id:
        return False
    try:
        from app.services.gmail_drafts import get_valid_gmail_connection

        get_valid_gmail_connection(
            db,
            company_id=campaign.company_id,
            user_id=int(campaign.seller_id),
        )
        return True
    except Exception:
        return False


def _book_with_calendar_availability(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    slot: datetime,
    tz: str,
    cal_link: str | None,
    testing: bool,
) -> dict[str, Any]:
    duration = 30
    end = slot + timedelta(minutes=duration)
    from app.services.google_calendar_availability import (
        fetch_busy_intervals,
        find_available_slots,
        slot_is_free,
    )

    busy = fetch_busy_intervals(
        db,
        company_id=campaign.company_id,
        seller_user_id=int(campaign.seller_id),
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
            created_by_user_id=campaign.seller_id,
        )
        return _attach_booking_reply(
            booking,
            prospect_name=prospect.name,
            scheduled_for=slot,
            timezone=tz,
            calendar_link=cal_link,
        )

    alts = find_available_slots(
        db,
        company_id=campaign.company_id,
        seller_user_id=int(campaign.seller_id),
        around=slot,
        duration_minutes=duration,
        timezone=tz,
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
    from app.services.meeting_slot_parser import parse_meeting_slot
    from app.services import conversation_intelligence as ci

    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    slot = parse_meeting_slot(inbound_text, timezone=tz)
    if slot is None:
        if not sig or not ci.meeting_acceptance_detected(inbound_text):
            return None
        return None

    cal_link = (getattr(campaign, "calendar_link", None) or "").strip() or None
    live_calendar = bool(campaign.seller_id) and _seller_google_calendar_ready(db, campaign)

    logger.info(
        "attempt_auto_book_from_message prospect_id=%s slot=%s testing=%s live_calendar=%s",
        prospect.id,
        slot.isoformat(),
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
            )
        except Exception as exc:
            logger.warning(
                "attempt_auto_book_from_message failed prospect_id=%s err=%s",
                prospect.id,
                str(exc)[:200],
            )
            return {
                "calendar_created": False,
                "calendar_error": str(exc)[:300],
                "booking_failed_reply": build_meeting_not_created_reply(
                    prospect_name=prospect.name,
                    calendar_link=cal_link,
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
        created_by_user_id=campaign.seller_id,
    )
    return _attach_booking_reply(
        booking,
        prospect_name=prospect.name,
        scheduled_for=slot,
        timezone=tz,
        calendar_link=cal_link,
    )


def prepare_agendar_reply_with_calendar_link(
    *,
    prospect: Prospect,
    campaign: Campaign,
    reply_objective: str | None,
    suggested_reply: str,
) -> str:
    """Opción 1 — asegura link de calendar en respuesta de agendar."""
    if (reply_objective or "").strip().lower() != "agendar":
        return suggested_reply
    link = (getattr(campaign, "calendar_link", None) or "").strip()
    if not link:
        return suggested_reply
    return build_calendar_link_reply(
        prospect_name=prospect.name,
        calendar_link=link,
        existing_reply=suggested_reply,
    )
