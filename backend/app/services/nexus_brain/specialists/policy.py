"""Especialista Policy: callar / pausar / no responder."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect


def run_policy(
    db: Session,
    *,
    campaign: Campaign,
    prospect: Prospect,
    inbound_text: str,
    channel: str = "email",
) -> dict[str, Any]:
    """
    Gate duro antes del orquestador / agentes.
    Respeta Iniciar secuencia, Pausar, solo hilos Nexus, post-reunión.
    Devuelve {ok: bool, skip_reason?, action?}.
    """
    from app.services.meeting_scheduling_extras import auto_reply_silenced

    # Sin «Iniciar secuencia» → cero agent (aunque haya inbound suelto).
    if getattr(prospect, "sequence_started_at", None) is None:
        return {
            "ok": False,
            "action": "skip",
            "skip_reason": "secuencia no iniciada — no trabajar esta cuenta",
            "silence_code": "sin_secuencia",
            "drop_focus": True,
        }

    silence = auto_reply_silenced(prospect)
    if silence:
        labels = {
            "ai_paused": "IA pausada para prospecto",
            # Botón Pausar / Handoff del SDR.
            "manual_pause": "secuencia pausada por el SDR — respetar freno",
            # Post-confirmación: sacar foco. No agent, research, booker ni draft.
            "meeting_booked": (
                "reunión ya agendada: sacar foco — no leer, no agent, no contestar"
            ),
            "meeting_done": "reunión cerrada: sacar foco — no contestar",
        }
        return {
            "ok": False,
            "action": "skip",
            "skip_reason": labels.get(silence, silence),
            "silence_code": silence,
            "drop_focus": silence
            in ("meeting_booked", "meeting_done", "manual_pause", "ai_paused"),
        }

    # Solo conversaciones Nexus: mail requiere outbound Nexus previo.
    ch = (channel or "email").strip().lower()
    if ch == "email":
        from app.services.gmail_inbound_sync import prospect_has_email_outbound

        if not prospect_has_email_outbound(db, int(prospect.id)):
            return {
                "ok": False,
                "action": "skip",
                "skip_reason": "sin hilo Nexus (no hubo outbound email nuestro)",
                "silence_code": "sin_conversacion_nexus",
                "drop_focus": True,
            }
    elif ch in ("whatsapp", "wa"):
        from sqlalchemy import func, select

        from app.models.outreach import OutreachMessage

        wa_out = db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.prospect_id == int(prospect.id),
                OutreachMessage.direction == "outbound",
                OutreachMessage.channel == "whatsapp",
            )
        )
        if int(wa_out or 0) == 0:
            return {
                "ok": False,
                "action": "skip",
                "skip_reason": "sin hilo Nexus WhatsApp (no hubo outbound nuestro)",
                "silence_code": "sin_conversacion_nexus",
                "drop_focus": True,
            }

    body = (inbound_text or "").strip()
    if not body:
        return {"ok": False, "action": "skip", "skip_reason": "inbound vacío"}

    from app.services.conversation_intelligence import looks_like_calendar_system_email

    if looks_like_calendar_system_email(body):
        return {
            "ok": False,
            "action": "skip_autoresponder",
            "skip_reason": "invitación / update de Google Calendar (no es el prospecto)",
        }

    # Autoresponder / OOO: el orquestador de agenda ya lo detecta; acá solo hint.
    low = body.lower()
    ooo_markers = (
        "out of office",
        "fuera de la oficina",
        "estaré ausente",
        "estare ausente",
        "automatic reply",
        "respuesta automática",
        "respuesta automatica",
    )
    if any(m in low for m in ooo_markers) and len(body) < 800:
        return {
            "ok": False,
            "action": "skip_autoresponder",
            "skip_reason": "respuesta automática / fuera de oficina",
        }

    return {
        "ok": True,
        "action": None,
        "skip_reason": None,
        "channel": channel,
        "campaign_id": int(campaign.id),
        "prospect_id": int(prospect.id),
    }
