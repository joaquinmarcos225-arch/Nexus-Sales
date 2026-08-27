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
from app.services.campaign_outreach_context import company_brand_name
from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url
from app.core.sequence_playbook import (
    PLAYBOOK_DAYS,
    PLAYBOOK_NAME,
    normalize_fired_milestones,
    playbook_step_for_day,
)
from app.services.lead_sourcing.mvp_outreach_playbook import (
    DEFAULT_MVP_PLAYBOOK,
    lead_available_channels,
    openai_configured,
)
from app.services.lead_sourcing import sdr_playbook_outreach as sdr_pb

logger = logging.getLogger(__name__)

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
        parsed = [int(x) for x in raw if str(x).isdigit()]
    else:
        try:
            parsed = [int(x) for x in json.loads(str(raw)) if str(x).isdigit()]
        except Exception:
            parsed = []
    return normalize_fired_milestones(parsed)


def _append_fired(prospect: Prospect, day: int) -> None:
    days = sorted(set(_fired_list(prospect) + [int(day)]))
    prospect.sequence_fired_milestones = json.dumps(days)


def _remove_fired(prospect: Prospect, day: int) -> None:
    days = [d for d in _fired_list(prospect) if int(d) != int(day)]
    prospect.sequence_fired_milestones = json.dumps(days)


def _touch_entry_lacks_real_delivery_meta(prospect: Prospect, day: int, entry: dict[str, Any]) -> bool:
    """True si el toque figura enviado pero no hubo entrega real (fallback/sim)."""
    if entry.get("fallback_test"):
        return True
    # WhatsApp Web asistido (SDR marcó enviado): es entrega real, no Cloud API wamid.
    if entry.get("whatsapp_assisted_sent") or entry.get("sdr_marked_sent"):
        return False
    if getattr(prospect, "whatsapp_sdr_marked_sent_at", None) and (
        entry.get("status") in (TOUCH_ENVIADO, TOUCH_RESPONDIDO)
    ):
        # Misma ventana: el mark-sent del SDR cuenta aunque falte el flag en el log viejo.
        campaign = getattr(prospect, "campaign", None)
        step = _playbook_step(day, campaign)
        if step is not None and str(getattr(step, "channel", "") or "").lower() == "whatsapp":
            return False

    campaign = getattr(prospect, "campaign", None)
    step = _playbook_step(day, campaign)
    if step is None:
        return False
    from app.services.sequence_touch_gmail import sequence_email_touch_uses_gmail
    from app.services.sequence_touch_whatsapp import sequence_whatsapp_touch_uses_api

    channel = str(getattr(step, "channel", "") or "").strip().lower()
    if sequence_whatsapp_touch_uses_api(day=day, channel=channel):
        wamid = str(entry.get("whatsapp_message_id") or "").strip()
        if entry.get("status") in (TOUCH_ENVIADO, TOUCH_RESPONDIDO):
            return not wamid
        return False
    if channel == "whatsapp":
        # Modo asistido (default): enviado + cuerpo/sent_at = real. No exigir wamid.
        if entry.get("status") in (TOUCH_ENVIADO, TOUCH_RESPONDIDO):
            has_body = bool(
                (entry.get("message_body") or "").strip() or (entry.get("body") or "").strip()
            )
            has_sent_at = bool(entry.get("sent_at"))
            return not (has_body or has_sent_at)
        return False
    if sequence_email_touch_uses_gmail(day=day, channel=channel):
        if entry.get("gmail_draft_id") or entry.get("gmail_manually_sent") or entry.get("gmail_auto_detected"):
            return False
        if "gmail_message_id" in entry:
            return not str(entry.get("gmail_message_id") or "").strip()
        return entry.get("status") in (TOUCH_ENVIADO, TOUCH_RESPONDIDO)
    return False


def _clear_touch_draft(prospect: Prospect, day: int) -> None:
    draft = _draft_by_day(prospect)
    touch = draft.get(day)
    if not touch:
        return
    for key in ("body", "message_body", "body_preview", "subject"):
        touch.pop(key, None)
    touches_list = []
    campaign = getattr(prospect, "campaign", None)
    for playbook_step in _playbook_steps(campaign):
        item = draft.get(playbook_step.day)
        if item:
            touches_list.append(item)
    prospect.sequence_playbook_draft = json.dumps(touches_list, ensure_ascii=False)


def _reset_touch_for_retry(prospect: Prospect, day: int) -> None:
    """Deja el toque listo para reejecutar tras un falso envío o fallo."""
    _remove_fired(prospect, day)
    _clear_touch_draft(prospect, day)
    _set_touch_entry(
        prospect,
        day,
        status=TOUCH_PENDIENTE,
        sent_at=None,
        message_id=None,
        error=None,
        validation_rejection=None,
        openai_last_error=None,
        generation_context=None,
        fallback_test=False,
        whatsapp_message_id=None,
        gmail_message_id=None,
        gmail_draft_id=None,
        body=None,
        message_body=None,
        subject=None,
    )


def _maybe_reset_pseudo_sent_touch(prospect: Prospect, day: int) -> bool:
    """Auto-limpia solo falsos envíos (sin wamid/gmail); no borra fallos con error."""
    entry = _touch_entry(prospect, day)
    status = entry.get("status")
    if status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO) and _touch_entry_lacks_real_delivery_meta(
        prospect, day, entry
    ):
        _reset_touch_for_retry(prospect, day)
        return True
    return False


def _maybe_reset_retryable_touch(prospect: Prospect, day: int) -> bool:
    entry = _touch_entry(prospect, day)
    status = entry.get("status")
    if status == TOUCH_FALLIDO:
        _reset_touch_for_retry(prospect, day)
        return True
    return _maybe_reset_pseudo_sent_touch(prospect, day)


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


def _init_touch_log_generado(prospect: Prospect, steps: Any = None) -> None:
    log: dict[str, dict[str, Any]] = {}
    iterable = steps if steps is not None else DEFAULT_MVP_PLAYBOOK
    for step in iterable:
        day = getattr(step, "day", None)
        if day is None and isinstance(step, dict):
            day = step.get("day")
        if day is None:
            continue
        log[str(int(day))] = {"status": TOUCH_PENDIENTE}
    _save_touch_log(prospect, log)


def _playbook_step(day: int, campaign: Campaign | None = None):
    if campaign is not None:
        from app.services.campaign_sequence_channels import effective_playbook_step

        return effective_playbook_step(campaign, day)
    return playbook_step_for_day(day)


def _playbook_steps(campaign: Campaign | None = None):
    if campaign is not None:
        from app.services.campaign_sequence_channels import effective_playbook_steps

        return effective_playbook_steps(campaign)
    return DEFAULT_MVP_PLAYBOOK


def _planned_days(prospect: Prospect, campaign: Campaign | None = None) -> tuple[int, ...]:
    """Días de la secuencia tal como se configuró (plan de campaña / draft completo)."""
    if campaign is not None:
        from app.services.campaign_sequence_channels import campaign_touch_days

        cdays = campaign_touch_days(campaign)
        if cdays:
            return cdays

    draft = _draft_by_day(prospect)
    if draft:
        days = tuple(sorted(int(d) for d in draft.keys()))
        # Preview real guarda TODOS los toques. Un stub con 1 día no define el plan.
        if len(days) >= 2:
            return days

    log = _touch_log(prospect)
    if log:
        days = tuple(sorted(int(k) for k in log.keys() if str(k).isdigit()))
        if len(days) >= 2:
            return days

    return tuple(PLAYBOOK_DAYS)


def _completed_days(prospect: Prospect, campaign: Campaign | None = None) -> set[int]:
    log = _touch_log(prospect)
    fired = set(_fired_list(prospect))
    draft = _draft_by_day(prospect)
    planned = _planned_days(prospect, campaign)
    done: set[int] = set()
    for day in planned:
        entry = log.get(str(day), {})
        status = entry.get("status")
        if status == TOUCH_OMITIDO:
            done.add(day)
            continue
        if status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO):
            if _touch_entry_lacks_real_delivery_meta(prospect, day, entry):
                continue
            draft_touch = draft.get(day, {})
            _, body = _resolve_step_message(entry=entry, draft_touch=draft_touch, msg=None)
            if body:
                done.add(day)
            continue
        if day in fired:
            entry = log.get(str(day), {})
            if _touch_entry_lacks_real_delivery_meta(prospect, day, entry):
                continue
            draft_touch = draft.get(day, {})
            _, body = _resolve_step_message(entry=entry, draft_touch=draft_touch, msg=None)
            if body:
                done.add(day)
    return done


def next_executable_day(prospect: Prospect, campaign: Campaign | None = None) -> int | None:
    if prospect.sequence_started_at is None:
        return None
    done = _completed_days(prospect, campaign)
    for day in _planned_days(prospect, campaign):
        if day not in done:
            return day
    return None


def next_executable_channel(prospect: Prospect, campaign: Campaign | None = None) -> str:
    """Canal del proximo toque: log / draft de campana / playbook."""
    nxt = next_executable_day(prospect, campaign)
    if nxt is None:
        return ""
    entry = _touch_log(prospect).get(str(nxt), {})
    ch = str(entry.get("channel") or "").strip().lower()
    if ch:
        return ch
    draft = _draft_by_day(prospect).get(nxt) or {}
    ch = str(draft.get("channel") or "").strip().lower()
    if ch:
        return ch
    step = _playbook_step(nxt, campaign)
    return str(getattr(step, "channel", None) or "").strip().lower() if step else ""


def _channel_ready(prospect: Prospect, channel: str) -> bool:
    if channel == "email":
        return _has_valid_email(prospect.email)
    if channel == "linkedin":
        return _has_valid_linkedin(prospect.linkedin_url)
    if channel == "whatsapp":
        return _has_valid_whatsapp(prospect.phone, prospect.whatsapp)
    if channel == "call":
        from app.services.call_assisted_service import prospect_has_callable_number

        return prospect_has_callable_number(prospect)
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
        if day == next_day:
            return TOUCH_FALLIDO, "current"
        return TOUCH_FALLIDO, "failed"

    sent_at = _parse_dt(entry.get("sent_at"))
    if status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO) or day in _fired_list(prospect):
        if _touch_entry_lacks_real_delivery_meta(prospect, day, entry):
            return TOUCH_FALLIDO, "current" if day == next_day else "failed"
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
    from app.services.outreach_prospect_research import research_context_for_prompt

    research = research_context_for_prompt(prospect)
    return {
        "id": str(getattr(prospect, "id", "") or ""),
        "name": prospect.name or "",
        "company_name": prospect.company_name or "",
        "role": prospect.role or "",
        "email": prospect.email or "",
        "linkedin_url": prospect.linkedin_url or "",
        "phone": prospect.phone or "",
        "whatsapp": prospect.whatsapp or "",
        "country": prospect.country or "",
        "industry": prospect.industry or "",
        "prospecting_context": research,
        "research_brief": research,
    }


def _campaign_dict(campaign: Campaign, seller: User | None) -> dict[str, str]:
    from app.services.outreach_display_names import sender_first_name

    sender = sender_first_name(
        user=seller,
        campaign_sender=getattr(campaign, "sender_name", None),
        fallback="",
    )
    company_name = company_brand_name(campaign)
    return {
        "id": str(getattr(campaign, "id", "") or ""),
        "name": campaign.name or "",
        "tone": campaign.tone or "",
        "target_role": campaign.target_role or "",
        "calendar_link": campaign.calendar_link or "",
        "sender_name": sender,
        "brand_name": company_name,
        "company_name": company_name,
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
    campaign = getattr(prospect, "campaign", None)
    planned = set(_planned_days(prospect, campaign)) if campaign is not None else set(PLAYBOOK_DAYS)
    if not planned:
        planned = set(PLAYBOOK_DAYS)
    out: dict[int, dict[str, Any]] = {}
    for touch in parsed:
        day = touch.get("day")
        if day is None:
            continue
        try:
            day_int = int(day)
        except (TypeError, ValueError):
            continue
        if day_int in planned or day_int in PLAYBOOK_DAYS:
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
    También deja el próximo toque reejecutable si quedó en fallido/falso envío.
    """
    changed = False
    if prospect.sequence_started_at is not None:
        nxt = next_executable_day(prospect)
        if nxt is not None and _maybe_reset_pseudo_sent_touch(prospect, nxt):
            changed = True
        if changed and commit:
            db.commit()
            db.refresh(prospect)
        return build_sequence_debug(prospect)

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
    """SDR/Manager/Director operan outreach de prospectos que tomaron."""
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
        landline_phone=getattr(prospect, "landline_phone", None),
    )


def _has_valid_email(email: str | None) -> bool:
    return bool((email or "").strip()) and "@" in (email or "")


def _has_valid_linkedin(linkedin_url: str | None) -> bool:
    return is_personal_linkedin_url(linkedin_url)


def _has_valid_whatsapp(phone: str | None, whatsapp: str | None) -> bool:
    from app.services.whatsapp_cloud_service import normalize_whatsapp_digits

    return bool(normalize_whatsapp_digits(phone, whatsapp))


def _has_valid_contact(prospect: Prospect) -> bool:
    from app.services.call_assisted_service import prospect_has_callable_number

    return (
        _has_valid_email(prospect.email)
        or _has_valid_linkedin(prospect.linkedin_url)
        or _has_valid_whatsapp(prospect.phone, prospect.whatsapp)
        or prospect_has_callable_number(prospect)
    )


CHANNEL_LABELS: dict[str, str] = {
    "email": "Email",
    "linkedin": "LinkedIn",
    "whatsapp": "WhatsApp",
    "call": "Llamada",
}

CHANNELS_REQUIRED = 1
CHANNELS_TOTAL = 4


def _channels_still_needed(channel_count: int) -> int:
    return max(0, CHANNELS_REQUIRED - channel_count)


def _format_channels_requirement_message(*, channel_count: int) -> str:
    """Mensaje claro: cuántos canales faltan para llegar al mínimo (no confundir con el mínimo mismo)."""
    still = _channels_still_needed(channel_count)
    if still == 0:
        return (
            f"{channel_count}/{CHANNELS_TOTAL} canales válidos "
            f"(mínimo {CHANNELS_REQUIRED} requeridos)."
        )
    if still == 1:
        return (
            f"Falta 1 canal más: tenés {channel_count} de {CHANNELS_REQUIRED} requeridos "
            f"({CHANNELS_TOTAL} posibles: email, LinkedIn, WhatsApp, llamada)."
        )
    return (
        f"Faltan {still} canales: tenés {channel_count} de {CHANNELS_REQUIRED} requeridos "
        f"({CHANNELS_TOTAL} posibles: email, LinkedIn, WhatsApp, llamada)."
    )


def _build_channels_detail(prospect: Prospect) -> list[dict[str, Any]]:
    email_ok = _has_valid_email(prospect.email)
    linkedin_ok = _has_valid_linkedin(prospect.linkedin_url)
    whatsapp_ok = _has_valid_whatsapp(prospect.phone, prospect.whatsapp)
    from app.services.call_assisted_service import prospect_call_target, prospect_has_callable_number

    call_ok = prospect_has_callable_number(prospect)
    _, call_kind, call_display = prospect_call_target(prospect)
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
            "detail": (prospect.whatsapp or prospect.phone) if whatsapp_ok else "Sin celular/WhatsApp",
        },
        {
            "key": "call",
            "label": CHANNEL_LABELS["call"],
            "ok": call_ok,
            "detail": (
                f"{'Fijo' if call_kind == 'landline' else 'Celular'} · {call_display}"
                if call_ok
                else "Sin teléfono para llamar"
            ),
        },
    ]


def _format_channels_summary(*, channel_count: int, available_channels: list[str]) -> str:
    detected = ", ".join(CHANNEL_LABELS.get(c, c) for c in sorted(available_channels)) or "ninguno"
    base = _format_channels_requirement_message(channel_count=channel_count)
    return f"{base} Detectados: {detected}."


def _format_readiness_block_detail(readiness: dict[str, Any]) -> str:
    parts: list[str] = []
    channel_count = int(readiness.get("channel_count") or 0)
    channels = readiness.get("available_channels") or []
    if channel_count < CHANNELS_REQUIRED:
        parts.append(_format_channels_requirement_message(channel_count=channel_count))
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


def _count_sent_touches(prospect: Prospect) -> int:
    log = _touch_log(prospect)
    return sum(
        1
        for entry in log.values()
        if entry.get("status") in (TOUCH_ENVIADO, TOUCH_RESPONDIDO)
    )


def _sequence_testing_allows_reset() -> bool:
    from app.services import outreach_metrics as om

    return om.is_sequence_testing_enabled()


def _reset_sequence_for_regenerate(db: Session, *, prospect: Prospect) -> None:
    """Limpia borrador y progreso de secuencia para volver a generar."""
    sent = _count_sent_touches(prospect)
    testing_reset = _sequence_testing_allows_reset()
    if sent > 0 and not testing_reset:
        raise HTTPException(
            status_code=400,
            detail=(
                "No se puede regenerar: ya hay toques enviados. "
                "Reejecutá un toque fallido con «Ejecutar toque» o probá con otro prospecto."
            ),
        )
    if sent > 0 and testing_reset:
        from sqlalchemy import delete

        from app.models.outreach import OutreachMessage

        db.execute(
            delete(OutreachMessage).where(
                OutreachMessage.prospect_id == prospect.id,
                OutreachMessage.is_testing.is_(True),
            )
        )
    prospect.sequence_playbook_draft = None
    prospect.sequence_touch_log = None
    prospect.playbook_name = None
    prospect.sequence_paused = False
    prospect.sequence_fired_milestones = "[]"
    prospect.next_touch_at = None
    if prospect.sequence_started_at is not None and (sent == 0 or testing_reset):
        prospect.sequence_started_at = None
        if prospect.ownership_status == ProspectOwnershipStatus.en_secuencia.value:
            prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    db.commit()
    db.refresh(prospect)


def explain_generate_sequence_block(
    user: User,
    prospect: Prospect,
    *,
    readiness: dict[str, Any] | None = None,
    force_regenerate: bool = False,
) -> str | None:
    """Motivo legible si no se puede generar secuencia; None si está permitido."""
    if not can_manage_outreach(user, prospect):
        return "No tenés permisos para gestionar outreach de este prospecto"
    status = own.effective_ownership_status(prospect)
    if force_regenerate:
        if status not in (
            ProspectOwnershipStatus.tomado.value,
            ProspectOwnershipStatus.en_secuencia.value,
        ):
            label = status.replace("_", " ")
            return f"No podés regenerar la secuencia en el estado actual ({label})."
        if _count_sent_touches(prospect) > 0 and not _sequence_testing_allows_reset():
            return (
                "Ya hay toques enviados — reejecutá un toque fallido o probá con otro prospecto."
            )
    elif status != ProspectOwnershipStatus.tomado.value:
        label = status.replace("_", " ")
        return (
            f"El prospecto debe estar Tomado para generar la secuencia "
            f"(estado actual: {label}). Tomalo desde la bandeja primero."
        )
    if _is_corrupt_draft_state(prospect):
        return None
    if _has_playbook_draft(prospect) and not force_regenerate:
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
        still = _channels_still_needed(channel_count)
        if still == 1:
            missing.append("1 canal más (email, LinkedIn o WhatsApp)")
        else:
            missing.append(f"{still} canales más (email, LinkedIn o WhatsApp)")

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


def compute_next_touch(prospect: Prospect, campaign: Campaign | None = None) -> tuple[datetime | None, str | None]:
    if prospect.sequence_started_at is None:
        return None, None
    done = _completed_days(prospect, campaign)
    pending = [d for d in _planned_days(prospect, campaign) if d not in done]
    if not pending:
        return None, "Secuencia completa"
    next_day = pending[0]
    start = prospect.sequence_started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    next_at = start + timedelta(days=max(0, next_day - 1))
    channel = next(
        (s.channel for s in _playbook_steps(campaign) if s.day == next_day),
        next((s.channel for s in DEFAULT_MVP_PLAYBOOK if s.day == next_day), "email"),
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
    campaign = None
    if prospect.campaign_id:
        campaign = getattr(prospect, "campaign", None)
        if campaign is None:
            campaign = db.get(Campaign, prospect.campaign_id)
    steps_plan = list(_playbook_steps(campaign))
    planned_days = tuple(s.day for s in steps_plan) or PLAYBOOK_DAYS

    done = _completed_days(prospect, campaign)
    next_day = next_executable_day(prospect, campaign)
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
    for day in planned_days:
        entry = log.get(str(day), {})
        msg_id = entry.get("message_id")
        if msg_id and int(msg_id) in msg_by_id:
            msg_by_day[day] = msg_by_id[int(msg_id)]

    fired_playbook = [d for d in planned_days if d in done and d not in msg_by_day]
    orphan_msgs = [m for m in outbound if m.id not in {x.id for x in msg_by_day.values()}]
    for i, day in enumerate(fired_playbook):
        if day not in msg_by_day and i < len(orphan_msgs):
            msg_by_day[day] = orphan_msgs[i]

    steps: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for step in steps_plan:
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

        from app.services.sequence_touch_gmail import sequence_email_touch_uses_gmail

        can_mark_sent = (
            touch_status == TOUCH_GENERADO
            and step.channel == "email"
            and sequence_email_touch_uses_gmail(day=day, channel=step.channel)
        )

        openai_last_error = entry.get("openai_last_error")
        generation_context = entry.get("generation_context")
        error_message = entry.get("error")
        validation_rejection = entry.get("validation_rejection")
        if day == next_day and entry.get("status") == TOUCH_PENDIENTE:
            error_message = None
            validation_rejection = None
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
            "error_message": error_message,
            "validation_rejection": validation_rejection,
            "openai_last_error": openai_last_error,
            "generation_context": generation_context,
            "fallback_test": bool(entry.get("fallback_test")),
            "can_execute": can_execute,
            "can_skip": can_skip,
            "can_mark_sent": can_mark_sent,
            "gmail_draft_id": entry.get("gmail_draft_id"),
            "gmail_web_link": entry.get("gmail_web_link"),
        }
        steps.append(step_data)
        if touch_status in (TOUCH_ENVIADO, TOUCH_RESPONDIDO, TOUCH_OMITIDO, TOUCH_FALLIDO):
            history.append(step_data)

    next_at, next_label = compute_next_touch(prospect, campaign)
    stored_next = prospect.next_touch_at or next_at
    current_day = next_day
    completed = _completed_days(prospect, campaign)
    last_completed_day = max(completed) if completed else None

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
        "last_completed_day": last_completed_day,
        "last_completed_day_label": f"Día {last_completed_day}" if last_completed_day else None,
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
                "last_completed_day": tracking.get("last_completed_day"),
                "last_completed_day_label": tracking.get("last_completed_day_label"),
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


def _build_preview_touch_body(
    *,
    step_day: int,
    channel: str,
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None,
    seller: User | None,
    prior: list[dict[str, Any]],
    step_objective: str,
    education: str,
) -> str:
    p_dict = _prospect_dict(prospect)
    c_dict = _campaign_dict(campaign, seller)
    pr_dict = _product_dict(product)
    if step_day == 1:
        if openai_configured():
            try:
                subj, body, _reason = sdr_pb.generate_sdr_playbook_touch(
                    channel=channel,
                    prospect=p_dict,
                    campaign=c_dict,
                    product=pr_dict,
                    education=education,
                    step_day=step_day,
                    step_objective=step_objective,
                    prior_touches=prior,
                    tone=campaign.tone or "",
                )
                if subj and channel == "email":
                    return f"Asunto: {subj}\n\n{body}"
                return body
            except HTTPException:
                raise
            except Exception:
                logger.warning(
                    "generate_sequence_preview day1_openai_fallback prospect_id=%s",
                    prospect.id,
                    exc_info=True,
                )
        from app.services.openai_fallback import apply_fallback_marker_to_body, build_sdr_playbook_fallback_json

        raw = build_sdr_playbook_fallback_json(
            channel=channel,
            prospect=p_dict,
            campaign=c_dict,
            product=pr_dict,
            step_day=step_day,
            step_objective=step_objective,
            prior_touches=prior,
        )
        data = json.loads(raw)
        body = apply_fallback_marker_to_body((data.get("body") or "").strip())
        if channel == "email" and (data.get("subject") or "").strip():
            return f"Asunto: {data['subject'].strip()}\n\n{body}"
        return body
    return _template_body(step_day, channel, prospect, product)


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
    campaign = getattr(prospect, "campaign", None)
    for playbook_step in _playbook_steps(campaign):
        touch = draft.get(playbook_step.day)
        if touch:
            touches_list.append(touch)
    # Conservar toques del draft que no estén en el plan actual (no perder historial).
    planned = {s.day for s in _playbook_steps(campaign)}
    for d, touch in sorted(draft.items()):
        if d not in planned and touch:
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
    step = _playbook_step(day, campaign)
    if step is None:
        raise HTTPException(status_code=400, detail="Toque inválido")

    seller = db.get(User, campaign.seller_id) if campaign.seller_id else None
    education = campaign_education_blob(db, campaign)
    from app.services.linkedin_sequence_policy import linkedin_mention_context
    from app.services.outreach_prospect_research import (
        ensure_outreach_research,
        extract_stored_research,
        resolve_research_depth,
    )

    depth = resolve_research_depth(
        day=day,
        prior_touches=prior,
        has_stored_brief=bool(extract_stored_research(prospect.notes)),
        prospect=prospect,
        campaign=campaign,
    )
    if depth != "skip":
        try:
            brief = ensure_outreach_research(
                db,
                prospect=prospect,
                campaign=campaign,
                product=product,
                force=False,
                depth=depth,
                prior_touches=prior,
                day=day,
            )
            if brief:
                education = (
                    f"{education}\n\n"
                    "INVESTIGACIÓN PREVIA DEL PROSPECTO (personalizá el ángulo; no inventes):\n"
                    f"{brief}"
                ).strip()
        except Exception:
            logger.exception(
                "outreach research failed prospect_id=%s day=%s",
                prospect.id,
                day,
            )

    mention = linkedin_mention_context(prospect, channel=step.channel)
    if mention:
        education = f"{education}\n\n{mention}".strip()
    fallback_used = False
    subj: str | None = None
    body = ""

    if not openai_configured():
        from app.services.openai_fallback import (
            apply_fallback_marker_to_body,
            build_sdr_playbook_fallback_json,
            is_openai_fallback_enabled,
        )

        if not is_openai_fallback_enabled():
            raise HTTPException(
                status_code=503,
                detail="OpenAI no está configurada. Definí OPENAI_API_KEY para generar mensajes reales.",
            )
        import json

        raw = build_sdr_playbook_fallback_json(
            channel=step.channel,
            prospect=_prospect_dict(prospect),
            campaign=_campaign_dict(campaign, seller),
            product=_product_dict(product),
            step_day=step.day,
            step_objective=step.objective,
            prior_touches=prior,
        )
        data = json.loads(raw)
        body = apply_fallback_marker_to_body((data.get("body") or "").strip())
        subj = (data.get("subject") or "").strip() or None
        fallback_used = True
    else:
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
    if force_regenerate and (_has_playbook_draft(prospect) or prospect.sequence_started_at is not None):
        _reset_sequence_for_regenerate(db, prospect=prospect)
        readiness = assess_outreach_readiness(db, prospect=prospect)
    block = explain_generate_sequence_block(
        user,
        prospect,
        readiness=readiness,
        force_regenerate=force_regenerate,
    )
    if block:
        raise HTTPException(status_code=403, detail=block)
    campaign = readiness.get("campaign")
    if campaign is None:
        raise HTTPException(status_code=400, detail="Asigná una campaña antes de generar la secuencia")
    product = readiness.get("product")
    if product is None:
        raise HTTPException(status_code=400, detail="La campaña debe tener un producto asociado")
    seller = db.get(User, campaign.seller_id) if campaign.seller_id else None
    # Preferí el usuario que genera (login) sobre el seller seed "Director Test".
    from app.services.outreach_display_names import sender_first_name

    sender = sender_first_name(
        user=user,
        campaign_sender=getattr(campaign, "sender_name", None),
        fallback="",
    )
    if not sender and seller is not None:
        sender = sender_first_name(user=seller, fallback="")
    if sender:
        campaign.sender_name = sender
    compose_as = user if sender_first_name(user=user, fallback="") else seller
    education = campaign_education_blob(db, campaign)

    touches: list[dict[str, Any]] = []
    prior: list[dict[str, Any]] = []
    for step in _playbook_steps(campaign):
        body = _build_preview_touch_body(
            step_day=step.day,
            channel=step.channel,
            prospect=prospect,
            campaign=campaign,
            product=product,
            seller=compose_as,
            prior=prior,
            step_objective=step.objective,
            education=education,
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
    _init_touch_log_generado(prospect, touches)
    db.commit()
    db.refresh(prospect)
    return {
        "prospect_id": prospect.id,
        "playbook_name": PLAYBOOK_NAME,
        "touches": touches,
    }


def bootstrap_sequence_scaffold_fast(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    product: Product | None = None,
) -> dict[str, Any]:
    """
    Arma draft + log de toques SIN OpenAI / SIN copy real.

    Contrato (mensajes bajo demanda): placeholders para el plan completo;
    el mensaje real se genera una sola vez al ejecutar el toque debido
    (kickoff día calendar-due, scheduler o execute manual). No pre-generar N toques.
    """
    if product is None and campaign.product_id:
        product = db.get(Product, int(campaign.product_id))
    touches: list[dict[str, Any]] = []
    for step in _playbook_steps(campaign):
        body = _template_body(step.day, step.channel, prospect, product)
        touches.append(
            {
                "day": step.day,
                "channel": step.channel,
                "objective": step.objective,
                "body_preview": body,
            }
        )
    prospect.sequence_playbook_draft = json.dumps(touches, ensure_ascii=False)
    prospect.playbook_name = PLAYBOOK_NAME
    _init_touch_log_generado(prospect, touches)
    db.flush()
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


def _prior_sent_touches(
    prospect: Prospect,
    before_day: int,
    campaign: Campaign | None = None,
) -> list[dict[str, Any]]:
    draft = _draft_by_day(prospect)
    log = _touch_log(prospect)
    prior: list[dict[str, Any]] = []
    for step in _playbook_steps(campaign):
        if step.day >= before_day:
            break
        if step.day not in _completed_days(prospect, campaign):
            continue
        touch = draft.get(step.day, {})
        entry = log.get(str(step.day), {})
        if entry.get("status") == TOUCH_OMITIDO:
            continue
        channel = (
            str(entry.get("channel") or touch.get("channel") or step.channel or "email")
            .strip()
            .lower()
        )
        _, body = _resolve_step_message(entry=entry, draft_touch=touch, msg=None)
        if not body:
            continue
        prior.append({"day": step.day, "channel": channel, "body": body})
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
        "validation_rejection": validation_rejection,
    }
    _remove_fired(prospect, day)
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

        campaign = db.get(Campaign, prospect.campaign_id)
        if campaign and getattr(campaign, "post_sequence_followup_enabled", True):
            from app.services import followup_engine

            followup_engine.schedule_followup_task(
                db,
                company_id=prospect.company_id,
                campaign_id=campaign.id,
                prospect_id=prospect.id,
                title="Último follow-up opcional (despedida)",
                campaign=campaign,
            )


def execute_sequence_touch(
    db: Session,
    *,
    user: User,
    prospect: Prospect,
    day: int,
    scheduled: bool = False,
) -> dict[str, Any]:
    campaign = _resolve_campaign(db, prospect)
    advance_auto_skipped_linkedin_touches(db, prospect=prospect, campaign=campaign)
    if scheduled:
        if campaign is None or not campaign.seller_id or user.id != campaign.seller_id:
            raise HTTPException(
                status_code=403,
                detail="Automatización: vendedor asignado inválido para este prospecto",
            )
        if prospect.owner_user_id and prospect.owner_user_id != user.id:
            raise HTTPException(
                status_code=403,
                detail="Automatización: el prospecto no pertenece al SDR de la campaña",
            )
        if not is_assisted_sequence_touch_due(prospect, day, campaign=campaign):
            raise HTTPException(
                status_code=400,
                detail="Toque aún no corresponde por calendario de secuencia",
            )
    elif not can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés ejecutar toques en este prospecto")
    if prospect.sequence_started_at is None:
        raise HTTPException(status_code=400, detail="Iniciá la secuencia antes de ejecutar toques")
    if prospect.sequence_paused:
        raise HTTPException(
            status_code=400,
            detail="La secuencia está pausada por respuesta del prospecto. Respondé antes de ejecutar más toques.",
        )
    if day not in _planned_days(prospect, campaign):
        raise HTTPException(status_code=400, detail="Día de secuencia inválido")

    nxt = next_executable_day(prospect, campaign)
    if nxt != day:
        raise HTTPException(
            status_code=400,
            detail=f"El próximo toque ejecutable es Día {nxt}" if nxt else "La secuencia ya está completa",
        )

    step = _playbook_step(day, campaign)
    if step is None:
        raise HTTPException(status_code=400, detail="Toque no encontrado en playbook")

    # Expirar Contactar a los 3 días (deja de usar LinkedIn; sigue email/WhatsApp).
    try:
        from app.services.linkedin_sequence_policy import refresh_linkedin_sequence_state

        if refresh_linkedin_sequence_state(prospect):
            db.flush()
    except Exception:  # noqa: BLE001
        pass

    # Si el canal del plan no está habilitado → omitir y seguir.
    # WhatsApp asistido NO requiere Meta Cloud API.
    if step.channel == "whatsapp":
        allowed = coerce_allowed_channels(
            getattr(campaign, "allowed_channels", None) if campaign else None
        )
        if "whatsapp" not in allowed:
            result = _auto_omit_sequence_touch(
                db,
                prospect=prospect,
                day=day,
                reason="whatsapp_pendiente",
            )
            db.commit()
            return {
                **result,
                "omitted": True,
                "channel": "whatsapp",
                "summary": (
                    "WhatsApp no está habilitado en esta campaña. "
                    "Día omitido; la secuencia sigue con los canales habilitados."
                ),
            }
    elif campaign is not None:
        allowed = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
        if allowed and step.channel not in allowed:
            result = _auto_omit_sequence_touch(
                db,
                prospect=prospect,
                day=day,
                reason="canal_no_habilitado",
            )
            db.commit()
            return {
                **result,
                "omitted": True,
                "channel": step.channel,
                "summary": f"Canal {step.channel} no habilitado en la campaña; toque omitido.",
            }

    if not _channel_ready(prospect, step.channel):
        result = _auto_omit_sequence_touch(
            db,
            prospect=prospect,
            day=day,
            reason=f"{step.channel}_sin_dato",
        )
        db.commit()
        tracking = build_sequence_tracking(db, prospect=prospect)
        return {
            **result,
            "omitted": True,
            "channel": step.channel,
            "summary": (
                f"Canal {step.channel} no disponible para este prospecto; "
                "día omitido y la secuencia sigue."
            ),
            "tracking": tracking,
        }

    existing_entry = _touch_log(prospect).get(str(day), {})
    # No resetear un WhatsApp ya marcado por el SDR (evita regenerar el frío).
    wa_sdr_sent = (
        step.channel == "whatsapp"
        and bool(getattr(prospect, "whatsapp_sdr_marked_sent_at", None))
        and not (prospect.whatsapp_assisted_draft or "").strip()
    )
    if not wa_sdr_sent:
        _maybe_reset_retryable_touch(prospect, day)
        existing_entry = _touch_log(prospect).get(str(day), {})

    if step.channel == "linkedin" and existing_entry.get("status") == TOUCH_GENERADO:
        has_queue_body = bool(
            (prospect.linkedin_assisted_draft or "").strip()
            or (existing_entry.get("message_body") or "").strip()
            or (existing_entry.get("body") or "").strip()
        )
        if has_queue_body:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Este toque LinkedIn ya está en la cola. "
                    "Usá «Enviar mensaje» en Centro de outreach y marcá como enviado."
                ),
            )

    if step.channel == "whatsapp" and existing_entry.get("status") == TOUCH_GENERADO:
        # Ya marcado enviado por el SDR pero el log quedó en generado → cerrar, no regenerar frío.
        if getattr(prospect, "whatsapp_sdr_marked_sent_at", None) and not (
            prospect.whatsapp_assisted_draft or ""
        ).strip():
            closed = complete_pending_whatsapp_sequence_touch(db, prospect=prospect)
            db.commit()
            tracking = build_sequence_tracking(db, prospect=prospect)
            return {
                "prospect_id": prospect.id,
                "day": closed or day,
                "channel": "whatsapp",
                "whatsapp_assisted": True,
                "skipped": True,
                "already_sent": True,
                "message": "Toque WhatsApp ya confirmado como enviado; no se regenera el frío.",
                "tracking": tracking,
            }
        has_wa_body = bool(
            (prospect.whatsapp_assisted_draft or "").strip()
            or (existing_entry.get("message_body") or "").strip()
            or (existing_entry.get("body") or "").strip()
        )
        if has_wa_body:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Este toque WhatsApp ya está en la cola. "
                    "Usá «Enviar WhatsApp» en Centro de outreach y marcá como enviado."
                ),
            )

    readiness = assess_outreach_readiness(db, prospect=prospect)
    campaign = readiness.get("campaign") or campaign
    product = readiness.get("product")
    if campaign is None:
        raise HTTPException(status_code=400, detail="Campaña no configurada")

    draft = _draft_by_day(prospect)
    from app.services import followup_engine
    from app.services import outreach_simulation as sim

    now = _now()
    had_linkedin_mention = (
        getattr(prospect, "linkedin_mention_next_touch", False) and step.channel != "linkedin"
    )

    # LinkedIn: LI-SAFE = borrador a cola sin verify 1º. Legacy = verify primero.
    if step.channel == "linkedin":
        from app.services.linkedin_assisted_service import (
            CONN_INVITE_PENDING,
            CONN_INVITE_SENT,
            LI_SAFE_NO_PROFILE_PROBE,
            is_real_linkedin_profile_url,
            mark_connection_check_pending,
            read_connection_status,
        )
        from app.services.linkedin_sequence_policy import (
            is_linkedin_connected,
            linkedin_connect_failed,
        )

        if not is_real_linkedin_profile_url(prospect.linkedin_url):
            raise HTTPException(
                status_code=400,
                detail="LinkedIn personal real requerido (linkedin.com/in/...).",
            )
        if linkedin_connect_failed(prospect):
            result = _auto_omit_sequence_touch(
                db,
                prospect=prospect,
                day=day,
                reason="linkedin_sin_conexion",
            )
            db.commit()
            tracking = build_sequence_tracking(db, prospect=prospect)
            return {
                "prospect_id": prospect.id,
                "day": day,
                "channel": step.channel,
                "linkedin_assisted": False,
                "skipped": True,
                "omitted": True,
                "message": result["message"],
                "tracking": tracking,
            }
        if not LI_SAFE_NO_PROFILE_PROBE:
            conn = read_connection_status(prospect)
            if conn in ("checking", "check_queued"):
                # Ya en checking o en cola lenta: no re-marcar.
                # Si aún no hay texto, lo generamos ahora para que el SDR lo vea armado.
                if not (prospect.linkedin_assisted_draft or "").strip():
                    try:
                        content = _generate_real_touch_content(
                            db,
                            prospect=prospect,
                            campaign=campaign,
                            product=product,
                            day=day,
                            prior=_prior_sent_touches(prospect, day, campaign),
                        )
                        draft_body = (content.get("message_body") or content.get("body") or "").strip()
                        if draft_body:
                            prospect.linkedin_assisted_draft = draft_body
                            _persist_touch_draft(prospect, _draft_by_day(prospect), content)
                            log = _touch_log(prospect)
                            entry = dict(log.get(str(day)) or {})
                            entry.update(
                                {
                                    "status": TOUCH_GENERADO,
                                    "message_body": draft_body,
                                    "body": content.get("body"),
                                    "subject": content.get("subject"),
                                    "awaiting_connection_check": True,
                                    "error": None,
                                }
                            )
                            log[str(day)] = entry
                            _save_touch_log(prospect, log)
                            db.commit()
                    except Exception:
                        pass
                tracking = build_sequence_tracking(db, prospect=prospect)
                return {
                    "prospect_id": prospect.id,
                    "day": day,
                    "channel": "linkedin",
                    "linkedin_assisted": True,
                    "pending_verify": True,
                    "message": (
                        "Verificando si ya es contacto en LinkedIn…"
                        if conn == "checking"
                        else "En cola de verificación LinkedIn (de a uno)…"
                    ),
                    "tracking": tracking,
                }
            if (
                not is_linkedin_connected(prospect)
                and conn not in (CONN_INVITE_PENDING, CONN_INVITE_SENT)
            ):
                # Armar el mensaje YA (antes de verificar 1º grado) para que la cola
                # tenga texto listo: Conectar o Enviar mensaje.
                draft_body = (prospect.linkedin_assisted_draft or "").strip() or None
                content_preview: dict[str, Any] | None = None
                if not draft_body:
                    content_preview = _generate_real_touch_content(
                        db,
                        prospect=prospect,
                        campaign=campaign,
                        product=product,
                        day=day,
                        prior=_prior_sent_touches(prospect, day, campaign),
                    )
                    draft_body = (
                        content_preview.get("message_body")
                        or content_preview.get("body")
                        or ""
                    ).strip() or None
                    if content_preview:
                        _persist_touch_draft(prospect, _draft_by_day(prospect), content_preview)
                mark_connection_check_pending(
                    db,
                    prospect,
                    campaign,
                    log_event=True,
                    pending_draft=draft_body,
                )
                log = _touch_log(prospect)
                log[str(day)] = {
                    **(log.get(str(day)) or {}),
                    "status": TOUCH_GENERADO,
                    "sent_at": None,
                    "message_id": None,
                    "subject": (content_preview or {}).get("subject") if content_preview else None,
                    "message_body": draft_body,
                    "body": (content_preview or {}).get("body") if content_preview else draft_body,
                    "error": None,
                    "awaiting_connection_check": True,
                }
                _save_touch_log(prospect, log)
                db.commit()
                tracking = build_sequence_tracking(db, prospect=prospect)
                return {
                    "prospect_id": prospect.id,
                    "day": day,
                    "channel": "linkedin",
                    "linkedin_assisted": True,
                    "pending_verify": True,
                    "message": (
                        "Mensaje LinkedIn armado. Verificando si ya son contacto… "
                        "En segundos aparece Enviar mensaje o Enviar Contactar."
                    ),
                    "tracking": tracking,
                }

    try:
        content = _generate_real_touch_content(
            db,
            prospect=prospect,
            campaign=campaign,
            product=product,
            day=day,
            prior=_prior_sent_touches(prospect, day, campaign),
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
                "prior_touch_count": len(_prior_sent_touches(prospect, day, campaign)),
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

    from app.services import outreach_metrics as om
    from app.services.sequence_touch_gmail import (
        deliver_sequence_email_touch_via_gmail,
        sequence_email_touch_uses_gmail,
    )

    message_body = content["message_body"]
    if had_linkedin_mention:
        from app.services.linkedin_sequence_policy import consume_linkedin_mention_flag

        consume_linkedin_mention_flag(prospect)
    _persist_touch_draft(prospect, draft, content)

    if content.get("fallback_test") and om.is_real_mode() and sequence_email_touch_uses_gmail(
        day=day, channel=step.channel
    ):
        _mark_touch_failed(
            prospect,
            day,
            "OpenAI devolvió mensaje de prueba (FALLBACK TEST). No se envió por el canal real. Reintentá.",
        )
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={
                "message": "OpenAI en modo fallback — no se envió el toque real.",
                "summary": "Reintentá en unos segundos para generar y enviar el mensaje real.",
                "retryable": True,
            },
        )

    gmail_delivery: dict[str, Any] | None = None
    if sequence_email_touch_uses_gmail(day=day, channel=step.channel):
        gmail_delivery = deliver_sequence_email_touch_via_gmail(
            db,
            user=user,
            campaign=campaign,
            prospect=prospect,
            day=day,
            subject=str(content.get("subject") or ""),
            body=str(content.get("body") or ""),
        )

    whatsapp_delivery: dict[str, Any] | None = None
    linkedin_assisted = False
    whatsapp_assisted = False
    call_assisted = False
    gmail_assisted = False  # borrador Gmail pendiente de envío manual
    gmail_sent_now = False  # auto_send: ya salió por Gmail API
    msg = None
    try:
        if gmail_delivery is not None:
            msg_id = int(gmail_delivery["message_id"])
            msg = db.get(OutreachMessage, msg_id)
            if msg is None:
                raise RuntimeError("No se encontró el mensaje outbound tras crear borrador Gmail")
            if gmail_delivery.get("sent"):
                gmail_sent_now = True
            else:
                gmail_assisted = True
        elif step.channel == "linkedin":
            from app.services.linkedin_assisted_service import (
                is_real_linkedin_profile_url,
                queue_linkedin_sequence_touch,
            )

            if not is_real_linkedin_profile_url(prospect.linkedin_url):
                raise HTTPException(
                    status_code=400,
                    detail="LinkedIn personal real requerido (linkedin.com/in/...).",
                )
            li_action = queue_linkedin_sequence_touch(
                db, prospect, campaign, message_body, log_event=True
            )
            if li_action == "skip":
                result = _auto_omit_sequence_touch(
                    db,
                    prospect=prospect,
                    day=day,
                    reason="linkedin_sin_conexion",
                )
                db.commit()
                tracking = build_sequence_tracking(db, prospect=prospect)
                return {
                    "prospect_id": prospect.id,
                    "day": day,
                    "channel": step.channel,
                    "linkedin_assisted": False,
                    "skipped": True,
                    "message": result["message"],
                    "tracking": tracking,
                }
            if li_action == "hold":
                # Compat: ya no debería ocurrir (invite_sent → message).
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Mensaje LinkedIn en cola: envialo cuando el contacto acepte. "
                        "Si ya pasó el plazo, la secuencia sigue por otros canales."
                    ),
                )
            # connect | message | checking: toque en cola (verificación o envío manual).
            linkedin_assisted = True
        elif step.channel == "whatsapp":
            from app.services.whatsapp_assisted_service import (
                prospect_whatsapp_digits,
                queue_whatsapp_sequence_touch,
            )

            wa_action = queue_whatsapp_sequence_touch(
                db, prospect, campaign, message_body, log_event=True
            )
            if wa_action == "skip":
                if not prospect_whatsapp_digits(prospect):
                    result = _auto_omit_sequence_touch(
                        db,
                        prospect=prospect,
                        day=day,
                        reason="whatsapp_sin_dato",
                    )
                    db.commit()
                    tracking = build_sequence_tracking(db, prospect=prospect)
                    return {
                        "prospect_id": prospect.id,
                        "day": day,
                        "channel": step.channel,
                        "whatsapp_assisted": False,
                        "skipped": True,
                        "omitted": True,
                        "message": result["message"],
                        "tracking": tracking,
                    }
                raise HTTPException(
                    status_code=400,
                    detail="No se pudo generar el borrador de WhatsApp. Reintentá el toque.",
                )
            whatsapp_assisted = True
        elif step.channel == "call":
            from app.services.call_assisted_service import (
                prospect_has_callable_number,
                queue_call_sequence_touch,
            )

            call_action = queue_call_sequence_touch(
                db, prospect, campaign, message_body, log_event=True
            )
            if call_action == "skip":
                if not prospect_has_callable_number(prospect):
                    result = _auto_omit_sequence_touch(
                        db,
                        prospect=prospect,
                        day=day,
                        reason="call_sin_dato",
                    )
                    db.commit()
                    tracking = build_sequence_tracking(db, prospect=prospect)
                    return {
                        "prospect_id": prospect.id,
                        "day": day,
                        "channel": step.channel,
                        "call_assisted": False,
                        "skipped": True,
                        "omitted": True,
                        "message": result["message"],
                        "tracking": tracking,
                    }
                raise HTTPException(
                    status_code=400,
                    detail="No se pudo preparar el guion de llamada. Reintentá el toque.",
                )
            call_assisted = True
        else:
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
        if linkedin_assisted or gmail_assisted or whatsapp_assisted or call_assisted:
            prev_touch = _touch_entry(prospect, day)
            touch_fields: dict[str, Any] = {
                "status": TOUCH_GENERADO,
                "sent_at": None,
                "message_id": msg.id if msg is not None else None,
                "subject": content.get("subject"),
                "message_body": message_body,
                "body": content.get("body"),
                "error": None,
                "openai_last_error": None,
                "generation_context": None,
                "fallback_test": bool(content.get("fallback_test")),
            }
            if linkedin_assisted or whatsapp_assisted or call_assisted:
                touch_fields["generated_at"] = prev_touch.get("generated_at") or now.isoformat()
                touch_fields["channel"] = step.channel
            if gmail_assisted:
                touch_fields["gmail_draft_id"] = (gmail_delivery or {}).get("gmail_draft_id")
                touch_fields["gmail_message_id"] = (gmail_delivery or {}).get("gmail_message_id")
                touch_fields["gmail_web_link"] = (gmail_delivery or {}).get("gmail_web_link")
            _set_touch_entry(prospect, day, **touch_fields)
        else:
            _append_fired(prospect, day)
            _set_touch_entry(
                prospect,
                day,
                status=TOUCH_ENVIADO,
                sent_at=now.isoformat(),
                message_id=msg.id if msg is not None else None,
                subject=content.get("subject"),
                message_body=message_body,
                body=content.get("body"),
                error=None,
                openai_last_error=None,
                generation_context=None,
                fallback_test=bool(content.get("fallback_test")),
                whatsapp_message_id=(whatsapp_delivery or {}).get("whatsapp_message_id"),
                gmail_message_id=(gmail_delivery or {}).get("gmail_message_id"),
            )
            try:
                from app.services.crm import sync as crm_sync

                crm_sync.sync_touch_sent(
                    db,
                    prospect=prospect,
                    day=day,
                    channel=step.channel,
                    message_body=message_body,
                )
            except Exception:
                pass
        next_at, next_label = compute_next_touch(prospect)
        prospect.next_touch_at = next_at
        _sync_sequence_completion(db, prospect=prospect)
        db.commit()
        db.refresh(prospect)
    except HTTPException:
        # Control de flujo intencional (p. ej. 409 "esperando aceptación de conexión",
        # 429 límite diario): no marcar el toque como fallido.
        db.rollback()
        raise
    except Exception as exc:
        _mark_touch_failed(prospect, day, f"No se pudo registrar el borrador: {exc}")
        db.commit()
        raise HTTPException(status_code=502, detail=f"No se pudo preparar el toque: {exc}") from exc

    tracking = build_sequence_tracking(db, prospect=prospect)
    if linkedin_assisted:
        touch_message = (
            f"Día {day} listo en cola LinkedIn. En Centro de outreach: "
            "si hace falta, primero «Conectar»; después el mensaje preparado "
            "(el texto ya está compuesto según el orden del toque)."
        )
        return {
            "prospect_id": prospect.id,
            "day": day,
            "channel": step.channel,
            "touch_status": TOUCH_GENERADO,
            "status_label": "Listo en LinkedIn",
            "fallback_test": bool(content.get("fallback_test")),
            "gmail_sent": False,
            "gmail_draft_created": False,
            "gmail_message_id": None,
            "linkedin_assisted": True,
            "whatsapp_assisted": False,
            "message": touch_message,
            "tracking": tracking,
        }

    if whatsapp_assisted:
        phone = (prospect.whatsapp or prospect.phone or "").strip()
        touch_message = (
            f"Día {day} listo en cola WhatsApp ({phone}). "
            "En Centro de outreach → Notificaciones WhatsApp: "
            "abrí el chat, enviá manualmente y confirmá el envío."
        )
        return {
            "prospect_id": prospect.id,
            "day": day,
            "channel": step.channel,
            "touch_status": TOUCH_GENERADO,
            "status_label": "Listo en WhatsApp",
            "fallback_test": bool(content.get("fallback_test")),
            "gmail_sent": False,
            "gmail_draft_created": False,
            "gmail_message_id": None,
            "linkedin_assisted": False,
            "whatsapp_assisted": True,
            "call_assisted": False,
            "message": touch_message,
            "tracking": tracking,
        }

    if call_assisted:
        from app.services.call_assisted_service import prospect_call_target

        _, call_kind, call_display = prospect_call_target(prospect)
        kind_label = "fijo" if call_kind == "landline" else "celular"
        touch_message = (
            f"Día {day} — tenés que llamar hoy al {kind_label} {call_display}. "
            "En Centro de outreach → Llamadas: seguí el guion y marcá como hecha."
        )
        return {
            "prospect_id": prospect.id,
            "day": day,
            "channel": step.channel,
            "touch_status": TOUCH_GENERADO,
            "status_label": "Llamada pendiente",
            "fallback_test": bool(content.get("fallback_test")),
            "gmail_sent": False,
            "gmail_draft_created": False,
            "gmail_message_id": None,
            "linkedin_assisted": False,
            "whatsapp_assisted": False,
            "call_assisted": True,
            "message": touch_message,
            "tracking": tracking,
        }

    if gmail_assisted:
        touch_message = (
            f"Día {day} — borrador creado en Gmail para {(prospect.email or '').strip()}. "
            "Revisá Borradores, enviá manualmente y marcá como enviado en Nexus."
        )
        return {
            "prospect_id": prospect.id,
            "day": day,
            "channel": step.channel,
            "touch_status": TOUCH_GENERADO,
            "status_label": "Borrador en Gmail",
            "fallback_test": bool(content.get("fallback_test")),
            "gmail_sent": False,
            "gmail_draft_created": True,
            "gmail_message_id": (gmail_delivery or {}).get("gmail_message_id"),
            "gmail_draft_id": (gmail_delivery or {}).get("gmail_draft_id"),
            "gmail_web_link": (gmail_delivery or {}).get("gmail_web_link"),
            "linkedin_assisted": False,
            "message": touch_message,
            "tracking": tracking,
        }

    sent_label = "FALLBACK TEST" if content.get("fallback_test") else TOUCH_STATUS_LABELS[TOUCH_ENVIADO]
    if gmail_sent_now:
        to_email = (prospect.email or "").strip()
        touch_message = f"Día {day} — email enviado por Gmail a {to_email}."
    elif whatsapp_delivery is not None:
        phone = (prospect.whatsapp or prospect.phone or "").strip()
        if whatsapp_delivery.get("whatsapp_dry_run"):
            touch_message = (
                f"Día {day} — WhatsApp simulado (WHATSAPP_DRY_RUN) a {phone}. "
                "Listo para Meta real."
            )
        else:
            touch_message = f"Día {day} enviado por WhatsApp real a {phone}"
    elif content.get("fallback_test"):
        touch_message = (
            f"Día {day} enviado con mensaje mock (FALLBACK TEST) — OpenAI en rate limit"
        )
    else:
        touch_message = f"Día {day} enviado por {step.channel} (simulado en Nexus)"
    return {
        "prospect_id": prospect.id,
        "day": day,
        "channel": step.channel,
        "touch_status": TOUCH_ENVIADO,
        "status_label": sent_label,
        "fallback_test": bool(content.get("fallback_test")),
        "gmail_sent": bool(gmail_sent_now),
        "gmail_draft_created": False,
        "gmail_message_id": (gmail_delivery or {}).get("gmail_message_id") if gmail_sent_now else None,
        "gmail_web_link": (gmail_delivery or {}).get("gmail_web_link") if gmail_sent_now else None,
        "whatsapp_sent": whatsapp_delivery is not None,
        "whatsapp_dry_run": bool((whatsapp_delivery or {}).get("whatsapp_dry_run")),
        "whatsapp_message_id": (whatsapp_delivery or {}).get("whatsapp_message_id"),
        "message": touch_message,
        "tracking": tracking,
    }


def complete_pending_linkedin_sequence_touch(
    db: Session,
    *,
    prospect: Prospect,
    sent_at: datetime | None = None,
) -> int | None:
    """Tras confirmar envío manual en LinkedIn, avanza el toque de secuencia pendiente."""
    log = _touch_log(prospect)
    when = sent_at or _now()
    campaign = _resolve_campaign(db, prospect)
    chosen_day: int | None = None
    for day in _planned_days(prospect, campaign):
        entry = log.get(str(day), {})
        if entry.get("status") != TOUCH_GENERADO:
            continue
        step = _playbook_step(day, campaign)
        if step is None or step.channel != "linkedin":
            continue
        chosen_day = day
        break
    if chosen_day is None:
        return None
    _append_fired(prospect, chosen_day)
    _set_touch_entry(
        prospect,
        chosen_day,
        status=TOUCH_ENVIADO,
        sent_at=when.isoformat(),
        error=None,
    )
    entry = log.get(str(chosen_day), {})
    try:
        from app.services.crm import sync as crm_sync

        crm_sync.sync_touch_sent(
            db,
            prospect=prospect,
            day=chosen_day,
            channel="linkedin",
            message_body=entry.get("message_body") or entry.get("body"),
        )
    except Exception:
        pass
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    _sync_sequence_completion(db, prospect=prospect)
    return chosen_day


def complete_pending_whatsapp_sequence_touch(
    db: Session,
    *,
    prospect: Prospect,
    sent_at: datetime | None = None,
) -> int | None:
    """Tras confirmar envío manual en WhatsApp Web, avanza el toque de secuencia pendiente."""
    log = _touch_log(prospect)
    when = sent_at or _now()
    campaign = _resolve_campaign(db, prospect)
    chosen_day: int | None = None
    for day in _planned_days(prospect, campaign):
        entry = log.get(str(day), {})
        if entry.get("status") != TOUCH_GENERADO:
            continue
        step = _playbook_step(day, campaign)
        if step is None or step.channel != "whatsapp":
            continue
        chosen_day = day
        break
    if chosen_day is None:
        return None
    _append_fired(prospect, chosen_day)
    _set_touch_entry(
        prospect,
        chosen_day,
        status=TOUCH_ENVIADO,
        sent_at=when.isoformat(),
        error=None,
        whatsapp_assisted_sent=True,
        sdr_marked_sent=True,
    )
    entry = log.get(str(chosen_day), {})
    try:
        from app.services.crm import sync as crm_sync

        crm_sync.sync_touch_sent(
            db,
            prospect=prospect,
            day=chosen_day,
            channel="whatsapp",
            message_body=entry.get("message_body") or entry.get("body"),
        )
    except Exception:
        pass
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    _sync_sequence_completion(db, prospect=prospect)
    return chosen_day


def complete_pending_call_sequence_touch(
    db: Session,
    *,
    prospect: Prospect,
    sent_at: datetime | None = None,
) -> int | None:
    """Tras confirmar llamada manual, avanza el toque de secuencia pendiente."""
    log = _touch_log(prospect)
    when = sent_at or _now()
    campaign = _resolve_campaign(db, prospect)
    chosen_day: int | None = None
    for day in _planned_days(prospect, campaign):
        entry = log.get(str(day), {})
        if entry.get("status") != TOUCH_GENERADO:
            continue
        step = _playbook_step(day, campaign)
        ch = str(entry.get("channel") or (getattr(step, "channel", None) if step else "") or "").strip().lower()
        if ch != "call":
            continue
        chosen_day = day
        break
    if chosen_day is None:
        return None
    _append_fired(prospect, chosen_day)
    _set_touch_entry(
        prospect,
        chosen_day,
        status=TOUCH_ENVIADO,
        sent_at=when.isoformat(),
        error=None,
        call_assisted_sent=True,
        sdr_marked_sent=True,
    )
    entry = log.get(str(chosen_day), {})
    try:
        from app.services.crm import sync as crm_sync

        crm_sync.sync_touch_sent(
            db,
            prospect=prospect,
            day=chosen_day,
            channel="call",
            message_body=entry.get("message_body") or entry.get("body"),
        )
    except Exception:
        pass
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    _sync_sequence_completion(db, prospect=prospect)
    return chosen_day


def mark_sequence_gmail_touch_sent(
    db: Session,
    *,
    user: User,
    prospect: Prospect,
    day: int,
    auto_detected: bool = False,
) -> dict[str, Any]:
    """Confirma envío del borrador Gmail del toque (manual o detectado por sync)."""
    if not auto_detected and not can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés marcar toques en este prospecto")
    if day not in _planned_days(prospect):
        raise HTTPException(status_code=400, detail="Día de secuencia inválido")

    campaign = _resolve_campaign(db, prospect)
    step = _playbook_step(day, campaign)
    if step is None or step.channel != "email":
        raise HTTPException(status_code=400, detail="Este toque no es un email de secuencia")

    from app.services.sequence_touch_gmail import sequence_email_touch_uses_gmail

    if not sequence_email_touch_uses_gmail(day=day, channel=step.channel):
        raise HTTPException(status_code=400, detail="Este toque no usa borrador Gmail")

    entry = _touch_entry(prospect, day)
    if entry.get("status") != TOUCH_GENERADO:
        raise HTTPException(
            status_code=400,
            detail="No hay borrador Gmail pendiente para este toque.",
        )

    message_body = str(entry.get("message_body") or entry.get("body") or "").strip()
    if not message_body and not entry.get("gmail_draft_id"):
        raise HTTPException(
            status_code=400,
            detail="Ejecutá el toque primero para crear el borrador en Gmail.",
        )

    now = _now()
    _append_fired(prospect, day)
    sent_fields: dict[str, Any] = {
        "status": TOUCH_ENVIADO,
        "sent_at": now.isoformat(),
        "error": None,
    }
    if auto_detected:
        sent_fields["gmail_auto_detected"] = True
    else:
        sent_fields["gmail_manually_sent"] = True
    _set_touch_entry(prospect, day, **sent_fields)

    from app.services import followup_engine

    readiness = assess_outreach_readiness(db, prospect=prospect)
    campaign = readiness.get("campaign")
    if campaign is not None and message_body:
        followup_engine.record_ai_outbound(
            db,
            prospect,
            campaign_calendar_link=campaign.calendar_link or "",
            outbound_text=message_body,
        )
        try:
            from app.services.crm import sync as crm_sync

            crm_sync.sync_touch_sent(
                db,
                prospect=prospect,
                day=day,
                channel="email",
                message_body=message_body,
            )
        except Exception:
            pass

    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    _sync_sequence_completion(db, prospect=prospect)
    db.commit()
    db.refresh(prospect)
    tracking = build_sequence_tracking(db, prospect=prospect)
    return {
        "prospect_id": prospect.id,
        "day": day,
        "touch_status": TOUCH_ENVIADO,
        "status_label": TOUCH_STATUS_LABELS[TOUCH_ENVIADO],
        "message": (
            f"Día {day} detectado como enviado en Gmail."
            if auto_detected
            else f"Día {day} marcado como enviado. Podés continuar con el próximo toque."
        ),
        "fallback_test": bool(entry.get("fallback_test")),
        "gmail_sent": False,
        "gmail_draft_created": False,
        "gmail_message_id": entry.get("gmail_message_id"),
        "tracking": tracking,
    }


def _auto_omit_sequence_touch(
    db: Session,
    *,
    prospect: Prospect,
    day: int,
    reason: str,
) -> dict[str, Any]:
    """Omite un toque sin chequeo de permisos (mantenimiento automático de secuencia)."""
    now = _now()
    _set_touch_entry(
        prospect,
        day,
        status=TOUCH_OMITIDO,
        skipped_at=now.isoformat(),
        skip_reason=reason,
    )
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at
    _sync_sequence_completion(db, prospect=prospect)
    return {
        "prospect_id": prospect.id,
        "day": day,
        "touch_status": TOUCH_OMITIDO,
        "message": f"Día {day} omitido automáticamente ({reason})",
    }


def _assisted_touch_was_sent(prospect: Prospect, channel: str, entry: dict[str, Any]) -> bool:
    if entry.get("sdr_marked_sent") or entry.get("whatsapp_assisted_sent") or entry.get("call_assisted_sent"):
        return True
    if channel == "linkedin" and getattr(prospect, "linkedin_sdr_marked_sent_at", None):
        return True
    if channel == "whatsapp" and getattr(prospect, "whatsapp_sdr_marked_sent_at", None):
        return True
    if channel == "call" and getattr(prospect, "call_sdr_marked_done_at", None):
        return True
    return False


def _clear_assisted_live_queue(prospect: Prospect, channel: str) -> None:
    """Saca el toque de la bandeja; el texto queda en el touch log (historial)."""
    if channel == "linkedin":
        prospect.linkedin_assisted_draft = None
        prospect.linkedin_assist_session_id = None
        if (getattr(prospect, "linkedin_assist_status", None) or "").strip().lower() != "sent":
            prospect.linkedin_assist_status = None
        return
    if channel == "whatsapp":
        prospect.whatsapp_assisted_draft = None
        prospect.whatsapp_assist_session_id = None
        if (getattr(prospect, "whatsapp_assist_status", None) or "").strip().lower() != "sent":
            prospect.whatsapp_assist_status = None
        return
    if channel == "call":
        prospect.call_assisted_brief = None
        from app.services.call_assisted_service import read_assist_status

        if read_assist_status(prospect) != "done":
            prospect.call_assist_status = None


def _assisted_queue_is_live(prospect: Prospect, channel: str) -> bool:
    """True si el SDR todavía tiene el toque asistido en bandeja."""
    ch = (channel or "").strip().lower()
    if ch == "linkedin":
        draft = (getattr(prospect, "linkedin_assisted_draft", None) or "").strip()
        if not draft:
            return False
        status = (getattr(prospect, "linkedin_assist_status", None) or "").strip().lower()
        return status in {"", "none", "suggested", "prepared", "opened", "checking"}
    if ch == "whatsapp":
        draft = (getattr(prospect, "whatsapp_assisted_draft", None) or "").strip()
        if not draft:
            return False
        status = (getattr(prospect, "whatsapp_assist_status", None) or "").strip().lower()
        return status in {"", "none", "suggested", "prepared", "opened"}
    if ch == "call":
        brief = (getattr(prospect, "call_assisted_brief", None) or "").strip()
        if not brief:
            return False
        from app.services.call_assisted_service import read_assist_status

        return read_assist_status(prospect) in {"", "none", "suggested"}
    return False


_CONVERSATION_HOLD_GROUPS = frozenset({"encajonado", "postergado", "reuniones"})


def _sequence_held_for_conversation(prospect: Prospect) -> bool:
    if bool(getattr(prospect, "sequence_paused", False)):
        return True
    group = str(getattr(prospect, "sequence_group", None) or "").strip().lower()
    return group in _CONVERSATION_HOLD_GROUPS


def _assisted_queue_started_at(
    prospect: Prospect, entry: dict[str, Any], channel: str
) -> datetime | None:
    generated = _parse_dt(entry.get("generated_at"))
    if generated is not None:
        return generated
    if channel == "linkedin":
        assisted = _parse_dt(getattr(prospect, "linkedin_last_assisted_at", None))
        if assisted is not None:
            return assisted
    if channel == "whatsapp":
        assisted = _parse_dt(getattr(prospect, "whatsapp_last_assisted_at", None))
        if assisted is not None:
            return assisted
    return None


def assisted_next_touch_due_at(
    prospect: Prospect,
    day: int,
    campaign: Campaign | None = None,
) -> datetime | None:
    """
    Momento exacto en que el toque `day` puede generarse / el anterior debe salir.

    - Primer toque del plan: sequence_started_at + (day-1) días (misma hora).
      Ej. inicio vie 17:00 → día 4 el lun 17:00.
    - Toques siguientes: generated_at del toque anterior + 3 días (misma hora).
    """
    from app.services.linkedin_sequence_policy import ASSISTED_QUEUE_TTL_DAYS

    started = prospect.sequence_started_at
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)

    planned = list(_planned_days(prospect, campaign))
    if day not in planned:
        planned = sorted(set(planned) | {day})
    prev = max((d for d in planned if d < day), default=None)
    if prev is None:
        return started + timedelta(days=max(0, day - 1))

    log = _touch_log(prospect)
    prev_entry = log.get(str(prev), {}) if isinstance(log.get(str(prev)), dict) else {}
    step = _playbook_step(prev, campaign)
    prev_channel = str(
        prev_entry.get("channel")
        or (getattr(step, "channel", None) if step else "")
        or ""
    ).strip().lower()
    anchor = _assisted_queue_started_at(prospect, prev_entry, prev_channel)
    if anchor is None:
        anchor = (
            _parse_dt(prev_entry.get("sent_at"))
            or _parse_dt(prev_entry.get("skipped_at"))
            or started
        )
    first = min(planned) if planned else prev
    if prev == first:
        # Primer tramo: ancla = inicio de secuencia (no generated_at tardío).
        return started + timedelta(days=ASSISTED_QUEUE_TTL_DAYS)
    return anchor + timedelta(days=ASSISTED_QUEUE_TTL_DAYS)


def is_assisted_sequence_touch_due(
    prospect: Prospect,
    day: int,
    *,
    campaign: Campaign | None = None,
    now: datetime | None = None,
) -> bool:
    now = now or _now()
    due_at = assisted_next_touch_due_at(prospect, day, campaign)
    if due_at is None:
        return False
    return due_at <= now


def ensure_single_assisted_live_queue(
    prospect: Prospect,
    campaign: Campaign | None = None,
) -> bool:
    """
    Nunca LI y WA juntos en bandeja.
    Conversación (paused/reuniones):
      - solo borrador WA → se conserva (Responder tras inbound WhatsApp);
      - hay borrador LI → WhatsApp frío sale (conversación LinkedIn).
    Si no: solo el canal del próximo toque ejecutable.
    """
    li_draft = (getattr(prospect, "linkedin_assisted_draft", None) or "").strip()
    wa_draft = (getattr(prospect, "whatsapp_assisted_draft", None) or "").strip()
    call_brief = (getattr(prospect, "call_assisted_brief", None) or "").strip()
    changed = False

    if _sequence_held_for_conversation(prospect):
        if wa_draft and li_draft:
            _clear_assisted_live_queue(prospect, "whatsapp")
            changed = True
        return changed

    nxt = next_executable_day(prospect, campaign)
    step = _playbook_step(nxt, campaign) if nxt is not None else None
    channel = str(getattr(step, "channel", None) or "").strip().lower() if step else ""

    if channel == "whatsapp":
        if li_draft:
            _clear_assisted_live_queue(prospect, "linkedin")
            changed = True
        if call_brief:
            _clear_assisted_live_queue(prospect, "call")
            changed = True
    elif channel == "linkedin":
        if wa_draft:
            _clear_assisted_live_queue(prospect, "whatsapp")
            changed = True
        if call_brief:
            _clear_assisted_live_queue(prospect, "call")
            changed = True
    elif channel == "call":
        if li_draft:
            _clear_assisted_live_queue(prospect, "linkedin")
            changed = True
        if wa_draft:
            _clear_assisted_live_queue(prospect, "whatsapp")
            changed = True
    else:
        # Email u otro: las colas asistidas LI/WA/Call no deben mostrar cards.
        if li_draft:
            _clear_assisted_live_queue(prospect, "linkedin")
            changed = True
        if wa_draft:
            _clear_assisted_live_queue(prospect, "whatsapp")
            changed = True
        if call_brief:
            _clear_assisted_live_queue(prospect, "call")
            changed = True
    return changed


def expire_unsent_assisted_touches_for_calendar(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign | None,
    now: datetime | None = None,
) -> list[int]:
    """
    Omite LI/WA no enviados cuando llega la hora exacta del siguiente toque
    (+3 días desde inicio de secuencia en el 1er tramo; +3 desde generated_at
    del anterior en el resto). El toque anterior SALE de bandeja y el siguiente
    puede generarse. No avanza si hay conversación (paused / reuniones).
    """
    now = now or _now()
    started = prospect.sequence_started_at
    if started is None:
        return []
    if _sequence_held_for_conversation(prospect):
        return []

    planned = list(_planned_days(prospect, campaign))
    log = _touch_log(prospect)
    omitted: list[int] = []

    def _later_assisted_live(day: int) -> bool:
        for d in planned:
            if d <= day:
                continue
            entry = log.get(str(d), {})
            if not isinstance(entry, dict):
                continue
            if entry.get("status") != TOUCH_GENERADO:
                continue
            step = _playbook_step(d, campaign)
            ch = str(
                entry.get("channel")
                or (getattr(step, "channel", None) if step else "")
                or ""
            ).strip().lower()
            if ch in ("linkedin", "whatsapp", "call"):
                return True
        return False

    def _should_leave_unsent(day: int) -> bool:
        following = next((d for d in planned if d > day), None)
        if following is not None:
            return is_assisted_sequence_touch_due(
                prospect, following, campaign=campaign, now=now
            )
        # Último toque asistido: 3 días desde que se generó.
        entry = log.get(str(day), {})
        if not isinstance(entry, dict):
            return False
        step = _playbook_step(day, campaign)
        ch = str(
            entry.get("channel")
            or (getattr(step, "channel", None) if step else "")
            or ""
        ).strip().lower()
        age = _assisted_queue_started_at(prospect, entry, ch)
        if age is None:
            return False
        from app.services.linkedin_sequence_policy import ASSISTED_QUEUE_TTL_DAYS

        return (now - age) >= timedelta(days=ASSISTED_QUEUE_TTL_DAYS)

    for day in planned:
        entry = log.get(str(day), {})
        if not isinstance(entry, dict):
            continue
        step = _playbook_step(day, campaign)
        channel = str(
            entry.get("channel")
            or (getattr(step, "channel", None) if step else "")
            or ""
        ).strip().lower()
        status = entry.get("status")

        if status == TOUCH_OMITIDO:
            if channel in ("linkedin", "whatsapp", "call") and not _assisted_touch_was_sent(
                prospect, channel, entry
            ):
                live = _assisted_queue_is_live(prospect, channel)
                if not live:
                    continue
                # Ya hay un toque asistido posterior en cola → este no vuelve a bandeja.
                if _later_assisted_live(day) or _should_leave_unsent(day):
                    _clear_assisted_live_queue(prospect, channel)
                    omitted.append(day)
                else:
                    body = (
                        (entry.get("message_body") or entry.get("body") or "").strip()
                        or (
                            (getattr(prospect, "linkedin_assisted_draft", None) or "").strip()
                            if channel == "linkedin"
                            else (
                                (getattr(prospect, "whatsapp_assisted_draft", None) or "").strip()
                                if channel == "whatsapp"
                                else (getattr(prospect, "call_assisted_brief", None) or "").strip()
                            )
                        )
                    )
                    _set_touch_entry(
                        prospect,
                        day,
                        status=TOUCH_GENERADO,
                        channel=channel,
                        generated_at=entry.get("generated_at") or now.isoformat(),
                        message_body=body or None,
                        body=body or None,
                        skip_reason=None,
                        skipped_at=None,
                        error=None,
                    )
                    omitted.append(day)
                    log = _touch_log(prospect)
            continue
        if status != TOUCH_GENERADO:
            continue
        if channel not in ("linkedin", "whatsapp", "call"):
            continue
        if _assisted_touch_was_sent(prospect, channel, entry):
            continue

        if not _should_leave_unsent(day):
            if _assisted_queue_started_at(prospect, entry, channel) is None:
                _set_touch_entry(prospect, day, generated_at=now.isoformat(), channel=channel)
                log = _touch_log(prospect)
            continue

        _auto_omit_sequence_touch(
            db,
            prospect=prospect,
            day=day,
            reason="asistido_sin_envio_3d",
        )
        _clear_assisted_live_queue(prospect, channel)
        other = "whatsapp" if channel == "linkedin" else "linkedin"
        _clear_assisted_live_queue(prospect, other)
        omitted.append(day)
        log = _touch_log(prospect)
        ensure_single_assisted_live_queue(prospect, campaign)
    return omitted


def advance_auto_skipped_linkedin_touches(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign | None,
    now: datetime | None = None,
) -> list[int]:
    """Omite toques LinkedIn vencidos cuando la conexión falló."""
    from app.services.linkedin_sequence_policy import (
        refresh_linkedin_sequence_state,
        should_auto_omit_linkedin_touch,
    )

    now = now or _now()
    refresh_linkedin_sequence_state(prospect, now=now)
    omitted: list[int] = []
    for _ in range(len(PLAYBOOK_DAYS)):
        nxt = next_executable_day(prospect, campaign)
        if nxt is None:
            break
        if not should_auto_omit_linkedin_touch(prospect, campaign, nxt, now=now):
            break
        _auto_omit_sequence_touch(
            db,
            prospect=prospect,
            day=nxt,
            reason="linkedin_sin_conexion",
        )
        omitted.append(nxt)
    omitted.extend(
        expire_unsent_assisted_touches_for_calendar(
            db, prospect=prospect, campaign=campaign, now=now
        )
    )
    return omitted


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
        .options(selectinload(Campaign.product), selectinload(Campaign.company))
    ).first() or campaign

    if not prospect.campaign_id:
        prospect.campaign_id = campaign.id

    step = _playbook_step(affected_day)
    touch_channel = channel or (step.channel if step else "email")
    if touch_channel not in ("email", "linkedin", "whatsapp", "call"):
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
    from app.services.whatsapp_cloud_service import sanitize_stored_phone

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
    phone_val = sanitize_stored_phone(phone_val)
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
