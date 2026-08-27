"""Llamadas asistidas — cola SDR con guion del toque (sin marcador automático)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.schemas.call_assisted import CallAssistQueueRead, CallAssistTaskRead
from app.services.whatsapp_cloud_service import sanitize_stored_phone
from app.services.whatsapp_phone_validation import (
    sanitize_landline_phone,
    sanitize_whatsapp_mobile,
)

STATUS_NONE = "none"
STATUS_SUGGESTED = "suggested"
STATUS_DONE = "done"


def read_assist_status(prospect: Prospect) -> str:
    raw = (getattr(prospect, "call_assist_status", None) or "").strip().lower()
    if raw in {STATUS_SUGGESTED, STATUS_DONE}:
        return raw
    if getattr(prospect, "call_sdr_marked_done_at", None) and not (
        prospect.call_assisted_brief or ""
    ).strip():
        return STATUS_DONE
    if (prospect.call_assisted_brief or "").strip():
        return STATUS_SUGGESTED
    return STATUS_NONE


def _set_assist_status(prospect: Prospect, status: str) -> None:
    prospect.call_assist_status = status


def prospect_call_target(prospect: Prospect) -> tuple[str | None, str, str]:
    """Número para llamar + etiqueta + display. Preferimos fijo."""
    landline = sanitize_landline_phone(getattr(prospect, "landline_phone", None))
    if landline:
        digits = re.sub(r"\D", "", landline)
        return digits or None, "landline", landline
    mobile = sanitize_whatsapp_mobile(prospect.whatsapp) or sanitize_whatsapp_mobile(
        prospect.phone
    )
    if mobile:
        digits = re.sub(r"\D", "", mobile)
        return digits or None, "mobile", mobile
    generic = sanitize_stored_phone(prospect.phone)
    if generic:
        digits = re.sub(r"\D", "", generic)
        kind = "mobile" if sanitize_whatsapp_mobile(generic) else "landline"
        return digits or None, kind, generic
    return None, "", ""


def prospect_has_callable_number(prospect: Prospect) -> bool:
    digits, _, _ = prospect_call_target(prospect)
    return bool(digits)


def tel_href_for(digits: str) -> str:
    d = re.sub(r"\D", "", digits or "")
    return f"tel:+{d}" if d else ""


def mark_brief_suggested(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    brief: str,
    *,
    log_event: bool = True,
) -> None:
    from app.services.multichannel_sequence import _append_log

    prospect.call_assisted_brief = (brief or "").strip()
    _set_assist_status(prospect, STATUS_SUGGESTED)
    if log_event:
        name = prospect.name or f"Prospecto #{prospect.id}"
        _append_log(
            campaign,
            f"Llamada programada · {name} (guion listo en cola).",
            kind="call_suggested",
        )
    db.flush()


def queue_call_sequence_touch(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    brief_body: str,
    *,
    log_event: bool = True,
) -> str:
    if not prospect_has_callable_number(prospect):
        return "skip"
    brief = (brief_body or "").strip()
    if not brief:
        return "skip"
    from app.services.prospect_sequence import _clear_assisted_live_queue

    _clear_assisted_live_queue(prospect, "linkedin")
    _clear_assisted_live_queue(prospect, "whatsapp")
    mark_brief_suggested(db, prospect, campaign, brief, log_event=log_event)
    return "call"


def confirm_call_done(db: Session, prospect: Prospect) -> str:
    if not prospect_has_callable_number(prospect):
        raise ValueError("Este prospecto no tiene un número para llamar.")
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None:
        raise ValueError("Campaña no encontrada")
    brief = (prospect.call_assisted_brief or "").strip()
    if not brief:
        if read_assist_status(prospect) == STATUS_DONE:
            return "Llamada ya confirmada."
        raise ValueError("No hay llamada pendiente para este prospecto.")

    from app.models.outreach import OutreachMessage
    from app.services import followup_engine

    name = prospect.name or f"Prospecto #{prospect.id}"
    body = f"[Llamada · confirmada por SDR]\n{brief}"
    db.add(
        OutreachMessage(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="user",
            message=body,
            channel="call",
            direction="outbound",
        )
    )
    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=brief,
    )
    prospect.call_assisted_brief = None
    prospect.call_sdr_marked_done_at = datetime.now(UTC)
    _set_assist_status(prospect, STATUS_DONE)
    from app.services.prospect_sequence import complete_pending_call_sequence_touch

    complete_pending_call_sequence_touch(db, prospect=prospect)
    from app.services.multichannel_sequence import _append_log

    _append_log(campaign, f"Llamada confirmada · {name}.", kind="call_done")
    return "Llamada confirmada."


def is_queue_eligible(prospect: Prospect, campaign=None) -> bool:
    if not prospect_has_callable_number(prospect):
        return False
    if not (prospect.call_assisted_brief or "").strip():
        return False
    if read_assist_status(prospect) == STATUS_DONE:
        return False
    try:
        from app.services.prospect_sequence import (
            _sequence_held_for_conversation,
            next_executable_channel,
        )

        if _sequence_held_for_conversation(prospect):
            return True
        if next_executable_channel(prospect, campaign) != "call":
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def build_task_read(prospect: Prospect) -> CallAssistTaskRead:
    digits, kind, display = prospect_call_target(prospect)
    return CallAssistTaskRead(
        prospect_id=prospect.id,
        prospect_name=prospect.name or f"Prospecto #{prospect.id}",
        company_name=prospect.company_name,
        phone_digits=digits or "",
        phone_display=display,
        phone_kind=kind or "unknown",
        brief=(prospect.call_assisted_brief or "").strip(),
        assist_status=read_assist_status(prospect),
        tel_href=tel_href_for(digits or ""),
        sequence_group=getattr(prospect, "sequence_group", None),
    )


def build_campaign_queue(db: Session, campaign_id: int, viewer=None) -> CallAssistQueueRead:
    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    if viewer is not None and campaign is not None:
        from app.services.campaign_visibility import filter_prospects_for_viewer

        rows = filter_prospects_for_viewer(viewer, campaign, list(rows))

    tasks: list[CallAssistTaskRead] = []
    for p in rows:
        try:
            from app.services.prospect_sequence import ensure_single_assisted_live_queue

            ensure_single_assisted_live_queue(p, campaign)
        except Exception:  # noqa: BLE001
            pass
        if not is_queue_eligible(p, campaign):
            continue
        tasks.append(build_task_read(p))

    tasks.sort(key=lambda t: (t.prospect_name or "").lower())
    return CallAssistQueueRead(
        campaign_id=campaign_id,
        tasks=tasks,
        total_pending=len(tasks),
    )
