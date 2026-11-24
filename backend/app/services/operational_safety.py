"""Frenos operativos: parada global, baja y envíos que no se pueden afirmar.

No desactiva features. Solo impide una acción real si el estado dice que no debe salir.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect


def _flag_is_on(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return False


def confirmed_gmail_id(value: object) -> str | None:
    """Solo un ID real de Gmail cuenta como confirmación. MagicMock no pasa."""
    if not isinstance(value, str):
        return None
    gid = value.strip()
    return gid or None


def company_has_global_stop(db: Session | None, company_id: int | None) -> bool:
    if db is None or not company_id:
        return False
    company = db.get(Company, int(company_id))
    if not company:
        return False
    return _flag_is_on(getattr(company, "global_automation_stop", False))


def assert_live_email_send_allowed(db: Session, *, company_id: int | None) -> None:
    """Parada de emergencia: no mandar Gmail automático."""
    if company_has_global_stop(db, company_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Parada de emergencia activa: no se envían emails automáticos. "
                "Reanudalá en Operaciones si corresponde."
            ),
        )


def assert_prospect_may_receive_outreach(prospect: Prospect) -> None:
    """Pidió la baja: no toques de secuencia (aunque alguien despausara a mano)."""
    from app.services.prospect_opt_out import is_suppressed

    if is_suppressed(prospect):
        raise HTTPException(
            status_code=403,
            detail="Este contacto pidió la baja. No se envían más mensajes.",
        )


def sequence_gmail_sent_prefix(day: int) -> str:
    return f"[Gmail enviado · secuencia Día {int(day)}]"


def find_confirmed_sequence_gmail_message(
    db: Session,
    *,
    prospect_id: int,
    campaign_id: int,
    day: int,
) -> OutreachMessage | None:
    """Si este toque ya tiene un mail con ID de Gmail, no volver a mandar."""
    prefix = sequence_gmail_sent_prefix(day)
    try:
        row = db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.prospect_id == int(prospect_id),
                OutreachMessage.campaign_id == int(campaign_id),
                OutreachMessage.direction == "outbound",
                OutreachMessage.channel == "email",
                OutreachMessage.gmail_message_id.isnot(None),
                OutreachMessage.message.startswith(prefix),
            )
            .order_by(OutreachMessage.id.desc())
        ).first()
    except Exception:  # noqa: BLE001 — tests con MagicMock u ORM incompleto
        return None
    if row is None or not confirmed_gmail_id(getattr(row, "gmail_message_id", None)):
        return None
    return row


def prospect_has_confirmed_gmail_outbound(db: Session, prospect_id: int) -> bool:
    n = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "email",
            OutreachMessage.gmail_message_id.isnot(None),
            OutreachMessage.gmail_message_id != "",
        )
    )
    return int(n or 0) > 0


def prospect_has_whatsapp_sequence_outbound(db: Session, prospect_id: int) -> bool:
    """WhatsApp confirmado en dispositivo/API — no solo historial Nexus sin envío real."""
    from app.models.prospect import Prospect
    from app.models.whatsapp_dispatch import WhatsAppDispatch
    from app.models.whatsapp_meow import WhatsappMeowMessage
    from app.services.whatsapp_assisted_service import prospect_whatsapp_digits
    from app.services.whatsapp_meow_service import _normalize_meow_peer

    prospect = db.get(Prospect, int(prospect_id))
    if prospect is None:
        return False

    pid = int(prospect_id)
    dispatched = db.scalar(
        select(func.count(WhatsAppDispatch.id)).where(
            WhatsAppDispatch.prospect_id == pid,
            WhatsAppDispatch.submit_status.in_(("submitted", "delivered", "read", "sent")),
        )
    )
    if int(dispatched or 0) > 0:
        return True

    with_wamid = db.scalar(
        select(func.count(OutreachMessage.id)).where(
            OutreachMessage.prospect_id == pid,
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.whatsapp_message_id.isnot(None),
            OutreachMessage.whatsapp_message_id != "",
        )
    )
    if int(with_wamid or 0) > 0:
        return True

    digits = prospect_whatsapp_digits(prospect)
    if digits:
        meow_rows = db.scalars(
            select(WhatsappMeowMessage).where(
                WhatsappMeowMessage.direction == "out",
                WhatsappMeowMessage.status == "sent",
            )
        ).all()
        tail = digits[-10:] if len(digits) >= 10 else digits
        for row in meow_rows:
            peer = _normalize_meow_peer(row.peer or "")
            if peer == digits or (tail and peer.endswith(tail)) or (
                peer and digits.endswith(peer[-10:])
            ):
                return True

    raw_log = getattr(prospect, "sequence_touch_log", None)
    if raw_log:
        import json

        try:
            touch_log = json.loads(raw_log) if isinstance(raw_log, str) else raw_log
        except json.JSONDecodeError:
            touch_log = {}
        for entry in (touch_log or {}).values():
            if entry.get("whatsapp_assisted_sent") or entry.get("sdr_marked_sent"):
                return True
    return False


def try_claim_sequence_start(db: Session, prospect: Prospect) -> bool:
    """Una sola sesión gana el arranque. Evita doble crédito / doble día 1."""
    if getattr(prospect, "id", None) is None:
        return False
    now = datetime.now(UTC)
    res = db.execute(
        update(Prospect)
        .where(
            Prospect.id == int(prospect.id),
            Prospect.sequence_started_at.is_(None),
        )
        .values(sequence_started_at=now)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    db.expire(prospect, ["sequence_started_at"])
    db.refresh(prospect)
    return int(getattr(res, "rowcount", 0) or 0) == 1


def maybe_unpause_campaign_for_kickoff(db: Session, campaign, company_id: int) -> None:
    """Kickoff no puede apagar una parada de emergencia."""
    if company_has_global_stop(db, company_id):
        return
    campaign.automation_paused = False


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def auto_send_env_enabled() -> bool:
    return _truthy_env("NEXUS_AUTO_SEND_ENABLED")


def automation_scheduler_env_enabled() -> bool:
    return _truthy_env("NEXUS_AUTOMATION_SCHEDULER")


def gmail_automation_env_enabled() -> bool:
    return _truthy_env("ENABLE_GMAIL_AUTOMATION")


def live_gmail_operations_allowed() -> bool:
    """Gmail outbound/inbound automático en background: solo con NEXUS_REAL_MODE=1."""
    from app.services import outreach_metrics as om

    return om.is_real_mode()


def auto_send_operations_allowed() -> bool:
    """Envío Gmail automático (no borrador): REAL_MODE + NEXUS_AUTO_SEND_ENABLED."""
    return live_gmail_operations_allowed() and auto_send_env_enabled()


def operational_env_issues() -> list[dict[str, Any]]:
    """Combinaciones peligrosas o contradictorias (auditoría / health)."""
    from app.services import outreach_metrics as om

    issues: list[dict[str, Any]] = []
    real = om.is_real_mode()
    auto = auto_send_env_enabled()
    sched = automation_scheduler_env_enabled()
    gmail = gmail_automation_env_enabled()

    if auto and not real:
        issues.append(
            {
                "level": "error",
                "id": "auto_send_without_real_mode",
                "message": (
                    "NEXUS_AUTO_SEND_ENABLED=1 sin NEXUS_REAL_MODE=1: "
                    "no se enviará automáticamente (protección activa)."
                ),
            }
        )
    if gmail and sched and not real:
        issues.append(
            {
                "level": "warning",
                "id": "gmail_scheduler_without_real_mode",
                "message": (
                    "ENABLE_GMAIL_AUTOMATION=1 y scheduler activos sin REAL_MODE: "
                    "ticks Gmail en background quedan deshabilitados."
                ),
            }
        )
    if real and auto and not sched:
        issues.append(
            {
                "level": "warning",
                "id": "real_auto_send_without_scheduler",
                "message": (
                    "REAL_MODE y auto-send activos pero NEXUS_AUTOMATION_SCHEDULER≠1: "
                    "no hay ticks de automatización en background."
                ),
            }
        )
    return issues
