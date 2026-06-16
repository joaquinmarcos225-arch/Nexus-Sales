"""
LinkedIn Assisted Layer — MVP asistido con arquitectura lista para extensión Chrome.

Estados (prospect.linkedin_assist_status):
  suggested → prepared → opened → sent
  (abandon desde opened vuelve a suggested con borrador intacto)

La extensión futura podrá reportar eventos con session_id sin cambiar este contrato base.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.linkedin_assisted import LinkedInAssistQueueRead, LinkedInAssistTaskRead
from app.services import followup_engine, openai_service
from app.services.ai_instruction_context import campaign_education_blob
from app.services.multichannel_sequence import (
    _append_log,
    _day_index_one_based,
    _product_payload,
    _prospect_payload,
    _update_group_for_prospect,
)

STATUS_NONE = "none"
STATUS_SUGGESTED = "suggested"
STATUS_PREPARED = "prepared"
STATUS_OPENED = "opened"
STATUS_SENT = "sent"

_DEMO_LI_PATH_RE = (
    re.compile(r"/in/demo[-_]", re.I),
    re.compile(r"/in/test[-_]", re.I),
    re.compile(r"/in/fake[-_]", re.I),
    re.compile(r"/in/mock[-_]", re.I),
    re.compile(r"/in/sample[-_]", re.I),
    re.compile(r"/in/example", re.I),
)


def is_real_linkedin_profile_url(raw: str | None) -> bool:
    url = (raw or "").strip()
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if "linkedin.com" not in host:
        return False
    path = (parsed.path or "").lower()
    if not path.startswith("/in/") and not path.startswith("/sales/"):
        return False
    if any(rx.search(path) or rx.search(url) for rx in _DEMO_LI_PATH_RE):
        return False
    slug = path.removeprefix("/in/").split("/")[0]
    return bool(slug and len(slug) >= 2)


def require_real_linkedin(prospect: Prospect) -> None:
    if not is_real_linkedin_profile_url(prospect.linkedin_url):
        raise ValueError(
            "Este prospecto no tiene un perfil LinkedIn real configurado. "
            "Agregá linkedin.com/in/... válido."
        )


def read_assist_status(prospect: Prospect) -> str:
    raw = (getattr(prospect, "linkedin_assist_status", None) or "").strip().lower()
    if raw in {STATUS_SUGGESTED, STATUS_PREPARED, STATUS_OPENED, STATUS_SENT}:
        return raw
    if getattr(prospect, "linkedin_sdr_marked_sent_at", None):
        return STATUS_SENT
    draft = (prospect.linkedin_assisted_draft or "").strip()
    if not draft:
        return STATUS_NONE
    if getattr(prospect, "linkedin_last_assisted_at", None):
        return STATUS_OPENED
    return STATUS_SUGGESTED


def _set_assist_status(prospect: Prospect, status: str) -> None:
    prospect.linkedin_assist_status = status


def _campaign_payload(campaign: Campaign) -> dict[str, str]:
    from app.services.multichannel_sequence import _campaign_payload as _cp

    return _cp(campaign)


def _conversation_for_prospect(db: Session, prospect_id: int) -> list[OutreachMessage]:
    return list(
        db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect_id)
            .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
        ).all()
    )


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


def _load_campaign(db: Session, prospect: Prospect) -> Campaign:
    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == prospect.campaign_id)
        .options(selectinload(Campaign.product))
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


def mark_draft_suggested(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
    draft: str,
    *,
    log_event: bool = True,
) -> None:
    """Secuencia o prepare: Nexus sugirió contacto LinkedIn."""
    prospect.linkedin_assisted_draft = draft
    if read_assist_status(prospect) != STATUS_SENT:
        _set_assist_status(prospect, STATUS_SUGGESTED)
    if log_event:
        name = prospect.name or f"Prospecto #{prospect.id}"
        _log_activity(
            campaign,
            f"LinkedIn sugerido · mensaje listo para {name}.",
            kind="linkedin_suggested",
        )


def ensure_linkedin_draft(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    draft = (prospect.linkedin_assisted_draft or "").strip()
    if draft:
        return draft
    history = _conversation_for_prospect(db, prospect.id)
    last_inbound = next((m for m in reversed(history) if m.direction == "inbound"), None)
    is_reply = last_inbound is not None
    last_text = (last_inbound.message if last_inbound else "") or ""
    blob = campaign_education_blob(db, campaign)
    draft = openai_service.generate_linkedin_sdr_draft(
        prospect=_prospect_payload(prospect),
        campaign=_campaign_payload(campaign),
        product=_product_payload(campaign),
        education=blob,
        is_reply=is_reply,
        last_prospect_message=last_text,
    )
    mark_draft_suggested(db, prospect, campaign, draft, log_event=True)
    return draft


def begin_assist_session(
    db: Session,
    prospect: Prospect,
    campaign: Campaign,
) -> tuple[str, str]:
    """
    Inicia sesión asistida. Retorna (draft, session_id).
    NO marca enviado — solo prepara + abre + copia (logs separados).
    """
    require_real_linkedin(prospect)
    had_draft = bool((prospect.linkedin_assisted_draft or "").strip())
    draft = ensure_linkedin_draft(db, prospect, campaign)
    name = prospect.name or f"Prospecto #{prospect.id}"
    now = datetime.now(UTC)

    if not had_draft or read_assist_status(prospect) == STATUS_SUGGESTED:
        _set_assist_status(prospect, STATUS_PREPARED)
        _log_activity(
            campaign,
            f"Mensaje preparado para LinkedIn · {name}.",
            kind="linkedin_prepared",
        )

    session_id = str(uuid.uuid4())
    prospect.linkedin_assist_session_id = session_id
    prospect.linkedin_last_assisted_at = now
    _set_assist_status(prospect, STATUS_OPENED)

    _log_activity(
        campaign,
        f"LinkedIn abierto · {name} (esperando envío manual del SDR).",
        kind="linkedin_opened",
    )
    _log_activity(
        campaign,
        f"Mensaje copiado al portapapeles · {name}.",
        kind="linkedin_copy",
    )
    return draft, session_id


def abandon_assist_session(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    name = prospect.name or f"Prospecto #{prospect.id}"
    if (prospect.linkedin_assisted_draft or "").strip():
        _set_assist_status(prospect, STATUS_SUGGESTED)
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    _log_activity(
        campaign,
        f"LinkedIn sin confirmar envío · {name} (sigue en cola).",
        kind="linkedin_pending",
    )
    return STATUS_SUGGESTED


def confirm_linkedin_sent(db: Session, prospect: Prospect) -> str:
    require_real_linkedin(prospect)
    campaign = _load_campaign(db, prospect)
    draft = (prospect.linkedin_assisted_draft or "").strip()
    if not draft:
        raise ValueError("No hay borrador LinkedIn pendiente para este prospecto.")

    name = prospect.name or f"Prospecto #{prospect.id}"
    body = f"[LinkedIn · enviado por SDR]\n{draft}"

    db.add(
        OutreachMessage(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="user",
            message=body,
            channel="linkedin",
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

    prospect.linkedin_sdr_marked_sent_at = datetime.now(UTC)
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_last_assisted_at = None
    prospect.linkedin_assist_session_id = None
    _set_assist_status(prospect, STATUS_SENT)

    day = _day_index_one_based(prospect.sequence_started_at)
    _update_group_for_prospect(
        prospect,
        day,
        _has_pending_followup(db, prospect.id),
    )

    _log_activity(
        campaign,
        f"Mensaje confirmado enviado en LinkedIn · {name}.",
        kind="linkedin_sent",
    )

    return "Envío confirmado en LinkedIn."


def is_queue_eligible(prospect: Prospect) -> bool:
    if not is_real_linkedin_profile_url(prospect.linkedin_url):
        return False
    if not (prospect.linkedin_assisted_draft or "").strip():
        return False
    if getattr(prospect, "linkedin_sdr_marked_sent_at", None):
        return False
    if read_assist_status(prospect) == STATUS_SENT:
        return False
    return True


def build_task_read(prospect: Prospect) -> LinkedInAssistTaskRead:
    status = read_assist_status(prospect)
    return LinkedInAssistTaskRead(
        prospect_id=prospect.id,
        prospect_name=prospect.name or f"Prospecto #{prospect.id}",
        company_name=prospect.company_name,
        linkedin_url=(prospect.linkedin_url or "").strip(),
        message=(prospect.linkedin_assisted_draft or "").strip(),
        assist_status=status,
        session_id=getattr(prospect, "linkedin_assist_session_id", None),
        priority=_priority_for(prospect),
        sequence_group=getattr(prospect, "sequence_group", None),
        opened_at=getattr(prospect, "linkedin_last_assisted_at", None),
        suggested_at=getattr(prospect, "created_at", None),
    )


def build_campaign_queue(db: Session, campaign_id: int) -> LinkedInAssistQueueRead:
    rows = db.scalars(select(Prospect).where(Prospect.campaign_id == campaign_id)).all()
    tasks: list[LinkedInAssistTaskRead] = []
    for p in rows:
        if not is_queue_eligible(p):
            continue
        tasks.append(build_task_read(p))
    priority_order = {"alta": 0, "media": 1, "baja": 2}
    status_order = {STATUS_OPENED: 0, STATUS_PREPARED: 1, STATUS_SUGGESTED: 2}
    tasks.sort(
        key=lambda t: (
            priority_order.get(t.priority, 9),
            status_order.get(t.assist_status, 9),
            t.prospect_name,
        )
    )
    return LinkedInAssistQueueRead(
        campaign_id=campaign_id,
        tasks=tasks,
        total_pending=len(tasks),
    )


# Compat aliases usados por routes previos
def log_assist_session(db: Session, prospect: Prospect, campaign: Campaign, **kwargs) -> None:
    begin_assist_session(db, prospect, campaign)


def log_assist_abandoned(db: Session, prospect: Prospect, campaign: Campaign) -> None:
    abandon_assist_session(db, prospect, campaign)
