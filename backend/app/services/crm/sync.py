from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.crm_sync_event import CrmSyncEvent
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.crm import company_credentials as cc
from app.services.crm import hubspot, salesforce

_logger = logging.getLogger("nexus.crm.sync")

PREVIEW_MAX = 200


def _split_name(full: str | None) -> tuple[str | None, str | None]:
    parts = (full or "").strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _prospect_email(prospect: Prospect) -> str | None:
    raw = (prospect.email or "").strip().lower()
    if raw and "@" in raw:
        return raw
    return None


def _preview(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:PREVIEW_MAX]


def _channel_label(channel: str) -> str:
    return {"email": "Email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp"}.get(
        channel, channel or "—"
    )


def touch_event_key(*, day: int, channel: str) -> str:
    return f"touch:{int(day)}:{(channel or '').strip().lower()}"


def inbound_event_key(*, channel: str, message_id: str) -> str:
    ch = (channel or "").strip().lower()
    mid = (message_id or "").strip()[:96]
    return f"inbound:{ch}:{mid}"


def meeting_event_key(*, meeting_id: int) -> str:
    return f"meeting:{int(meeting_id)}"


def prospect_has_prior_outbound(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.direction == "outbound",
        )
    )
    return int(n or 0) > 0


def _get_or_create_event(db: Session, *, prospect: Prospect, event_key: str) -> CrmSyncEvent:
    row = db.scalars(
        select(CrmSyncEvent).where(
            CrmSyncEvent.prospect_id == prospect.id,
            CrmSyncEvent.event_key == event_key,
        )
    ).first()
    if row is None:
        row = CrmSyncEvent(
            company_id=int(prospect.company_id),
            prospect_id=int(prospect.id),
            event_key=event_key,
        )
        db.add(row)
        db.flush()
    return row


def _mark_attempt(event: CrmSyncEvent) -> None:
    event.last_attempt_at = datetime.now(UTC)


def _touch_note(*, prospect: Prospect, day: int, channel: str, message_body: str | None) -> str:
    return (
        f"[Nexus] Día {day} · {_channel_label(channel)}\n"
        f"{prospect.name or '—'} · {prospect.company_name or '—'}\n"
        f"{_preview(message_body) or '(sin cuerpo)'}"
    )


def _inbound_note(*, prospect: Prospect, channel: str, message_body: str | None) -> str:
    return (
        f"[Nexus] Respondió · {_channel_label(channel)}\n"
        f"{prospect.name or '—'} · {prospect.company_name or '—'}\n"
        f"{_preview(message_body) or '(sin cuerpo)'}"
    )


def _meeting_note(
    *,
    prospect: Prospect,
    scheduled_for: datetime | None,
    title: str | None,
) -> str:
    when = "—"
    if scheduled_for is not None:
        dt = scheduled_for
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        when = dt.strftime("%d/%m/%Y %H:%M UTC")
    return (
        f"[Nexus] Reunión agendada\n"
        f"{prospect.name or '—'} · {prospect.company_name or '—'}\n"
        f"{(title or 'Reunión').strip()}\n"
        f"Fecha: {when}"
    )


def _upsert_contact_ids(
    db: Session,
    prospect: Prospect,
    *,
    company_id: int,
    email: str,
) -> tuple[str | None, str | None]:
    """Upsert contacto en CRMs activos. Devuelve (hubspot_id, salesforce_id)."""
    first, last = _split_name(prospect.name)
    hs_id = getattr(prospect, "hubspot_contact_id", None)
    sf_id = getattr(prospect, "salesforce_contact_id", None)

    if cc.hubspot_active(db, company_id):
        token = cc.get_hubspot_access_token(db, company_id)
        if token:
            new_hs = hubspot.upsert_contact(
                access_token=token,
                email=email,
                first_name=first,
                last_name=last,
                company_name=prospect.company_name,
                job_title=prospect.role,
            )
            if new_hs:
                hs_id = new_hs
                prospect.hubspot_contact_id = new_hs

    if cc.salesforce_active(db, company_id):
        pair = cc.get_salesforce_auth(db, company_id)
        if pair:
            access_token, instance_url = pair
            new_sf = salesforce.upsert_contact(
                access_token=access_token,
                instance_url=instance_url,
                email=email,
                first_name=first,
                last_name=last,
                company_name=prospect.company_name,
                job_title=prospect.role,
            )
            if new_sf:
                sf_id = new_sf
                prospect.salesforce_contact_id = new_sf

    return hs_id, sf_id


def _push_hubspot_activity(
    db: Session,
    *,
    company_id: int,
    contact_id: str | None,
    body: str,
    event: CrmSyncEvent,
) -> None:
    _mark_attempt(event)
    if not cc.hubspot_active(db, company_id):
        return
    if event.hubspot_synced:
        return
    if not contact_id:
        event.hubspot_error = "Sin contact_id HubSpot"
        return
    token = cc.get_hubspot_access_token(db, company_id)
    if not token:
        event.hubspot_error = "Token HubSpot no disponible"
        return
    try:
        ok = hubspot.create_note_for_contact(
            contact_id=contact_id,
            access_token=token,
            body=body,
        )
        if ok:
            event.hubspot_synced = True
            event.hubspot_error = None
        else:
            event.hubspot_error = "HubSpot rechazó la nota"
    except Exception as exc:
        event.hubspot_error = str(exc)[:400]
        _logger.warning("HubSpot activity failed: %s", exc)


def _push_salesforce_activity(
    db: Session,
    *,
    company_id: int,
    contact_id: str | None,
    subject: str,
    body: str,
    event: CrmSyncEvent,
) -> None:
    _mark_attempt(event)
    if not cc.salesforce_active(db, company_id):
        return
    if event.salesforce_synced:
        return
    if not contact_id:
        event.salesforce_error = "Sin contact_id Salesforce"
        return
    pair = cc.get_salesforce_auth(db, company_id)
    if pair is None:
        event.salesforce_error = "Token Salesforce no disponible"
        return
    access_token, instance_url = pair
    try:
        ok = salesforce.create_task_for_contact(
            access_token=access_token,
            instance_url=instance_url,
            contact_id=contact_id,
            subject=subject,
            body=body,
        )
        if ok:
            event.salesforce_synced = True
            event.salesforce_error = None
        else:
            event.salesforce_error = "Salesforce rechazó la tarea"
    except Exception as exc:
        event.salesforce_error = str(exc)[:400]
        _logger.warning("Salesforce activity failed: %s", exc)


def _sync_event(
    db: Session,
    *,
    prospect: Prospect,
    event_key: str,
    note_body: str,
    sf_subject: str,
) -> None:
    company_id = int(prospect.company_id or 0)
    if not company_id:
        return
    if not cc.hubspot_active(db, company_id) and not cc.salesforce_active(db, company_id):
        return
    email = _prospect_email(prospect)
    if not email:
        return

    event = _get_or_create_event(db, prospect=prospect, event_key=event_key)
    if event.hubspot_synced and event.salesforce_synced:
        return

    try:
        hs_id, sf_id = _upsert_contact_ids(db, prospect, company_id=company_id, email=email)
    except Exception as exc:
        err = str(exc)[:400]
        if cc.hubspot_active(db, company_id) and not event.hubspot_synced:
            event.hubspot_error = err
        if cc.salesforce_active(db, company_id) and not event.salesforce_synced:
            event.salesforce_error = err
        _mark_attempt(event)
        _logger.warning("CRM contact upsert failed prospect=%s: %s", prospect.id, exc)
        hs_id = getattr(prospect, "hubspot_contact_id", None)
        sf_id = getattr(prospect, "salesforce_contact_id", None)

    _push_hubspot_activity(
        db,
        company_id=company_id,
        contact_id=hs_id,
        body=note_body,
        event=event,
    )
    _push_salesforce_activity(
        db,
        company_id=company_id,
        contact_id=sf_id,
        subject=sf_subject,
        body=note_body,
        event=event,
    )


def sync_touch_sent(
    db: Session,
    *,
    prospect: Prospect,
    day: int,
    channel: str,
    message_body: str | None,
) -> None:
    """Registra contacto + actividad en CRM tras un toque enviado."""
    note = _touch_note(prospect=prospect, day=day, channel=channel, message_body=message_body)
    _sync_event(
        db,
        prospect=prospect,
        event_key=touch_event_key(day=day, channel=channel),
        note_body=note,
        sf_subject=f"Nexus · Día {day} · {_channel_label(channel)}",
    )


def sync_inbound_reply(
    db: Session,
    *,
    prospect: Prospect,
    channel: str,
    message_id: str,
    message_body: str | None,
) -> None:
    """Registra respuesta inbound en CRM (solo si hubo outbound previo de Nexus)."""
    if not prospect_has_prior_outbound(db, int(prospect.id)):
        return
    note = _inbound_note(prospect=prospect, channel=channel, message_body=message_body)
    _sync_event(
        db,
        prospect=prospect,
        event_key=inbound_event_key(channel=channel, message_id=message_id),
        note_body=note,
        sf_subject=f"Nexus · Respondió · {_channel_label(channel)}",
    )


def sync_meeting_booked(
    db: Session,
    *,
    prospect: Prospect,
    meeting_id: int,
    scheduled_for: datetime | None,
    title: str | None,
) -> None:
    """Registra reunión agendada en CRM."""
    note = _meeting_note(prospect=prospect, scheduled_for=scheduled_for, title=title)
    _sync_event(
        db,
        prospect=prospect,
        event_key=meeting_event_key(meeting_id=meeting_id),
        note_body=note,
        sf_subject="Nexus · Reunión agendada",
    )


def _event_needs_retry(event: CrmSyncEvent, *, hubspot_on: bool, salesforce_on: bool) -> bool:
    if hubspot_on and not event.hubspot_synced and event.hubspot_error:
        return True
    if salesforce_on and not event.salesforce_synced and event.salesforce_error:
        return True
    if hubspot_on and not event.hubspot_synced and not event.hubspot_error:
        return True
    if salesforce_on and not event.salesforce_synced and not event.salesforce_error:
        return True
    return False


def _event_pending(event: CrmSyncEvent, *, hubspot_on: bool, salesforce_on: bool) -> bool:
    if hubspot_on and not event.hubspot_synced:
        return True
    if salesforce_on and not event.salesforce_synced:
        return True
    return False


def _event_has_actionable_failure(
    event: CrmSyncEvent, *, hubspot_on: bool, salesforce_on: bool
) -> bool:
    if not _event_pending(event, hubspot_on=hubspot_on, salesforce_on=salesforce_on):
        return False
    if hubspot_on and event.hubspot_error:
        return True
    if salesforce_on and event.salesforce_error:
        return True
    return False


def _lookup_touch_message_body(db: Session, *, prospect_id: int, channel: str) -> str | None:
    row = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect_id,
            OutreachMessage.channel == channel,
            OutreachMessage.direction == "outbound",
        )
        .order_by(OutreachMessage.id.desc())
    ).first()
    return (row.message if row else None) or None


def _lookup_inbound_message_body(
    db: Session, *, prospect_id: int, channel: str, message_id: str
) -> str | None:
    mid = (message_id or "").strip()
    if not mid:
        return None
    q = select(OutreachMessage).where(
        OutreachMessage.prospect_id == prospect_id,
        OutreachMessage.direction == "inbound",
    )
    if channel == "email":
        q = q.where(OutreachMessage.gmail_message_id == mid)
    elif channel == "linkedin":
        q = q.where(OutreachMessage.linkedin_message_id == mid)
    else:
        q = q.where(OutreachMessage.channel == channel)
    row = db.scalars(q.order_by(OutreachMessage.id.desc())).first()
    return (row.message if row else None) or None


def retry_event(db: Session, event: CrmSyncEvent) -> None:
    prospect = db.get(Prospect, event.prospect_id)
    if prospect is None:
        return
    key = event.event_key or ""
    if key.startswith("touch:"):
        parts = key.split(":", 2)
        if len(parts) >= 3:
            day = int(parts[1])
            channel = parts[2]
            body = _lookup_touch_message_body(db, prospect_id=int(prospect.id), channel=channel)
            sync_touch_sent(
                db,
                prospect=prospect,
                day=day,
                channel=channel,
                message_body=body,
            )
    elif key.startswith("inbound:"):
        parts = key.split(":", 2)
        if len(parts) >= 3:
            channel = parts[1]
            message_id = parts[2]
            body = _lookup_inbound_message_body(
                db,
                prospect_id=int(prospect.id),
                channel=channel,
                message_id=message_id,
            )
            sync_inbound_reply(
                db,
                prospect=prospect,
                channel=channel,
                message_id=message_id,
                message_body=body,
            )
    elif key.startswith("meeting:"):
        meeting_id = int(key.split(":", 1)[1])
        from app.models.meeting import Meeting

        meeting = db.get(Meeting, meeting_id)
        if meeting:
            sync_meeting_booked(
                db,
                prospect=prospect,
                meeting_id=meeting_id,
                scheduled_for=meeting.scheduled_for,
                title=meeting.title,
            )


def retry_pending_for_company(db: Session, company_id: int, *, limit: int = 25) -> dict[str, int]:
    hubspot_on = cc.hubspot_active(db, company_id)
    salesforce_on = cc.salesforce_active(db, company_id)
    if not hubspot_on and not salesforce_on:
        return {"retried": 0, "resolved": 0}

    rows = db.scalars(
        select(CrmSyncEvent)
        .where(CrmSyncEvent.company_id == company_id)
        .order_by(CrmSyncEvent.last_attempt_at.asc().nullsfirst(), CrmSyncEvent.id.asc())
        .limit(limit * 3)
    ).all()

    retried = 0
    resolved = 0
    for row in rows:
        if retried >= limit:
            break
        if not _event_needs_retry(row, hubspot_on=hubspot_on, salesforce_on=salesforce_on):
            continue
        before_hs = row.hubspot_synced
        before_sf = row.salesforce_synced
        retry_event(db, row)
        retried += 1
        if (hubspot_on and row.hubspot_synced and not before_hs) or (
            salesforce_on and row.salesforce_synced and not before_sf
        ):
            resolved += 1
    return {"retried": retried, "resolved": resolved}


def company_sync_status(db: Session, company_id: int, *, limit: int = 8) -> dict:
    hubspot_on = cc.hubspot_active(db, company_id)
    salesforce_on = cc.salesforce_active(db, company_id)

    all_events = db.scalars(
        select(CrmSyncEvent).where(CrmSyncEvent.company_id == company_id)
    ).all()
    pending = sum(
        1
        for ev in all_events
        if _event_pending(ev, hubspot_on=hubspot_on, salesforce_on=salesforce_on)
    )

    failed_q = (
        select(CrmSyncEvent)
        .where(
            CrmSyncEvent.company_id == company_id,
            or_(
                CrmSyncEvent.hubspot_error.isnot(None),
                CrmSyncEvent.salesforce_error.isnot(None),
            ),
        )
        .order_by(CrmSyncEvent.last_attempt_at.desc().nullslast(), CrmSyncEvent.id.desc())
        .limit(limit * 3)
    )
    failed_rows = db.scalars(failed_q).all()

    recent: list[dict] = []
    for ev in failed_rows:
        if not _event_has_actionable_failure(
            ev, hubspot_on=hubspot_on, salesforce_on=salesforce_on
        ):
            continue
        prospect = db.get(Prospect, ev.prospect_id)
        hubspot_error = ev.hubspot_error if hubspot_on else None
        salesforce_error = ev.salesforce_error if salesforce_on else None
        recent.append(
            {
                "event_id": ev.id,
                "prospect_id": ev.prospect_id,
                "prospect_name": (prospect.name if prospect else None) or f"#{ev.prospect_id}",
                "event_key": ev.event_key,
                "hubspot_synced": ev.hubspot_synced,
                "salesforce_synced": ev.salesforce_synced,
                "hubspot_error": hubspot_error,
                "salesforce_error": salesforce_error,
                "last_attempt_at": ev.last_attempt_at.isoformat() if ev.last_attempt_at else None,
            }
        )

    return {
        "hubspot_active": hubspot_on,
        "salesforce_active": salesforce_on,
        "pending_count": pending,
        "failed_recent": recent[:limit],
    }
