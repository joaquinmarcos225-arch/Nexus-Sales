"""Puente inbound Meow → conversación Nexus (solo contactos autorizados)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.whatsapp_meow import WhatsappMeowMessage, WhatsappMeowSession
from app.schemas.whatsapp_meow import WhatsappMeowInboundEvent
from app.services.whatsapp_meow_authorization import authorize_meow_inbound_commercial
from app.services.whatsapp_inbound_sync import (
    _wa_dedup_id,
    process_whatsapp_inbound_for_prospect,
)

logger = logging.getLogger(__name__)


def _maybe_auto_reply_meow_inbound(
    db: Session,
    *,
    prospect,
    campaign,
    session_row: WhatsappMeowSession,
    inbound_plain: str,
    whatsapp_message_id: str | None,
) -> str | None:
    """Solo agenda la tarea. Brain + typing van en el worker (no en el webhook).

    Antes deliver() corrían OpenAI + sleep en el request Meow y congelaban uvicorn
    (campañas 504 / «Nexus no responde» / Reanudar tildado).
    """
    try:
        from app.services import inbound_auto_reply as iar
        from app.services.meeting_scheduling_extras import auto_reply_silenced
        from app.services.prospect_opt_out import is_suppressed
    except ImportError:
        return None

    if is_suppressed(prospect):
        return None
    if not iar.inbound_auto_reply_enabled():
        return None
    if auto_reply_silenced(prospect):
        return None
    # session_row se usa al ejecutar la task (worker relee Meow session).
    _ = session_row
    inbound_id = _wa_dedup_id(
        prospect_id=int(prospect.id),
        text=(inbound_plain or "").strip(),
        external_id=whatsapp_message_id,
    )
    try:
        task_id = iar.schedule_inbound_auto_send(
            db,
            campaign=campaign,
            prospect=prospect,
            inbound_gmail_message_id=inbound_id,
            channel="whatsapp",
            planned=None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("meow inbound schedule failed prospect_id=%s", prospect.id)
        return None
    return "scheduled" if task_id else "pending_or_dup"


def process_meow_inbound_after_store(
    db: Session,
    *,
    payload: WhatsappMeowInboundEvent,
    session_row: WhatsappMeowSession,
    meow_row: WhatsappMeowMessage,
) -> bool:
    """Registra en conversación/ secuencia si el contacto está autorizado. True si procesó."""
    auth = authorize_meow_inbound_commercial(
        db,
        session_row=session_row,
        from_peer=payload.from_,
        body=payload.body,
    )
    if not auth.allowed or auth.prospect is None or auth.campaign is None:
        logger.info(
            "meow inbound ignored reason=%s session=%s peer=%s meow_msg_id=%s",
            auth.reason,
            payload.session_id,
            payload.from_,
            payload.whatsapp_msg_id,
        )
        return False

    wa_id = f"meow:{payload.whatsapp_msg_id or meow_row.id}"
    from app.services.whatsapp_reply_compose import inbound_is_thin, merge_quoted_inbound

    raw_body = (payload.body or "").strip()
    quoted = (payload.quoted_body or "").strip() or None
    inbound_plain = merge_quoted_inbound(raw_body, quoted)
    if inbound_is_thin(inbound_plain):
        from app.services.whatsapp_inbound_auto_reply import (
            last_substantial_whatsapp_inbound_plain,
        )

        prev = last_substantial_whatsapp_inbound_plain(
            db, prospect_id=int(auth.prospect.id)
        )
        inbound_plain = merge_quoted_inbound(raw_body, quoted or prev or None)
    inserted = process_whatsapp_inbound_for_prospect(
        db,
        prospect=auth.prospect,
        campaign=auth.campaign,
        inbound_plain=inbound_plain,
        whatsapp_message_id=wa_id,
    )
    if inserted:
        logger.info(
            "meow inbound processed prospect_id=%s session=%s",
            auth.prospect.id,
            payload.session_id,
        )
    outcome = _maybe_auto_reply_meow_inbound(
        db,
        prospect=auth.prospect,
        campaign=auth.campaign,
        session_row=session_row,
        inbound_plain=inbound_plain,
        whatsapp_message_id=wa_id,
    )
    if outcome:
        logger.info(
            "meow inbound auto-reply prospect_id=%s outcome=%s inserted=%s "
            "hint=draft→cola WA asistida; sent→Meow; ver NEXUS_MEOW_INBOUND_AUTO_SEND",
            auth.prospect.id,
            outcome,
            inserted,
        )
    return bool(inserted or outcome)
