"""Envío WhatsApp real para toques de secuencia (cualquier día con canal WhatsApp)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.prospect import Prospect
from app.models.user import User
from app.services import followup_engine, pipeline_sync
from app.services import outreach_metrics as om
from app.services.outreach_simulation import make_message
from app.services.whatsapp_cloud_service import (
    normalize_whatsapp_digits,
    send_sequence_whatsapp_message,
)

logger = logging.getLogger(__name__)


def friendly_whatsapp_api_error(raw: str) -> str:
    """Traduce errores comunes de Meta Graph a mensajes accionables en español."""
    text = (raw or "").strip()
    lower = text.lower()
    if "131030" in text or "not in allowed list" in lower:
        return (
            "Meta rechazó el envío (#131030): tu celular no está en la lista de permitidos de la app "
            "(modo desarrollo). En developers.facebook.com → tu app → WhatsApp → API Setup → "
            "«Enviar y recibir mensajes» → Manage phone number list, agregá y verificá con el código: "
            "+5491128942875 o +54111528942875 (mismo número, formatos distintos). "
            "Completá la verificación por WhatsApp antes de reintentar."
        )
    if "190" in text and (
        "oauth" in lower or "expired" in lower or "session has expired" in lower
    ):
        return (
            "Token de WhatsApp vencido o inválido. Actualizá WHATSAPP_ACCESS_TOKEN en "
            "backend/.env y verificá en Configuración → WhatsApp."
        )
    if "131047" in text or "re-engagement" in lower or "24 hour" in lower:
        return (
            "Meta no permite texto libre: el contacto no escribió en las últimas 24 h. "
            "Desde tu celular enviá cualquier mensaje al número comercial de prueba "
            "(+1 555-665-4023) y reintentá Ejecutar toque dentro de las 24 h."
        )
    if "131026" in text or "undeliverable" in lower:
        return "Meta no pudo entregar al número (inválido, sin WhatsApp o bloqueado)."
    if "100" in text and "invalid parameter" in lower:
        return "Parámetro inválido para Meta (revisá formato del teléfono del prospecto)."
    return text[:500] if text else "Error desconocido al enviar por WhatsApp."


def sequence_whatsapp_touch_uses_api(*, day: int, channel: str) -> bool:
    """True solo con Cloud API opt-in. Default del producto: cola WhatsApp Web asistida."""
    _ = day
    if channel != "whatsapp" or not om.is_real_mode():
        return False
    from app.services.whatsapp_cloud_service import is_whatsapp_cloud_api_enabled

    return is_whatsapp_cloud_api_enabled()


def deliver_sequence_whatsapp_touch(
    db: Session,
    *,
    user: User,
    campaign: Campaign,
    prospect: Prospect,
    day: int,
    body: str,
) -> dict[str, Any]:
    """Envía el mensaje del toque de secuencia por WhatsApp Business API."""
    if not sequence_whatsapp_touch_uses_api(day=day, channel="whatsapp"):
        raise HTTPException(
            status_code=400,
            detail="Este toque no está configurado para WhatsApp API real.",
        )

    seller_id = int(campaign.seller_id or 0)
    if seller_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="La campaña no tiene vendedor asignado.",
        )

    from app.core.permissions import is_company_admin

    actor_id = seller_id
    if int(user.id) != seller_id:
        if is_company_admin(user.role) and prospect.owner_user_id == user.id:
            actor_id = int(user.id)
        else:
            raise HTTPException(
                status_code=403,
                detail="Solo el vendedor asignado a la campaña puede enviar el WhatsApp de la secuencia.",
            )

    from app.services import daily_send_limits as dsl

    if not dsl.whatsapp_qualified(db, prospect):
        raise HTTPException(
            status_code=409,
            detail=(
                "WhatsApp calificado: el prospecto todavía no tuvo contacto por email o LinkedIn. "
                "Se pospone el toque de WhatsApp hasta que haya interacción previa."
            ),
        )
    if not dsl.can_send(db, actor_id, dsl.KIND_WHATSAPP):
        effective = dsl.whatsapp_effective_limit_today(db, actor_id)
        bonus = dsl.whatsapp_inbounds_today(db, actor_id)
        bonus_note = f" (+{bonus} por respuestas hoy)" if bonus else ""
        raise HTTPException(
            status_code=429,
            detail=(
                f"Límite diario de WhatsApp alcanzado ({effective}/día{bonus_note}). "
                "Se retoma mañana para no bloquear tu cuenta."
            ),
        )

    to_digits = normalize_whatsapp_digits(prospect.phone, prospect.whatsapp)
    if not to_digits:
        raise HTTPException(
            status_code=400,
            detail="El prospecto no tiene teléfono/WhatsApp válido.",
        )

    text = (body or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="Falta cuerpo para enviar por WhatsApp.")

    try:
        out = send_sequence_whatsapp_message(
            to_digits=to_digits,
            body=text,
            day=day,
            prospect_name=prospect.name,
            company_name=prospect.company_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=friendly_whatsapp_api_error(str(exc)),
        ) from exc

    wamid = (out.get("whatsapp_message_id") or "").strip() or None
    if not wamid and not out.get("dry_run"):
        raise HTTPException(
            status_code=503,
            detail=(
                "Meta aceptó la solicitud pero no devolvió ID de mensaje. "
                "Revisá que el número esté en la lista permitida y que hayas escrito "
                "al número comercial de prueba en las últimas 24 h."
            ),
        )

    hist_text = f"[WhatsApp · secuencia Día {day}]\n{text}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="user",
        message=hist_text,
        channel="whatsapp",
        direction="outbound",
        whatsapp_message_id=wamid,
    )
    db.add(msg)
    db.flush()

    followup_engine.record_ai_outbound(
        db,
        prospect,
        campaign_calendar_link=campaign.calendar_link or "",
        outbound_text=text,
    )
    if prospect.status in (
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.not_compatible.value,
    ):
        prospect.status = ProspectStatus.contacted.value
        pipeline_sync.sync_pipeline_from_status(prospect)
    if not (prospect.preferred_channel or "").strip():
        prospect.preferred_channel = "whatsapp"

    logger.info(
        "sequence_touch_whatsapp_sent prospect_id=%s day=%s whatsapp_message_id=%s",
        prospect.id,
        day,
        (wamid or "")[:24],
    )
    return {
        "message_id": msg.id,
        "whatsapp_message_id": wamid,
        "whatsapp_dry_run": bool(out.get("dry_run")),
    }
