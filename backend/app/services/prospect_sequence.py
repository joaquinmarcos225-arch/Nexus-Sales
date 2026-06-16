"""Conexión Prospecto → Outreach → Secuencia SDR 21d."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import normalize_role
from app.models.campaign import Campaign
from app.models.enums import ProspectOwnershipStatus, UserRole
from app.models.outreach import OutreachMessage
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services import prospect_ownership as own
from app.services.ai_instruction_context import campaign_education_blob
from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url
from app.services.lead_sourcing.mvp_outreach_playbook import (
    DEFAULT_MVP_PLAYBOOK,
    lead_available_channels,
    openai_configured,
)
from app.services.lead_sourcing import sdr_playbook_outreach as sdr_pb

logger = logging.getLogger(__name__)

PLAYBOOK_NAME = "SDR 21d MVP"
PLAYBOOK_DAYS = tuple(step.day for step in DEFAULT_MVP_PLAYBOOK)
COOLDOWN_DAYS = own.OWNERSHIP_COOLDOWN_DAYS

TOUCH_PENDIENTE = "pendiente"
TOUCH_GENERADO = "generado"
TOUCH_ENVIADO = "enviado"
TOUCH_RESPONDIDO = "respondido"
TOUCH_FALLIDO = "fallido"
TOUCH_OMITIDO = "omitido"

TOUCH_STATUS_LABELS: dict[str, str] = {
    TOUCH_PENDIENTE: "Pendiente",
    TOUCH_GENERADO: "Generado",
    TOUCH_ENVIADO: "Enviado",
    TOUCH_RESPONDIDO: "Respondido",
    TOUCH_FALLIDO: "Fallido",
    TOUCH_OMITIDO: "Omitido",
}

TERMINAL_TOUCH_STATUSES = frozenset({TOUCH_ENVIADO, TOUCH_RESPONDIDO, TOUCH_OMITIDO})


def _now() -> datetime:
    return datetime.now(UTC)


def _fired_list(prospect: Prospect) -> list[int]:
    raw = getattr(prospect, "sequence_fired_milestones", None) or "[]"
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).isdigit()]
    try:
        return [int(x) for x in json.loads(str(raw)) if str(x).isdigit()]
    except Exception:
        return []


def _append_fired(prospect: Prospect, day: int) -> None:
    days = sorted(set(_fired_list(prospect) + [int(day)]))
    prospect.sequence_fired_milestones = json.dumps(days)


def _touch_log(prospect: Prospect) -> dict[str, dict[str, Any]]:
    raw = getattr(prospect, "sequence_touch_log", None) or "{}"
    try:
        data = json.loads(str(raw))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_touch_log(prospect: Prospect, log: dict[str, dict[str, Any]]) -> None:
    prospect.sequence_touch_log = json.dumps(log, ensure_ascii=False)


def _touch_entry(prospect: Prospect, day: int) -> dict[str, Any]:
    return dict(_touch_log(prospect).get(str(day), {}))


def _set_touch_entry(prospect: Prospect, day: int, **fields: Any) -> None:
    log = _touch_log(prospect)
    entry = dict(log.get(str(day), {}))
    entry.update(fields)
    log[str(day)] = entry
    _save_touch_log(prospect, log)


def _init_touch_log_generado(prospect: Prospect) -> None:
    log: dict[str, dict[str, Any]] = {}
    for step in DEFAULT_MVP_PLAYBOOK:
        log[str(step.day)] = {"status": TOUCH_GENERADO}
    _save_touch_log(prospect, log)


def _playbook_step(day: int):
    return next((s for s in DEFAULT_MVP_PLAYBOOK if s.day == day), None)


def _completed_days(prospect: Prospect) -> set[int]:
    log = _touch_log(prospect)
    fired = set(_fired_list(prospect))
    draft = _draft_by_day(prospect)
    done: set[int] = set()
    for day in PLAYBOOK_DAYS:
        entry = log.get(str(day), {})
        status = entry.get("status")
        if status == TOUCH_OMITIDO:
            done.add(day)
            continue
        if status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO):
            draft_touch = draft.get(day, {})
            _, body = _resolve_step_message(entry=entry, draft_touch=draft_touch, msg=None)
            if body:
                done.add(day)
            continue
        if day in fired:
            draft_touch = draft.get(day, {})
            _, body = _resolve_step_message(entry=entry, draft_touch=draft_touch, msg=None)
            if body:
                done.add(day)
    return done


def next_executable_day(prospect: Prospect) -> int | None:
    if prospect.sequence_started_at is None:
        return None
    done = _completed_days(prospect)
    for day in PLAYBOOK_DAYS:
        if day not in done:
            return day
    return None


def _channel_ready(prospect: Prospect, channel: str) -> bool:
    if channel == "email":
        return _has_valid_email(prospect.email)
    if channel == "linkedin":
        return _has_valid_linkedin(prospect.linkedin_url)
    if channel == "whatsapp":
        return _has_valid_whatsapp(prospect.phone, prospect.whatsapp)
    return False


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    except Exception:
        return None


def _resolve_touch_statuses(
    prospect: Prospect,
    *,
    day: int,
    next_day: int | None,
    entry: dict[str, Any],
    has_draft: bool,
    draft_touch: dict[str, Any] | None = None,
    msg: OutreachMessage | None = None,
) -> tuple[str, str]:
    status = entry.get("status")
    if status == TOUCH_OMITIDO:
        return TOUCH_OMITIDO, "skipped"
    if status == TOUCH_FALLIDO:
        return TOUCH_FALLIDO, "current" if day == next_day else "failed"

    sent_at = _parse_dt(entry.get("sent_at"))
    if status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO) or day in _fired_list(prospect):
        _, body = _resolve_step_message(
            entry=entry,
            draft_touch=draft_touch or {},
            msg=msg,
        )
        if not body:
            return TOUCH_FALLIDO, "current" if day == next_day else "failed"
        inbound = prospect.last_inbound_at
        if inbound is not None and sent_at is not None:
            inbound_cmp = inbound.replace(tzinfo=UTC) if inbound.tzinfo is None else inbound
            if inbound_cmp > sent_at:
                return TOUCH_RESPONDIDO, "respondido"
        if status == TOUCH_RESPONDIDO:
            return TOUCH_RESPONDIDO, "respondido"
        return TOUCH_ENVIADO, "sent"

    if day == next_day and prospect.sequence_started_at is not None:
        touch_st = status or (TOUCH_GENERADO if has_draft else TOUCH_PENDIENTE)
        return touch_st, "current"

    if status == TOUCH_GENERADO or has_draft:
        return TOUCH_GENERADO, "pending"
    return TOUCH_PENDIENTE, "pending"


def _prospect_dict(prospect: Prospect) -> dict[str, str]:
    return {
        "name": prospect.name or "",
        "company_name": prospect.company_name or "",
        "role": prospect.role or "",
        "email": prospect.email or "",
        "linkedin_url": prospect.linkedin_url or "",
        "phone": prospect.phone or "",
        "whatsapp": prospect.whatsapp or "",
        "country": prospect.country or "",
        "industry": prospect.industry or "",
    }


def _campaign_dict(campaign: Campaign, seller: User | None) -> dict[str, str]:
    return {
        "name": campaign.name or "",
        "tone": campaign.tone or "",
        "target_role": campaign.target_role or "",
        "calendar_link": campaign.calendar_link or "",
        "sender_name": (seller.name if seller else "") or "",
        "brand_name": campaign.name or "",
    }


def _product_dict(product: Product | None) -> dict[str, str]:
    if product is None:
        return {"name": "", "description": "", "value_proposition": ""}
    return {
        "name": product.name or "",
        "description": product.description or "",
        "value_proposition": product.value_proposition or "",
    }


def _parse_playbook_draft_raw(raw: str | None) -> list[dict[str, Any]] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return [t for t in data if isinstance(t, dict)]


def _usable_draft_touches(prospect: Prospect) -> dict[int, dict[str, Any]]:
    parsed = _parse_playbook_draft_raw(getattr(prospect, "sequence_playbook_draft", None))
    if not parsed:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for touch in parsed:
        day = touch.get("day")
        if day is None:
            continue
        try:
            day_int = int(day)
        except (TypeError, ValueError):
            continue
        if day_int in PLAYBOOK_DAYS:
            out[day_int] = touch
    return out


def _draft_raw_present(prospect: Prospect) -> bool:
    raw = getattr(prospect, "sequence_playbook_draft", None)
    return bool(raw and str(raw).strip())


def _has_playbook_draft(prospect: Prospect) -> bool:
    return len(_usable_draft_touches(prospect)) > 0


def _is_corrupt_draft_state(prospect: Prospect) -> bool:
    """JSON de borrador presente pero sin toques utilizables."""
    return _draft_raw_present(prospect) and not _has_playbook_draft(prospect)


def _touch_log_has_entries(prospect: Prospect) -> bool:
    return bool(_touch_log(prospect))


def _sequence_status_label(prospect: Prospect) -> str:
    status = own.effective_ownership_status(prospect)
    if prospect.sequence_started_at is None:
        if _has_playbook_draft(prospect):
            return "borrador_listo"
        if _is_corrupt_draft_state(prospect) or _touch_log_has_entries(prospect):
            return "borrador_corrupto"
        return "sin_secuencia"
    if status == ProspectOwnershipStatus.en_secuencia.value:
        return "en_curso"
    if status == ProspectOwnershipStatus.secuencia_finalizada.value:
        return "finalizada"
    return status


def build_sequence_debug(prospect: Prospect) -> dict[str, Any]:
    usable = _usable_draft_touches(prospect)
    log = _touch_log(prospect)
    started = prospect.sequence_started_at is not None
    has_usable_draft = len(usable) > 0
    return {
        "prospect_id": prospect.id,
        "ownership_status": own.effective_ownership_status(prospect),
        "sequence_status": _sequence_status_label(prospect),
        "sequence_started_at": prospect.sequence_started_at,
        "has_draft_raw": _draft_raw_present(prospect),
        "has_usable_draft": has_usable_draft,
        "draft_is_corrupt": _is_corrupt_draft_state(prospect),
        "has_draft": has_usable_draft,
        "draft_touch_count": len(usable),
        "touch_log_entries": len(log),
        "has_touches": len(log) > 0,
        "has_timeline": started or len(log) > 0 or has_usable_draft,
        "playbook_name": getattr(prospect, "playbook_name", None),
        "sequence_id": None,
    }


def reconcile_sequence_state(
    db: Session,
    prospect: Prospect,
    *,
    commit: bool = False,
) -> dict[str, Any]:
    """
    Limpia borradores huérfanos o corruptos (sin secuencia iniciada).
    No borra datos de secuencias en curso o finalizadas.
    """
    if prospect.sequence_started_at is not None:
        return build_sequence_debug(prospect)

    changed = False
    if _is_corrupt_draft_state(prospect):
        prospect.sequence_playbook_draft = None
        changed = True

    orphan_log = _touch_log_has_entries(prospect) and not _has_playbook_draft(prospect)
    if orphan_log:
        prospect.sequence_touch_log = None
        if not _has_playbook_draft(prospect):
            prospect.playbook_name = None
        changed = True

    if changed and commit:
        db.commit()
        db.refresh(prospect)

    return build_sequence_debug(prospect)


def clear_sequence_draft(db: Session, *, prospect: Prospect) -> dict[str, Any]:
    """Elimina borrador y touch log (solo si la secuencia no fue iniciada)."""
    if prospect.sequence_started_at is not None:
        raise HTTPException(
            status_code=400,
            detail="No se puede resetear el borrador con la secuencia en curso",
        )
    prospect.sequence_playbook_draft = None
    prospect.sequence_touch_log = None
    prospect.playbook_name = None
    db.commit()
    db.refresh(prospect)
    return build_sequence_debug(prospect)


def is_own_prospect(user: User, prospect: Prospect) -> bool:
    return prospect.owner_user_id is not None and prospect.owner_user_id == user.id


def can_manage_outreach(user: User, prospect: Prospect) -> bool:
    """SDR/Manager trabajan outreach solo de prospectos que tomaron."""
    if normalize_role(user.role) == UserRole.gerente:
        return False
    if user.company_id != prospect.company_id:
        return False
    status = own.effective_ownership_status(prospect)
    if status in (
        ProspectOwnershipStatus.libre.value,
        ProspectOwnershipStatus.liberado.value,
    ):
        return False
    return prospect.owner_user_id == user.id


def _resolve_campaign(db: Session, prospect: Prospect) -> Campaign | None:
    if not prospect.campaign_id:
        return None
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None or campaign.company_id != prospect.company_id:
        return None
    return campaign


def _resolve_product(db: Session, campaign: Campaign | None) -> Product | None:
    if campaign is None or not campaign.product_id:
        return None
    product = db.get(Product, campaign.product_id)
    return product


def _prospect_channels(prospect: Prospect) -> set[str]:
    return lead_available_channels(
        email=prospect.email,
        linkedin_url=prospect.linkedin_url,
        phone=prospect.phone,
        whatsapp_number=prospect.whatsapp,
    )


def _has_valid_email(email: str | None) -> bool:
    return bool((email or "").strip()) and "@" in (email or "")


def _has_valid_linkedin(linkedin_url: str | None) -> bool:
    return is_personal_linkedin_url(linkedin_url)


def _has_valid_whatsapp(phone: str | None, whatsapp: str | None) -> bool:
    return bool((whatsapp or phone or "").strip())


def _has_valid_contact(prospect: Prospect) -> bool:
    return (
        _has_valid_email(prospect.email)
        or _has_valid_linkedin(prospect.linkedin_url)
        or _has_valid_whatsapp(prospect.phone, prospect.whatsapp)
    )


CHANNEL_LABELS: dict[str, str] = {
    "email": "Email",
    "linkedin": "LinkedIn",
    "whatsapp": "WhatsApp",
}

CHANNELS_REQUIRED = 2
CHANNELS_TOTAL = 3


def _build_channels_detail(prospect: Prospect) -> list[dict[str, Any]]:
    email_ok = _has_valid_email(prospect.email)
    linkedin_ok = _has_valid_linkedin(prospect.linkedin_url)
    whatsapp_ok = _has_valid_whatsapp(prospect.phone, prospect.whatsapp)
    return [
        {
            "key": "email",
            "label": CHANNEL_LABELS["email"],
            "ok": email_ok,
            "detail": prospect.email if email_ok else "Sin email válido",
        },
        {
            "key": "linkedin",
            "label": CHANNEL_LABELS["linkedin"],
            "ok": linkedin_ok,
            "detail": prospect.linkedin_url if linkedin_ok else "Sin perfil LinkedIn personal",
        },
        {
            "key": "whatsapp",
            "label": CHANNEL_LABELS["whatsapp"],
            "ok": whatsapp_ok,
            "detail": (prospect.whatsapp or prospect.phone) if whatsapp_ok else "Sin teléfono/WhatsApp",
        },
    ]


def _format_channels_summary(*, channel_count: int, available_channels: list[str]) -> str:
    detected = ", ".join(CHANNEL_LABELS.get(c, c) for c in sorted(available_channels)) or "ninguno"
    return (
        f"{channel_count}/{CHANNELS_TOTAL} canales válidos (mínimo {CHANNELS_REQUIRED}). "
        f"Detectados: {detected}"
    )


def _format_readiness_block_detail(readiness: dict[str, Any]) -> str:
    parts: list[str] = []
    channel_count = int(readiness.get("channel_count") or 0)
    channels = readiness.get("available_channels") or []
    if channel_count < CHANNELS_REQUIRED:
        parts.append(
            f"Faltan canales: {channel_count}/{CHANNELS_TOTAL} válidos "
            f"(se requieren al menos {CHANNELS_REQUIRED})"
        )
    if not readiness.get("campaign"):
        parts.append("falta campaña asignada")
    product = readiness.get("product")
    if readiness.get("campaign") and product is None:
        parts.append("la campaña no tiene producto asociado")
    prospect = readiness.get("prospect")
    if prospect is not None and not _has_valid_contact(prospect):
        parts.append("falta al menos un dato de contacto (email, LinkedIn o teléfono)")
    if parts:
        summary = _format_channels_summary(channel_count=channel_count, available_channels=channels)
        return f"{'. '.join(p.capitalize() for p in parts)}. {summary}"
    if readiness.get("missing_summary"):
        return str(readiness["missing_summary"])
    return _format_channels_summary(channel_count=channel_count, available_channels=channels)


def explain_generate_sequence_block(
    user: User,
    prospect: Prospect,
    *,
    readiness: dict[str, Any] | None = None,
) -> str | None:
    """Motivo legible si no se puede generar secuencia; None si está permitido."""
    if not can_manage_outreach(user, prospect):
        return "No tenés permisos para gestionar outreach de este prospecto"
    status = own.effective_ownership_status(prospect)
    if status != ProspectOwnershipStatus.tomado.value:
        label = status.replace("_", " ")
        return (
            f"El prospecto debe estar Tomado para generar la secuencia "
            f"(estado actual: {label}). Tomalo desde la bandeja primero."
        )
    if _is_corrupt_draft_state(prospect):
        return None
    if _has_playbook_draft(prospect):
        return (
            "Ya hay una secuencia generada — usá «Ver secuencia» o «Regenerar secuencia» "
            "si el borrador quedó vacío o corrupto"
        )
    if readiness is not None and not _readiness_is_ready(readiness):
        return _format_readiness_block_detail(readiness)
    return None


def explain_start_sequence_block(
    user: User,
    prospect: Prospect,
    *,
    readiness: dict[str, Any] | None = None,
) -> str | None:
    """Motivo legible si no se puede iniciar secuencia; None si está permitido."""
    if not can_manage_outreach(user, prospect):
        return "No tenés permisos para gestionar outreach de este prospecto"
    status = own.effective_ownership_status(prospect)
    if status != ProspectOwnershipStatus.tomado.value:
        label = status.replace("_", " ")
        return f"El prospecto debe estar Tomado para iniciar (estado actual: {label})"
    if not _has_playbook_draft(prospect):
        return "Generá la secuencia antes de iniciarla (falta borrador del playbook)"
    if prospect.sequence_started_at is not None:
        return "La secuencia ya fue iniciada"
    if readiness is not None and not _readiness_is_ready(readiness):
        return _format_readiness_block_detail(readiness)
    return None


def assess_outreach_readiness(db: Session, *, prospect: Prospect) -> dict[str, Any]:
    campaign = _resolve_campaign(db, prospect)
    product = _resolve_product(db, campaign)
    channels = sorted(_prospect_channels(prospect))
    channel_count = len(channels)
    channels_detail = _build_channels_detail(prospect)
    channels_ok = channel_count >= CHANNELS_REQUIRED

    checklist = [
        {
            "key": "campaign",
            "label": "Campaña",
            "ok": campaign is not None,
            "optional": False,
            "detail": campaign.name if campaign else "Asigná una campaña de origen",
        },
        {
            "key": "product",
            "label": "Producto",
            "ok": product is not None,
            "optional": False,
            "detail": product.name if product else "La campaña debe tener producto asociado",
        },
        {
            "key": "contact",
            "label": "Contacto",
            "ok": _has_valid_contact(prospect),
            "optional": False,
            "detail": "Al menos email, LinkedIn o teléfono/WhatsApp",
        },
        {
            "key": "channels_minimum",
            "label": f"Mínimo {CHANNELS_REQUIRED} canales",
            "ok": channels_ok,
            "optional": False,
            "detail": _format_channels_summary(channel_count=channel_count, available_channels=channels),
        },
        {
            "key": "playbook",
            "label": "Playbook",
            "ok": True,
            "optional": False,
            "detail": PLAYBOOK_NAME,
        },
    ]

    is_ready = (
        campaign is not None
        and product is not None
        and _has_valid_contact(prospect)
        and channels_ok
    )

    prep_action: str | None = None
    missing: list[str] = []
    if not campaign:
        missing.append("campaña")
    if not product:
        missing.append("producto")
    if not _has_valid_contact(prospect):
        missing.append("contacto (email, LinkedIn o teléfono)")
    if not channels_ok:
        missing.append(
            f"al menos {CHANNELS_REQUIRED} canales válidos "
            f"({channel_count}/{CHANNELS_TOTAL} detectados)"
        )

    if not is_ready:
        if not campaign or not product:
            prep_action = "complete"
        else:
            prep_action = "enrich"

    missing_summary = None
    if missing:
        missing_summary = "Falta: " + ", ".join(missing)

    return {
        "is_ready": is_ready,
        "channel_count": channel_count,
        "channels_required": CHANNELS_REQUIRED,
        "channels_total": CHANNELS_TOTAL,
        "channels_detail": channels_detail,
        "channels_summary": _format_channels_summary(
            channel_count=channel_count, available_channels=channels
        ),
        "prep_action": prep_action,
        "missing_summary": missing_summary,
        "checklist": checklist,
        "available_channels": channels,
        "campaign": campaign,
        "product": product,
        "prospect": prospect,
    }


def _readiness_api_payload(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "is_ready": readiness["is_ready"],
        "channel_count": readiness["channel_count"],
        "channels_required": readiness["channels_required"],
        "channels_total": readiness["channels_total"],
        "channels_summary": readiness["channels_summary"],
        "channels_detail": readiness["channels_detail"],
        "prep_action": readiness["prep_action"],
        "missing_summary": readiness["missing_summary"],
        "checklist": readiness["checklist"],
    }


def _readiness_is_ready(readiness: dict[str, Any]) -> bool:
    return bool(readiness.get("is_ready"))


def can_generate_sequence(user: User, prospect: Prospect, *, readiness: dict[str, Any] | None = None) -> bool:
    if not can_manage_outreach(user, prospect):
        return False
    status = own.effective_ownership_status(prospect)
    if status != ProspectOwnershipStatus.tomado.value:
        return False
    if _has_playbook_draft(prospect):
        return False
    if readiness is not None and not _readiness_is_ready(readiness):
        return False
    return True


def can_complete_outreach(user: User, prospect: Prospect, *, readiness: dict[str, Any] | None = None) -> bool:
    if not can_manage_outreach(user, prospect):
        return False
    status = own.effective_ownership_status(prospect)
    if status != ProspectOwnershipStatus.tomado.value:
        return False
    if _has_playbook_draft(prospect):
        return False
    if readiness is None:
        return True
    return not _readiness_is_ready(readiness)


def can_view_sequence(user: User, prospect: Prospect) -> bool:
    if not can_manage_outreach(user, prospect):
        return False
    status = own.effective_ownership_status(prospect)
    if status == ProspectOwnershipStatus.en_secuencia.value:
        return True
    if status == ProspectOwnershipStatus.tomado.value and _has_playbook_draft(prospect):
        return True
    return False


def can_start_outreach(user: User, prospect: Prospect) -> bool:
    return can_generate_sequence(user, prospect)


def can_start_sequence(user: User, prospect: Prospect, *, readiness: dict[str, Any] | None = None) -> bool:
    if not can_manage_outreach(user, prospect):
        return False
    status = own.effective_ownership_status(prospect)
    if status != ProspectOwnershipStatus.tomado.value:
        return False
    if not _has_playbook_draft(prospect):
        return False
    if prospect.sequence_started_at is not None:
        return False
    if readiness is not None and not _readiness_is_ready(readiness):
        return False
    return True


def compute_next_touch(prospect: Prospect) -> tuple[datetime | None, str | None]:
    if prospect.sequence_started_at is None:
        return None, None
    done = _completed_days(prospect)
    pending = [d for d in PLAYBOOK_DAYS if d not in done]
    if not pending:
        return None, "Secuencia completa"
    next_day = pending[0]
    start = prospect.sequence_started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    next_at = start + timedelta(days=max(0, next_day - 1))
    channel = next(
        (s.channel for s in DEFAULT_MVP_PLAYBOOK if s.day == next_day),
        "email",
    )
    return next_at, f"Día {next_day} · {channel}"


def estimated_release_at(prospect: Prospect) -> datetime | None:
    status = own.effective_ownership_status(prospect)
    if status == ProspectOwnershipStatus.secuencia_finalizada.value:
        return prospect.ownership_cooldown_until
    if prospect.sequence_started_at and status == ProspectOwnershipStatus.en_secuencia.value:
        start = prospect.sequence_started_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        return start + timedelta(days=42 + COOLDOWN_DAYS)
    return own.release_at(prospect)


def sequence_current_label(prospect: Prospect) -> str | None:
    status = own.effective_ownership_status(prospect)
    if status == ProspectOwnershipStatus.tomado.value and not prospect.sequence_started_at:
        return "Pendiente de iniciar"
    if status == ProspectOwnershipStatus.en_secuencia.value:
        group = (prospect.sequence_group or "contactado").replace("_", " ")
        return f"En curso · {group}"
    if status == ProspectOwnershipStatus.secuencia_finalizada.value:
        return "Finalizada · cooldown"
    return own.last_sequence_label(prospect)


def last_touch_at(prospect: Prospect) -> datetime | None:
    return (
        prospect.last_outbound_at
        or prospect.last_followup_at
        or prospect.linkedin_sdr_marked_sent_at
    )


def _draft_by_day(prospect: Prospect) -> dict[int, dict[str, Any]]:
    return _usable_draft_touches(prospect)


def _scheduled_at(prospect: Prospect, day: int) -> datetime | None:
    start = prospect.sequence_started_at
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start + timedelta(days=max(0, day - 1))


def _status_label(status: str, touch_status: str | None = None) -> str:
    if touch_status and touch_status in TOUCH_STATUS_LABELS:
        return TOUCH_STATUS_LABELS[touch_status]
    return {
        "sent": "Enviado",
        "respondido": "Respondido",
        "current": "Próximo",
        "pending": "Pendiente",
        "failed": "Fallido",
        "skipped": "Omitido",
    }.get(status, status)


def _message_preview(body: str | None, limit: int = 220) -> str | None:
    if not body:
        return None
    text = body.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def build_sequence_tracking(db: Session, *, prospect: Prospect) -> dict[str, Any]:
    done = _completed_days(prospect)
    next_day = next_executable_day(prospect)
    draft = _draft_by_day(prospect)
    start = prospect.sequence_started_at
    log = _touch_log(prospect)

    outbound: list[OutreachMessage] = []
    if start is not None:
        start_cmp = start.replace(tzinfo=UTC) if start.tzinfo is None else start
        rows = db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.direction == "outbound",
            )
            .order_by(OutreachMessage.created_at.asc())
        ).all()
        outbound = [
            m
            for m in rows
            if (m.created_at.replace(tzinfo=UTC) if m.created_at.tzinfo is None else m.created_at)
            >= start_cmp
        ]

    msg_by_id: dict[int, OutreachMessage] = {m.id: m for m in outbound}
    msg_by_day: dict[int, OutreachMessage] = {}
    for day in PLAYBOOK_DAYS:
        entry = log.get(str(day), {})
        msg_id = entry.get("message_id")
        if msg_id and int(msg_id) in msg_by_id:
            msg_by_day[day] = msg_by_id[int(msg_id)]

    fired_playbook = [d for d in PLAYBOOK_DAYS if d in done and d not in msg_by_day]
    orphan_msgs = [m for m in outbound if m.id not in {x.id for x in msg_by_day.values()}]
    for i, day in enumerate(fired_playbook):
        if day not in msg_by_day and i < len(orphan_msgs):
            msg_by_day[day] = orphan_msgs[i]

    steps: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for step in DEFAULT_MVP_PLAYBOOK:
        day = step.day
        draft_touch = draft.get(day, {})
        entry = log.get(str(day), {})
        msg = msg_by_day.get(day)
        touch_status, ui_status = _resolve_touch_statuses(
            prospect,
            day=day,
            next_day=next_day,
            entry=entry,
            has_draft=bool(draft_touch.get("body_preview")),
            draft_touch=draft_touch,
            msg=msg,
        )

        subject, body = _resolve_step_message(entry=entry, draft_touch=draft_touch, msg=msg)
        plain_body = entry.get("body") or draft_touch.get("body")
        sent_at = _parse_dt(entry.get("sent_at")) or (msg.created_at if msg else None)
        can_execute = (
            day == next_day
            and prospect.sequence_started_at is not None
            and not bool(prospect.sequence_paused)
            and touch_status not in TERMINAL_TOUCH_STATUSES
            and ui_status in ("current", "failed")
        )
        can_skip = can_execute

        openai_last_error = entry.get("openai_last_error")
        generation_context = entry.get("generation_context")
        step_data = {
            "day": day,
            "channel": step.channel,
            "objective": draft_touch.get("objective") or step.objective,
            "touch_status": touch_status,
            "status": ui_status,
            "status_label": _status_label(ui_status, touch_status),
            "scheduled_at": _scheduled_at(prospect, day),
            "sent_at": sent_at,
            "subject": subject,
            "body": plain_body if plain_body and not _is_placeholder_message(plain_body) else None,
            "message_body": body,
            "message_preview": _message_preview(body),
            "message_id": entry.get("message_id") or (msg.id if msg else None),
            "error_message": entry.get("error"),
            "validation_rejection": entry.get("validation_rejection"),
            "openai_last_error": openai_last_error,
            "generation_context": generation_context,
            "fallback_test": bool(entry.get("fallback_test")),
            "can_execute": can_execute,
            "can_skip": can_skip,
        }
        steps.append(step_data)
        if touch_status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO, TOUCH_OMITIDO, TOUCH_FALLIDO):
            history.append(step_data)

    next_at, next_label = compute_next_touch(prospect)
    stored_next = prospect.next_touch_at or next_at
    current_day = next_day

    last_response_class: str | None = None
    last_response_class_label: str | None = None
    last_reply_objective: str | None = None
    last_reply_objective_label: str | None = None
    last_response_is_testing: bool = False
    suggested_reply: str | None = None
    last_auto_sent: bool = False
    last_classification_confidence: float | None = None
    last_escalation_reason: str | None = None
    last_delivery_mode: str | None = None
    for day_key in reversed(list(PLAYBOOK_DAYS)):
        entry = log.get(str(day_key), {})
        if entry.get("response_class"):
            last_response_class = str(entry.get("response_class"))
            last_response_class_label = str(entry.get("response_class_label") or "")
            last_reply_objective = str(entry.get("reply_objective") or "") or None
            last_reply_objective_label = str(entry.get("reply_objective_label") or "") or None
            last_response_is_testing = bool(entry.get("testing"))
            suggested_reply = str(entry.get("suggested_reply") or "").strip() or None
            last_auto_sent = bool(entry.get("auto_sent"))
            conf = entry.get("classification_confidence")
            last_classification_confidence = float(conf) if conf is not None else None
            last_escalation_reason = entry.get("escalation_reason")
            last_delivery_mode = entry.get("delivery_mode")
            break

    from app.services.commercial_conversation_agent import conversation_state_label

    conv_state = getattr(prospect, "conversation_state", None) or "sin_conversacion"

    conversation: list[dict[str, Any]] = []
    if start is not None:
        start_cmp = start.replace(tzinfo=UTC) if start.tzinfo is None else start
        conv_rows = db.scalars(
            select(OutreachMessage)
            .where(OutreachMessage.prospect_id == prospect.id)
            .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
        ).all()
        conversation = [
            {
                "id": m.id,
                "prospect_id": m.prospect_id,
                "campaign_id": m.campaign_id,
                "sender_type": m.sender_type,
                "message": m.message,
                "channel": m.channel,
                "direction": m.direction,
                "is_testing": bool(getattr(m, "is_testing", False)),
                "created_at": m.created_at,
            }
            for m in conv_rows
            if (m.created_at.replace(tzinfo=UTC) if m.created_at.tzinfo is None else m.created_at)
            >= start_cmp
        ]

    return {
        "prospect_id": prospect.id,
        "prospect_name": prospect.name,
        "prospect_company": prospect.company_name,
        "playbook_name": getattr(prospect, "playbook_name", None) or PLAYBOOK_NAME,
        "ownership_status": own.effective_ownership_status(prospect),
        "sequence_started_at": prospect.sequence_started_at,
        "sequence_paused": bool(prospect.sequence_paused),
        "sequence_state": getattr(prospect, "sequence_state", None),
        "prospect_status": getattr(prospect, "status", None),
        "current_day": current_day,
        "current_day_label": f"Día {current_day}" if current_day else None,
        "next_touch_at": stored_next,
        "next_touch_label": next_label,
        "last_response_class": last_response_class,
        "last_response_class_label": last_response_class_label,
        "last_reply_objective": last_reply_objective,
        "last_reply_objective_label": last_reply_objective_label,
        "last_response_is_testing": last_response_is_testing,
        "suggested_reply": suggested_reply,
        "conversation_state": conv_state,
        "conversation_state_label": conversation_state_label(conv_state),
        "last_auto_sent": last_auto_sent,
        "last_classification_confidence": last_classification_confidence,
        "last_escalation_reason": last_escalation_reason,
        "last_delivery_mode": last_delivery_mode,
        "steps": steps,
        "history": [s for s in steps if s["touch_status"] in (TOUCH_ENVIADO, TOUCH_RESPONDIDO)],
        "conversation": conversation,
        "testing": _sequence_testing_config(),
    }


def list_active_sequences(db: Session, *, company_id: int, user: User) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(Prospect)
        .where(Prospect.company_id == company_id)
        .order_by(Prospect.next_touch_at.asc().nullslast(), Prospect.id.desc())
    ).all()
    role = normalize_role(user.role)
    summaries: list[dict[str, Any]] = []
    for prospect in rows:
        status = own.effective_ownership_status(prospect)
        if status != ProspectOwnershipStatus.en_secuencia.value:
            continue
        if role == UserRole.sdr and prospect.owner_user_id != user.id:
            continue
        tracking = build_sequence_tracking(db, prospect=prospect)
        summaries.append(
            {
                "prospect_id": prospect.id,
                "prospect_name": prospect.name,
                "company_name": prospect.company_name,
                "ownership_status": status,
                "current_day": tracking["current_day"],
                "current_day_label": tracking["current_day_label"],
                "next_touch_label": tracking["next_touch_label"],
                "next_touch_at": tracking["next_touch_at"],
            }
        )
    return summaries


def sequence_tracking_fields(prospect: Prospect) -> dict[str, Any]:
    next_at, next_label = compute_next_touch(prospect)
    stored_next = prospect.next_touch_at or next_at
    current_day = next_executable_day(prospect)
    return {
        "sequence_current_label": sequence_current_label(prospect),
        "sequence_current_day": current_day,
        "sequence_current_day_label": f"Día {current_day}" if current_day else None,
        "next_touch_at": stored_next,
        "next_touch_label": next_label,
        "last_touch_at": last_touch_at(prospect),
        "sequence_start_at": prospect.sequence_started_at,
        "sequence_end_at": prospect.sequence_completed_at,
        "estimated_release_at": estimated_release_at(prospect),
        "playbook_name": getattr(prospect, "playbook_name", None) or PLAYBOOK_NAME,
        "has_playbook_draft": _has_playbook_draft(prospect),
    }


def outreach_action_flags(
    user: User,
    prospect: Prospect,
    *,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ready = _readiness_is_ready(readiness) if readiness is not None else False
    prep_action = readiness.get("prep_action") if readiness else None
    missing_summary = readiness.get("missing_summary") if readiness else None
    return {
        "is_own_prospect": is_own_prospect(user, prospect),
        "can_start_outreach": can_generate_sequence(user, prospect, readiness=readiness),
        "can_generate_sequence": can_generate_sequence(user, prospect, readiness=readiness),
        "can_view_sequence": can_view_sequence(user, prospect),
        "can_start_sequence": can_start_sequence(user, prospect, readiness=readiness),
        "can_complete_outreach": can_complete_outreach(user, prospect, readiness=readiness),
        "outreach_ready": ready,
        "outreach_prep_action": prep_action,
        "outreach_missing_summary": missing_summary,
        "generate_sequence_block_reason": explain_generate_sequence_block(
            user, prospect, readiness=readiness
        ),
        "start_sequence_block_reason": explain_start_sequence_block(
            user, prospect, readiness=readiness
        ),
    }


def get_saved_sequence_preview(prospect: Prospect) -> dict[str, Any]:
    usable = _usable_draft_touches(prospect)
    if not usable:
        if _is_corrupt_draft_state(prospect):
            raise HTTPException(
                status_code=404,
                detail="Borrador de secuencia corrupto o vacío — regenerá la secuencia",
            )
        raise HTTPException(status_code=404, detail="No hay secuencia generada para este prospecto")
    touches = [usable[day] for day in PLAYBOOK_DAYS if day in usable]
    return {
        "prospect_id": prospect.id,
        "playbook_name": getattr(prospect, "playbook_name", None) or PLAYBOOK_NAME,
        "touches": touches,
    }


def build_outreach_context(db: Session, *, prospect: Prospect, user: User) -> dict[str, Any]:
    reconcile_sequence_state(db, prospect, commit=True)
    readiness = assess_outreach_readiness(db, prospect=prospect)
    campaign = readiness.get("campaign")
    product = readiness.get("product")
    seller = db.get(User, campaign.seller_id) if campaign and campaign.seller_id else None
    channels = readiness.get("available_channels") or []
    allowed = (
        coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
        if campaign
        else []
    )

    campaign_rows = db.scalars(
        select(Campaign)
        .where(Campaign.company_id == prospect.company_id)
        .order_by(Campaign.name.asc())
    ).all()
    campaign_options = []
    for row in campaign_rows:
        prod = db.get(Product, row.product_id) if row.product_id else None
        campaign_options.append(
            {
                "id": row.id,
                "name": row.name,
                "product_name": prod.name if prod else None,
            }
        )

    return {
        "prospect_id": prospect.id,
        "campaign_id": campaign.id if campaign else prospect.campaign_id,
        "company_id": prospect.company_id,
        "prospect_name": prospect.name,
        "prospect_company": prospect.company_name,
        "prospect_email": prospect.email,
        "prospect_linkedin": prospect.linkedin_url,
        "prospect_phone": prospect.phone,
        "prospect_whatsapp": prospect.whatsapp,
        "prospect_company_website": prospect.company_website,
        "ownership_status": own.effective_ownership_status(prospect),
        "owner_user_id": prospect.owner_user_id,
        "campaign_name": campaign.name if campaign else None,
        "product_name": product.name if product else None,
        "product_description": product.description if product else None,
        "playbook_name": PLAYBOOK_NAME,
        "available_channels": channels,
        "campaign_channels": allowed,
        "seller_name": seller.name if seller else None,
        "readiness": _readiness_api_payload(readiness),
        "campaign_options": campaign_options,
        "testing": _sequence_testing_config(),
        "sequence_debug": build_sequence_debug(prospect),
        **outreach_action_flags(user, prospect, readiness=readiness),
    }


def _sequence_testing_config() -> dict[str, Any]:
    from app.services import outreach_metrics as om

    return om.outreach_simulation_config()


def _template_body(step_day: int, channel: str, prospect: Prospect, product: Product | None) -> str:
    pname = product.name if product else "nuestra solución"
    return (
        f"[Vista previa Día {step_day} · {channel}] "
        f"Mensaje personalizado para {prospect.name} ({prospect.company_name}) "
        f"sobre {pname}. Se generará con IA al ejecutar el toque."
    )


def _is_placeholder_message(text: str | None) -> bool:
    if not text or not str(text).strip():
        return True
    normalized = str(text).strip()
    return (
        normalized.startswith("[Vista previa")
        or "Se generará con IA" in normalized
    )


def _persist_touch_draft(prospect: Prospect, draft: dict[int, dict[str, Any]], content: dict[str, Any]) -> None:
    day = int(content["day"])
    draft[day] = {
        **draft.get(day, {}),
        **content,
        "body_preview": content.get("body_preview") or _message_preview(content.get("message_body")),
    }
    touches_list = []
    for playbook_step in DEFAULT_MVP_PLAYBOOK:
        touch = draft.get(playbook_step.day)
        if touch:
            touches_list.append(touch)
    prospect.sequence_playbook_draft = json.dumps(touches_list, ensure_ascii=False)


def _resolve_step_message(
    *,
    entry: dict[str, Any],
    draft_touch: dict[str, Any],
    msg: OutreachMessage | None,
) -> tuple[str | None, str | None]:
    subject = entry.get("subject") or draft_touch.get("subject")
    candidates = [
        entry.get("message_body"),
        draft_touch.get("message_body"),
        draft_touch.get("body"),
        msg.message if msg else None,
        draft_touch.get("body_preview"),
    ]
    body: str | None = None
    for candidate in candidates:
        if candidate and not _is_placeholder_message(str(candidate)):
            body = str(candidate)
            break
    return (str(subject).strip() if subject else None), body


def _raise_sdr_generation_error(exc: Exception) -> None:
    from app.schemas.mvp_outreach import OpenAIGenerationDebugRead, OutreachValidationReportRead
    from app.services.lead_sourcing.sdr_playbook_outreach import SdrDraftValidationError, SdrResponseParseError

    if isinstance(exc, SdrDraftValidationError):
        report = OutreachValidationReportRead.model_validate(exc.report)
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Borrador rechazado por validación SDR",
                "summary": report.summary,
                "validation": report.model_dump(mode="json"),
            },
        ) from exc
    if isinstance(exc, SdrResponseParseError):
        salvage = (exc.salvage_body or "").strip()
        report = OutreachValidationReportRead(
            valid=False,
            summary=exc.message,
            issues=[str(exc.debug.get("parse_error") or exc.message)],
            rejected_body=salvage,
            channel=exc.debug.get("channel"),
            step_day=exc.debug.get("step_day"),
            generation_debug=OpenAIGenerationDebugRead.model_validate(exc.debug),
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": exc.message,
                "summary": report.summary,
                "validation": report.model_dump(mode="json"),
            },
        ) from exc
    raise HTTPException(
        status_code=502,
        detail=f"No se pudo generar el mensaje con IA: {exc}",
    ) from exc


def _generate_real_touch_content(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
    day: int,
    prior: list[dict[str, Any]],
) -> dict[str, Any]:
    step = _playbook_step(day)
    if step is None:
        raise HTTPException(status_code=400, detail="Toque inválido")
    if not openai_configured():
        raise HTTPException(
            status_code=503,
            detail="OpenAI no está configurada. Definí OPENAI_API_KEY para generar mensajes reales.",
        )

    seller = db.get(User, campaign.seller_id) if campaign.seller_id else None
    education = campaign_education_blob(db, campaign)
    fallback_used = False
    try:
        subj, body, _reason = sdr_pb.generate_sdr_playbook_touch(
            channel=step.channel,
            prospect=_prospect_dict(prospect),
            campaign=_campaign_dict(campaign, seller),
            product=_product_dict(product),
            education=education,
            step_day=step.day,
            step_objective=step.objective,
            prior_touches=prior,
            tone=campaign.tone or "",
        )
    except HTTPException:
        raise
    except Exception as exc:
        _raise_sdr_generation_error(exc)

    from app.services.openai_fallback import FALLBACK_MARKER

    body = (body or "").strip()
    if FALLBACK_MARKER in body:
        fallback_used = True
    if not body or _is_placeholder_message(body):
        raise HTTPException(
            status_code=502,
            detail="La IA no devolvió un mensaje válido para este toque.",
        )

    subject = (subj or "").strip() or None
    if step.channel == "email" and not subject:
        raise HTTPException(
            status_code=502,
            detail="La IA no devolvió un asunto válido para el email.",
        )

    message_body = body
    if step.channel == "email" and subject:
        message_body = f"Asunto: {subject}\n\n{body}"

    return {
        "day": day,
        "channel": step.channel,
        "objective": step.objective,
        "subject": subject,
        "body": body,
        "message_body": message_body,
        "body_preview": _message_preview(message_body),
        "fallback_test": fallback_used,
    }


def generate_sequence_preview(
    db: Session,
    *,
    user: User,
    prospect: Prospect,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    reconcile_sequence_state(db, prospect, commit=True)
    readiness = assess_outreach_readiness(db, prospect=prospect)
    if force_regenerate and prospect.sequence_started_at is None and _has_playbook_draft(prospect):
        prospect.sequence_playbook_draft = None
        prospect.sequence_touch_log = None
        prospect.playbook_name = None
        db.commit()
        db.refresh(prospect)
    block = explain_generate_sequence_block(user, prospect, readiness=readiness)
    if block:
        raise HTTPException(status_code=403, detail=block)
    campaign = readiness.get("campaign")
    if campaign is None:
        raise HTTPException(status_code=400, detail="Asigná una campaña antes de generar la secuencia")
    product = readiness.get("product")
    if product is None:
        raise HTTPException(status_code=400, detail="La campaña debe tener un producto asociado")
    seller = db.get(User, campaign.seller_id) if campaign.seller_id else None
    education = campaign_education_blob(db, campaign)
    p_dict = _prospect_dict(prospect)
    c_dict = _campaign_dict(campaign, seller)
    pr_dict = _product_dict(product)

    touches: list[dict[str, Any]] = []
    prior: list[dict[str, Any]] = []
    for step in DEFAULT_MVP_PLAYBOOK:
        body = _template_body(step.day, step.channel, prospect, product)
        if step.day == 1 and openai_configured():
            try:
                subj, body, _reason = sdr_pb.generate_sdr_playbook_touch(
                    channel=step.channel,
                    prospect=p_dict,
                    campaign=c_dict,
                    product=pr_dict,
                    education=education,
                    step_day=step.day,
                    step_objective=step.objective,
                    prior_touches=prior,
                    tone=campaign.tone or "",
                )
                if subj and step.channel == "email":
                    body = f"Asunto: {subj}\n\n{body}"
            except HTTPException:
                raise
            except Exception:
                logger.warning(
                    "generate_sequence_preview day1_openai_fallback prospect_id=%s",
                    prospect.id,
                    exc_info=True,
                )
        touch = {
            "day": step.day,
            "channel": step.channel,
            "objective": step.objective,
            "body_preview": body,
        }
        touches.append(touch)
        prior.append({"day": step.day, "channel": step.channel, "body": body})

    draft_json = json.dumps(touches, ensure_ascii=False)
    prospect.sequence_playbook_draft = draft_json
    prospect.playbook_name = PLAYBOOK_NAME
    _init_touch_log_generado(prospect)
    db.commit()
    db.refresh(prospect)
    return {
        "prospect_id": prospect.id,
        "playbook_name": PLAYBOOK_NAME,
        "touches": touches,
    }


def start_prospect_sequence(db: Session, *, user: User, prospect: Prospect) -> Prospect:
    reconcile_sequence_state(db, prospect, commit=True)
    readiness = assess_outreach_readiness(db, prospect=prospect)
    block = explain_start_sequence_block(user, prospect, readiness=readiness)
    if block:
        raise HTTPException(status_code=403, detail=block)
    if prospect.owner_user_id != user.id and normalize_role(user.role) == UserRole.sdr:
        raise HTTPException(status_code=403, detail="Solo el owner puede iniciar la secuencia")

    now = _now()
    own.mark_sequence_started(db, user=user, prospect=prospect)
    prospect.sequence_started_at = now
    prospect.sequence_group = "contactado"
    prospect.sequence_state = "sin_respuesta"
    prospect.sequence_paused = False
    prospect.sequence_fired_milestones = "[]"
    prospect.playbook_name = prospect.playbook_name or PLAYBOOK_NAME
    log = _touch_log(prospect)
    if not log:
        _init_touch_log_generado(prospect)
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    db.commit()
    db.refresh(prospect)
    return prospect


def _prior_sent_touches(prospect: Prospect, before_day: int) -> list[dict[str, Any]]:
    draft = _draft_by_day(prospect)
    log = _touch_log(prospect)
    prior: list[dict[str, Any]] = []
    for step in DEFAULT_MVP_PLAYBOOK:
        if step.day >= before_day:
            break
        if step.day not in _completed_days(prospect):
            continue
        touch = draft.get(step.day, {})
        entry = log.get(str(step.day), {})
        _, body = _resolve_step_message(entry=entry, draft_touch=touch, msg=None)
        if not body:
            continue
        prior.append({"day": step.day, "channel": step.channel, "body": body})
    return prior


def _mark_touch_failed(
    prospect: Prospect,
    day: int,
    error: str,
    *,
    validation_rejection: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "status": TOUCH_FALLIDO,
        "error": error[:500],
        "message_id": None,
    }
    if validation_rejection:
        fields["validation_rejection"] = validation_rejection
    _set_touch_entry(prospect, day, **fields)


def _mark_touch_openai_pending(
    prospect: Prospect,
    day: int,
    *,
    error_detail: dict[str, Any],
    generation_context: dict[str, Any],
) -> None:
    """Conserva el toque pendiente ante rate limit (no marca fallido)."""
    entry = _touch_entry(prospect, day)
    prev_status = entry.get("status")
    keep_status = (
        prev_status
        if prev_status in (TOUCH_GENERADO, TOUCH_PENDIENTE)
        else TOUCH_PENDIENTE
    )
    openai_meta = error_detail.get("openai") if isinstance(error_detail.get("openai"), dict) else {}
    message = str(
        error_detail.get("summary")
        or error_detail.get("message")
        or "OpenAI rate limit excedido. Reintentá en unos segundos."
    )
    _set_touch_entry(
        prospect,
        day,
        status=keep_status,
        error=message[:500],
        openai_last_error=openai_meta,
        generation_context=generation_context,
        validation_rejection=None,
    )


def _record_openai_failure_event(
    db: Session,
    *,
    prospect: Prospect,
    day: int,
    openai_meta: dict[str, Any],
    generation_context: dict[str, Any],
) -> None:
    from app.services.ai_decision_log import record_ai_decision

    record_ai_decision(
        db,
        company_id=prospect.company_id,
        campaign_id=prospect.campaign_id,
        prospect_id=prospect.id,
        event_type="openai_rate_limit",
        decision="pending_retry",
        summary=str(openai_meta.get("error") or "OpenAI rate limit")[:500],
        payload={
            "day": day,
            "model": openai_meta.get("model"),
            "error_type": openai_meta.get("error_type"),
            "attempts": openai_meta.get("attempts"),
            "timestamp": openai_meta.get("timestamp"),
            "generation_context": generation_context,
        },
    )


def _sync_sequence_completion(db: Session, *, prospect: Prospect) -> None:
    if next_executable_day(prospect) is None and prospect.sequence_started_at is not None:
        own.mark_sequence_completed(db, prospect=prospect)
        prospect.sequence_completed_at = _now()
        next_at, _ = compute_next_touch(prospect)
        prospect.next_touch_at = next_at


def execute_sequence_touch(db: Session, *, user: User, prospect: Prospect, day: int) -> dict[str, Any]:
    if not can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés ejecutar toques en este prospecto")
    if prospect.sequence_started_at is None:
        raise HTTPException(status_code=400, detail="Iniciá la secuencia antes de ejecutar toques")
    if prospect.sequence_paused:
        raise HTTPException(
            status_code=400,
            detail="La secuencia está pausada por respuesta del prospecto. Respondé antes de ejecutar más toques.",
        )
    if day not in PLAYBOOK_DAYS:
        raise HTTPException(status_code=400, detail="Día de secuencia inválido")

    nxt = next_executable_day(prospect)
    if nxt != day:
        raise HTTPException(
            status_code=400,
            detail=f"El próximo toque ejecutable es Día {nxt}" if nxt else "La secuencia ya está completa",
        )

    step = _playbook_step(day)
    if step is None:
        raise HTTPException(status_code=400, detail="Toque no encontrado en playbook")
    if not _channel_ready(prospect, step.channel):
        raise HTTPException(
            status_code=400,
            detail=f"Canal {step.channel} no disponible para este prospecto",
        )

    readiness = assess_outreach_readiness(db, prospect=prospect)
    campaign = readiness.get("campaign")
    product = readiness.get("product")
    if campaign is None:
        raise HTTPException(status_code=400, detail="Campaña no configurada")

    draft = _draft_by_day(prospect)
    from app.services import followup_engine
    from app.services import outreach_simulation as sim

    now = _now()
    try:
        content = _generate_real_touch_content(
            db,
            prospect=prospect,
            campaign=campaign,
            product=product,
            day=day,
            prior=_prior_sent_touches(prospect, day),
        )
    except HTTPException as exc:
        from app.services.openai_service import is_retryable_openai_http_detail

        detail = exc.detail
        if isinstance(detail, dict) and is_retryable_openai_http_detail(detail):
            openai_meta = detail.get("openai") if isinstance(detail.get("openai"), dict) else {}
            generation_context = {
                "day": day,
                "channel": step.channel,
                "objective": step.objective,
                "prior_touch_count": len(_prior_sent_touches(prospect, day)),
                "saved_at": now.isoformat(),
            }
            _mark_touch_openai_pending(
                prospect,
                day,
                error_detail=detail,
                generation_context=generation_context,
            )
            _record_openai_failure_event(
                db,
                prospect=prospect,
                day=day,
                openai_meta=openai_meta,
                generation_context=generation_context,
            )
            db.commit()
            raise

        validation_rejection = (
            detail.get("validation") if isinstance(detail, dict) else None
        )
        error_text = (
            str(detail.get("summary") or detail.get("message") or detail)
            if isinstance(detail, dict)
            else str(detail)
        )
        _mark_touch_failed(
            prospect,
            day,
            error_text[:500],
            validation_rejection=validation_rejection,
        )
        db.commit()
        raise

    message_body = content["message_body"]
    _persist_touch_draft(prospect, draft, content)

    try:
        msg = sim.make_message(
            prospect_id=prospect.id,
            campaign_id=campaign.id,
            sender_type="ai",
            message=message_body,
            channel=step.channel,
            direction="outbound",
            is_testing=True,
        )
        db.add(msg)
        db.flush()
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=message_body,
        )
        _append_fired(prospect, day)
        _set_touch_entry(
            prospect,
            day,
            status=TOUCH_ENVIADO,
            sent_at=now.isoformat(),
            message_id=msg.id,
            subject=content.get("subject"),
            message_body=message_body,
            body=content.get("body"),
            error=None,
            openai_last_error=None,
            generation_context=None,
            fallback_test=bool(content.get("fallback_test")),
        )
        next_at, next_label = compute_next_touch(prospect)
        prospect.next_touch_at = next_at
        _sync_sequence_completion(db, prospect=prospect)
        db.commit()
        db.refresh(prospect)
    except Exception as exc:
        _mark_touch_failed(prospect, day, f"No se pudo registrar el envío: {exc}")
        db.commit()
        raise HTTPException(status_code=502, detail=f"No se pudo enviar el toque: {exc}") from exc

    tracking = build_sequence_tracking(db, prospect=prospect)
    sent_label = "FALLBACK TEST" if content.get("fallback_test") else TOUCH_STATUS_LABELS[TOUCH_ENVIADO]
    return {
        "prospect_id": prospect.id,
        "day": day,
        "touch_status": TOUCH_ENVIADO,
        "status_label": sent_label,
        "fallback_test": bool(content.get("fallback_test")),
        "message": (
            f"Día {day} enviado con mensaje mock (FALLBACK TEST) — OpenAI en rate limit"
            if content.get("fallback_test")
            else f"Día {day} enviado por {step.channel}"
        ),
        "tracking": tracking,
    }


def skip_sequence_touch(db: Session, *, user: User, prospect: Prospect, day: int) -> dict[str, Any]:
    if not can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés omitir toques en este prospecto")
    if prospect.sequence_started_at is None:
        raise HTTPException(status_code=400, detail="Iniciá la secuencia antes de omitir toques")
    if prospect.sequence_paused:
        raise HTTPException(
            status_code=400,
            detail="La secuencia está pausada por respuesta del prospecto.",
        )
    nxt = next_executable_day(prospect)
    if nxt != day:
        raise HTTPException(
            status_code=400,
            detail=f"Solo podés omitir el próximo toque (Día {nxt})" if nxt else "La secuencia ya está completa",
        )
    now = _now()
    _set_touch_entry(prospect, day, status=TOUCH_OMITIDO, skipped_at=now.isoformat())
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    _sync_sequence_completion(db, prospect=prospect)
    db.commit()
    db.refresh(prospect)
    tracking = build_sequence_tracking(db, prospect=prospect)
    return {
        "prospect_id": prospect.id,
        "day": day,
        "touch_status": TOUCH_OMITIDO,
        "status_label": TOUCH_STATUS_LABELS[TOUCH_OMITIDO],
        "message": f"Día {day} omitido",
        "tracking": tracking,
    }


def _last_sent_touch_day(prospect: Prospect) -> int | None:
    done = _completed_days(prospect)
    log = _touch_log(prospect)
    for day in reversed(list(PLAYBOOK_DAYS)):
        if day not in done:
            continue
        if log.get(str(day), {}).get("status") == TOUCH_OMITIDO:
            continue
        return day
    return None


def last_sent_touch_day(prospect: Prospect) -> int | None:
    """Último toque enviado (público para estado comercial / inbound)."""
    return _last_sent_touch_day(prospect)


def _outreach_message_dict(msg: OutreachMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "prospect_id": msg.prospect_id,
        "campaign_id": msg.campaign_id,
        "sender_type": msg.sender_type,
        "message": msg.message,
        "channel": msg.channel,
        "direction": msg.direction,
        "is_testing": bool(getattr(msg, "is_testing", False)),
        "created_at": msg.created_at,
    }


def simulate_sequence_response(
    db: Session,
    *,
    user: User,
    prospect: Prospect,
    message: str,
    channel: str | None = None,
) -> dict[str, Any]:
    from sqlalchemy.orm import selectinload

    from app.models.enums import ProspectStatus
    from app.services import conversation_intelligence as ci
    from app.services import followup_engine
    from app.services import multichannel_sequence as mseq
    from app.services import openai_service
    from app.services import outreach_metrics as om
    from app.services import outreach_simulation as sim
    from app.services import pipeline_sync
    from app.services.ai_behavior_policy import load_behavior_policy, resolve_booking_priority_from_signals
    from app.services.ai_instruction_context import campaign_education_blob

    if not can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés simular respuestas en este prospecto")
    if prospect.sequence_started_at is None:
        raise HTTPException(status_code=400, detail="Iniciá la secuencia antes de simular respuestas")
    if not om.is_sequence_testing_enabled():
        cfg = om.outreach_simulation_config()
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Simulación de secuencia deshabilitada",
                "reason": (
                    "NEXUS_REAL_MODE=1 sin NEXUS_ENABLE_SEQUENCE_TESTING=1"
                    if om.is_real_mode() and not cfg.get("env_nexus_enable_sequence_testing")
                    else "NEXUS_DISABLE_OUTREACH_SIMULATION=1"
                    if om.is_outreach_simulation_disabled()
                    else "simulación deshabilitada"
                ),
                "testing": cfg,
            },
        )

    affected_day = _last_sent_touch_day(prospect)
    if affected_day is None:
        raise HTTPException(
            status_code=400,
            detail="Ejecutá al menos un toque antes de simular una respuesta del prospecto.",
        )

    readiness = assess_outreach_readiness(db, prospect=prospect)
    campaign = readiness.get("campaign")
    if campaign is None:
        raise HTTPException(status_code=400, detail="Campaña no configurada para este prospecto")

    campaign = db.scalars(
        select(Campaign)
        .where(Campaign.id == campaign.id)
        .options(selectinload(Campaign.product))
    ).first() or campaign

    if not prospect.campaign_id:
        prospect.campaign_id = campaign.id

    step = _playbook_step(affected_day)
    touch_channel = channel or (step.channel if step else "email")
    if touch_channel not in ("email", "linkedin", "whatsapp"):
        raise HTTPException(status_code=400, detail="Canal inválido")

    inbound_text = message.strip()
    if len(inbound_text) < 2:
        raise HTTPException(status_code=400, detail="Escribí un mensaje de respuesta del prospecto")

    now = _now()
    inbound = sim.make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="prospect",
        message=inbound_text,
        channel=touch_channel,
        direction="inbound",
        is_testing=True,
    )
    db.add(inbound)
    db.flush()

    mseq.on_inbound_pause_sequence(db, prospect)
    prospect.next_touch_at = None

    history_rows = db.scalars(
        select(OutreachMessage)
        .where(OutreachMessage.prospect_id == prospect.id)
        .order_by(OutreachMessage.created_at.asc(), OutreachMessage.id.asc())
    ).all()
    hist_payload = [
        {"sender_type": m.sender_type, "direction": m.direction, "message": m.message}
        for m in history_rows
    ]
    digest_lines = [
        f"{x['sender_type']}/{x['direction']}: {(x.get('message') or '')[:240]}"
        for x in hist_payload[-16:]
    ]
    digest = "\n".join(digest_lines) if digest_lines else "(vacío)"
    instruction_blob = campaign_education_blob(db, campaign)

    sig = ci.classify_inbound_full(
        inbound_text=inbound_text,
        prior_interest=getattr(prospect, "interest_level", None),
        conversation_digest=digest,
        education=instruction_blob,
    )
    response_class, response_class_label = ci.classify_commercial_response(inbound_text, sig)
    reply_objective = ci.resolve_reply_objective(
        text=inbound_text,
        sig=sig,
        response_class=response_class,
    )
    reply_objective_label = ci.REPLY_OBJECTIVE_LABELS.get(reply_objective, reply_objective)

    policy = load_behavior_policy(db, campaign.company_id)
    inbound_n = followup_engine.count_inbound_prospect_messages(db, prospect.id) + 1
    allow_meeting = ci.should_allow_meeting_nudge(sig, inbound_turn_index=inbound_n)
    norm_in = ci.normalize_inbound_text_for_classification(inbound_text)
    booking_priority = resolve_booking_priority_from_signals(
        policy,
        inbound_text=norm_in or inbound_text,
        explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
        prospect_wants_meeting=bool(sig.prospect_wants_meeting),
        interest_level=sig.interest_level,
    ) or reply_objective == "agendar"
    if reply_objective in ("rechazo", "timing") or response_class in (
        "no_interesado",
        "contactar_mas_adelante",
    ):
        booking_priority = False
    timing_soft = (
        sig.objection_type != "not_interested"
        and ci.timing_deferral_should_apply(sig, inbound_text=inbound_text)
        and not booking_priority
    )

    seller = db.get(User, campaign.seller_id) if campaign.seller_id else None
    product = readiness.get("product")
    suggestion_error: str | None = None
    suggested_reply = ""

    from app.services import commercial_conversation_agent as agent

    pre_confidence = agent.estimate_classification_confidence(
        text=inbound_text,
        sig=sig,
        response_class=response_class,
    )
    pre_escalation = agent.detect_escalation_reason(
        text=inbound_text,
        sig=sig,
        response_class=response_class,
        confidence=pre_confidence,
    )

    if agent.simulation_reply_needs_openai(
        inbound_text=inbound_text,
        campaign=campaign,
        reply_objective=reply_objective,
        escalation_reason=pre_escalation,
    ):
        try:
            suggested_reply = openai_service.generate_inbound_response(
                prospect=_prospect_dict(prospect),
                inbound_message=inbound_text,
                conversation_history=hist_payload,
                campaign=_campaign_dict(campaign, seller),
                product=_product_dict(product),
                education=instruction_blob,
                objection_type=sig.objection_type,
                interest_level=sig.interest_level or "low",
                allow_soft_meeting_close=allow_meeting,
                inbound_turn_index=inbound_n,
                prospect_timing_soft=timing_soft,
                prospect_booking_priority=booking_priority,
                ai_policy=policy,
                prospect_wants_meeting=bool(sig.prospect_wants_meeting),
                explicit_meeting_commitment=bool(sig.explicit_meeting_commitment),
                prospect_substantive_questions=bool(sig.asks_concrete_questions),
                reply_objective=reply_objective,
                response_class=response_class,
            )
        except Exception as exc:
            suggestion_error = str(exc)[:300]
            suggested_reply = (
                f"[Sugerencia no disponible — configurá OPENAI_API_KEY]\n"
                f"Clasificación: {response_class_label}. "
                f"Respondé al prospecto de forma consultiva según su mensaje."
            )

    classification_summary = (
        f"{response_class_label} · objetivo {reply_objective_label} · interés {sig.interest_level or 'low'}"
        + (f" · objeción {sig.objection_type}" if sig.objection_type else "")
    )

    from app.services import prospect_commercial_state as pcs
    from app.services.meeting_slot_parser import parse_meeting_slot

    campaign_tz = (
        getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires"
    ).strip()
    parsed_meeting_slot = parse_meeting_slot(inbound_text, timezone=campaign_tz)

    agent_result = agent.process_inbound_turn(
        db,
        prospect=prospect,
        campaign=campaign,
        inbound=inbound,
        inbound_text=inbound_text,
        channel=touch_channel,
        sig=sig,
        response_class=response_class,
        response_class_label=response_class_label,
        reply_objective=reply_objective,
        reply_objective_label=reply_objective_label,
        suggested_reply=suggested_reply or "",
        testing=True,
        simulate_delivery=True,
    )

    _set_touch_entry(
        prospect,
        affected_day,
        status=TOUCH_RESPONDIDO,
        inbound_at=now.isoformat(),
        inbound_message=inbound_text[:4000],
        response_class=response_class,
        response_class_label=response_class_label,
        reply_objective=reply_objective,
        reply_objective_label=reply_objective_label,
        suggested_reply=None,
        classification_summary=classification_summary,
        suggestion_error=suggestion_error,
        testing=True,
        auto_sent=agent_result["auto_sent"],
        delivery_mode=agent_result["delivery_mode"],
        classification_confidence=agent_result["classification_confidence"],
        escalation_reason=agent_result.get("escalation_reason"),
        outbound_message_id=agent_result.get("outbound_message_id"),
        conversation_state=agent_result["conversation_state"],
        meeting_id=(agent_result.get("meeting_booking") or {}).get("meeting_id"),
        google_calendar_event_id=(agent_result.get("meeting_booking") or {}).get(
            "google_calendar_event_id"
        ),
        google_calendar_html_link=(agent_result.get("meeting_booking") or {}).get(
            "google_calendar_html_link"
        ),
        calendar_created=bool(
            (agent_result.get("meeting_booking") or {}).get("calendar_created")
        ),
        meeting_scheduled_for=(agent_result.get("meeting_booking") or {}).get("scheduled_for"),
        creation_method=(agent_result.get("meeting_booking") or {}).get("creation_method"),
    )

    previous_state = pcs.resolve_commercial_state(prospect, db=db, include_testing=True)
    commercial_state = pcs.apply_commercial_state(
        prospect,
        response_class=response_class,
        reply_objective=reply_objective,
        db=db,
        testing=True,
    )
    commercial_state_debug = {
        "inbound_text": inbound_text,
        "response_class": response_class,
        "response_class_label": response_class_label,
        "reply_objective": reply_objective,
        "reply_objective_label": reply_objective_label,
        "meeting_slot_parsed": parsed_meeting_slot.isoformat() if parsed_meeting_slot else None,
        "meeting_intent_detected": bool(parsed_meeting_slot or reply_objective == "agendar"),
        "meeting_booking": agent_result.get("meeting_booking"),
        "previous_commercial_state": previous_state,
        "previous_commercial_state_label": pcs.commercial_state_label(previous_state),
        "new_commercial_state": commercial_state,
        "new_commercial_state_label": pcs.commercial_state_label(commercial_state),
        "saved_to_db": True,
        "is_testing": True,
    }
    logger.info(
        "simulate_sequence_response commercial_state prospect_id=%s day=%s "
        "text=%r class=%s objective=%s prev=%s new=%s testing=True saved=True",
        prospect.id,
        affected_day,
        inbound_text[:200],
        response_class,
        reply_objective,
        previous_state,
        commercial_state,
    )

    db.commit()
    db.refresh(prospect)
    db.refresh(inbound)
    outbound_msg = agent_result.get("outbound_message")
    if outbound_msg is not None:
        db.refresh(outbound_msg)

    tracking = build_sequence_tracking(db, prospect=prospect)
    conversation = tracking.get("conversation") or []

    agent_turn = {
        "inbound_text": inbound_text,
        "response_class": response_class,
        "response_class_label": response_class_label,
        "reply_objective": reply_objective,
        "reply_objective_label": reply_objective_label,
        "classification_confidence": agent_result["classification_confidence"],
        "delivery_mode": agent_result["delivery_mode"],
        "auto_sent": agent_result["auto_sent"],
        "channel": touch_channel,
        "escalation_reason": agent_result.get("escalation_reason"),
        "conversation_state": agent_result["conversation_state"],
        "conversation_state_label": agent_result["conversation_state_label"],
        "saved_to_db": True,
        "is_testing": True,
    }

    return {
        "prospect_id": prospect.id,
        "affected_day": affected_day,
        "response_class": response_class,
        "response_class_label": response_class_label,
        "reply_objective": reply_objective,
        "reply_objective_label": reply_objective_label,
        "classification_summary": classification_summary,
        "sequence_paused": bool(prospect.sequence_paused),
        "sequence_state": getattr(prospect, "sequence_state", None),
        "prospect_status": prospect.status,
        "commercial_state": commercial_state,
        "commercial_state_label": pcs.commercial_state_label(commercial_state),
        "commercial_state_is_testing": True,
        "commercial_state_debug": commercial_state_debug,
        "agent_turn": agent_turn,
        "auto_sent": agent_result["auto_sent"],
        "delivery_mode": agent_result["delivery_mode"],
        "classification_confidence": agent_result["classification_confidence"],
        "escalation_reason": agent_result.get("escalation_reason"),
        "conversation_state": agent_result["conversation_state"],
        "conversation_state_label": agent_result["conversation_state_label"],
        "outbound_message": (
            _outreach_message_dict(outbound_msg) if outbound_msg is not None else None
        ),
        "testing": True,
        "suggested_reply": None,
        "suggested_channel": touch_channel,
        "inbound_message": _outreach_message_dict(inbound),
        "conversation": conversation,
        "tracking": tracking,
        "meeting_booking": agent_result.get("meeting_booking"),
    }


def enrich_prospect_contact(db: Session, *, user: User, prospect: Prospect) -> dict[str, Any]:
    if not can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés enriquecer este prospecto")
    from app.schemas.lead_sourcing import LeadCandidateRead
    from app.services.lead_sourcing.providers.registry import get_contact_enrichment_provider

    provider = get_contact_enrichment_provider()
    if not provider.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Enriquecimiento no disponible. Configurá PROSPEO_API_KEY o completá los datos manualmente.",
        )

    lead = LeadCandidateRead(
        external_id=str(prospect.id),
        provider=prospect.source_provider or "manual",
        name=prospect.name,
        company_name=prospect.company_name,
        role=prospect.role,
        industry=prospect.industry,
        country=prospect.country,
        email=prospect.email,
        linkedin_url=prospect.linkedin_url,
        phone=prospect.phone,
        whatsapp=prospect.whatsapp,
        company_website=prospect.company_website,
    )
    try:
        enriched = provider.enrich_contact(lead)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Enriquecimiento falló: {exc}") from exc

    changed = False
    if enriched.email and enriched.email != prospect.email:
        prospect.email = enriched.email
        changed = True
    if enriched.linkedin_url and enriched.linkedin_url != prospect.linkedin_url:
        prospect.linkedin_url = enriched.linkedin_url
        changed = True
    phone_val = enriched.phone or enriched.whatsapp
    if phone_val and phone_val != prospect.phone:
        prospect.phone = phone_val
        changed = True
    if phone_val and not prospect.whatsapp:
        prospect.whatsapp = phone_val
        changed = True

    db.commit()
    db.refresh(prospect)
    readiness = assess_outreach_readiness(db, prospect=prospect)
    return {
        "prospect_id": prospect.id,
        "message": "Contacto enriquecido" if changed else "Sin datos nuevos — revisá nombre, empresa o LinkedIn",
        "enriched": changed,
        "readiness": _readiness_api_payload(readiness),
    }
