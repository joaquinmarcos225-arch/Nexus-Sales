"""Entrega Gmail real para toques de secuencia (Días de email del playbook)."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import OutreachEmailMode
from app.models.prospect import Prospect
from app.models.user import User
from app.services import outreach_metrics as om
from app.services.email_deliverability import deliverable_email_skip_reason, is_real_deliverable_email
from app.services.gmail_drafts import create_draft_for_user, get_valid_gmail_connection
from app.services.gmail_send import send_email_for_user
from app.services.outreach_simulation import make_message

logger = logging.getLogger(__name__)


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def sequence_email_touch_uses_gmail(*, day: int, channel: str) -> bool:
    """True si este toque debe ir por Gmail real (no simulación en BD).

    Cualquier día cuya canal efectiva sea email (plan custom o playbook default).
    """
    _ = day  # day queda por compatibilidad de firma / logs
    return om.is_real_mode() and str(channel or "").strip().lower() == "email"


def _campaign_wants_auto_send(campaign: Campaign) -> bool:
    mode = (getattr(campaign, "outreach_email_mode", None) or OutreachEmailMode.draft_only.value).strip()
    return mode == OutreachEmailMode.auto_send.value and _truthy_env("NEXUS_AUTO_SEND_ENABLED")


def deliver_sequence_email_touch_via_gmail(
    db: Session,
    *,
    user: User,
    campaign: Campaign,
    prospect: Prospect,
    day: int,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """
    Entrega el toque de email de secuencia por Gmail.
    - auto_send + NEXUS_AUTO_SEND_ENABLED=1 → envía
    - si no → crea borrador (legacy)
    """
    if not sequence_email_touch_uses_gmail(day=day, channel="email"):
        raise HTTPException(status_code=400, detail="Este toque no está configurado para Gmail real.")

    from app.core.permissions import is_company_admin

    seller_id = int(campaign.seller_id or 0)
    if seller_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="La campaña no tiene vendedor asignado. Asigná un SDR antes de enviar.",
        )

    actor_is_seller = int(user.id) == seller_id
    actor_is_owner_admin = (
        is_company_admin(user.role) and prospect.owner_user_id == user.id
    )
    if not actor_is_seller and not actor_is_owner_admin:
        raise HTTPException(
            status_code=403,
            detail="Solo el vendedor asignado a la campaña puede enviar el email de la secuencia.",
        )

    # Preferí Gmail del actor; si el director no tiene Gmail, usá la del seller de la campaña.
    gmail_candidates: list[int] = []
    for uid in (int(user.id), seller_id):
        if uid > 0 and uid not in gmail_candidates:
            gmail_candidates.append(uid)

    from app.services import daily_send_limits as dsl

    to_addr = (prospect.email or "").strip()
    if not to_addr or "@" not in to_addr:
        raise HTTPException(status_code=400, detail="El prospecto no tiene email válido.")
    skip_reason = deliverable_email_skip_reason(to_addr)
    if skip_reason:
        raise HTTPException(
            status_code=400,
            detail=f"No se puede enviar a este email: {skip_reason}",
        )
    if not is_real_deliverable_email(to_addr):
        raise HTTPException(
            status_code=400,
            detail="El email del prospecto no es entregable (dominio demo o interno bloqueado).",
        )

    subj = (subject or "").strip()
    text = (body or "").strip()
    if not subj or not text:
        raise HTTPException(status_code=502, detail="Falta asunto o cuerpo para el email.")

    cid = int(campaign.company_id)
    sender_id: int | None = None
    row = None
    last_exc: Exception | None = None
    for candidate in gmail_candidates:
        try:
            _, row = get_valid_gmail_connection(db, company_id=cid, user_id=candidate)
            sender_id = candidate
            break
        except Exception as exc:  # noqa: BLE001 — probamos el siguiente candidato
            last_exc = exc
            continue
    if sender_id is None or row is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Gmail no está conectado. Conectá Google en Integraciones "
                "(si Google dice Access blocked, agregá tu mail como Test user en Google Cloud)."
            ),
        ) from last_exc

    if not dsl.can_send(db, sender_id, dsl.KIND_EMAIL):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Límite diario de emails alcanzado ({dsl.limit_for(dsl.KIND_EMAIL)}/día por SDR). "
                "Se retoma mañana para cuidar la reputación de tu cuenta."
            ),
        )

    auto_send = _campaign_wants_auto_send(campaign)
    from_addr = (row.external_email or "").strip()
    draft_id = None
    gid = None
    tid = None
    web_link = None

    try:
        if auto_send:
            if not from_addr:
                raise HTTPException(status_code=400, detail="Gmail conectado sin email de remitente.")
            out = send_email_for_user(
                db,
                company_id=cid,
                user_id=sender_id,
                from_addr=from_addr,
                to_addr=to_addr,
                subject=subj,
                body=text,
                thread_id=(prospect.gmail_thread_id or None),
            )
            gid = (out.get("gmail_message_id") or out.get("message_id") or "").strip() or None
            tid = (out.get("thread_id") or "").strip() or None
            web_link = out.get("gmail_web_link")
            hist_prefix = f"[Gmail enviado · secuencia Día {day}]"
            log_event = "sequence_touch_gmail_sent"
        else:
            out = create_draft_for_user(
                db,
                company_id=cid,
                user_id=sender_id,
                to_addr=to_addr,
                subject=subj,
                body=text,
            )
            draft_id = (out.get("draft_id") or "").strip() or None
            gid = (out.get("message_id") or "").strip() or None
            tid = (out.get("thread_id") or "").strip() or None
            web_link = out.get("gmail_web_link")
            hist_prefix = f"[Borrador Gmail · secuencia Día {day}]"
            log_event = "sequence_touch_gmail_draft"
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if tid:
        prospect.gmail_thread_id = tid

    hist_text = f"{hist_prefix}\nAsunto: {subj}\n\n{text}"
    msg = make_message(
        prospect_id=prospect.id,
        campaign_id=campaign.id,
        sender_type="system",
        message=hist_text,
        channel="email",
        direction="outbound",
        gmail_message_id=gid,
    )
    db.add(msg)
    db.flush()

    logger.info(
        "%s prospect_id=%s day=%s draft_id=%s message_id=%s",
        log_event,
        prospect.id,
        day,
        (draft_id or "")[:20],
        (gid or "")[:20],
    )
    return {
        "message_id": msg.id,
        "gmail_draft_id": draft_id,
        "gmail_message_id": gid,
        "thread_id": tid,
        "gmail_web_link": web_link,
        "sent": auto_send,
    }
