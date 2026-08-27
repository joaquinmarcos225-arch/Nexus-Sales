"""Estado operativo visible de un prospecto en listas de campaña."""

from __future__ import annotations

from typing import Any

from app.models.prospect import Prospect


def compute_prospect_activity(prospect: Prospect) -> dict[str, Any]:
    """
    Qué está pasando ahora con este contacto (para UI al costado del nombre).

    Prioridad: enrich → pausa → LinkedIn conexión → LinkedIn mensaje → WhatsApp →
    secuencia / email → idle.
    """
    enrich = (getattr(prospect, "channel_enrich_status", None) or "").strip().lower()
    if enrich == "searching":
        msg = (getattr(prospect, "channel_enrich_message", None) or "").strip()
        return {
            "code": "enriching",
            "label": msg or "Buscando información de canales…",
            "deadline_at": getattr(prospect, "channel_enrich_deadline_at", None),
            "tone": "search",
        }

    if bool(getattr(prospect, "sequence_paused", False)):
        return {
            "code": "paused",
            "label": "Secuencia pausada",
            "deadline_at": None,
            "tone": "muted",
        }

    status = (getattr(prospect, "status", None) or "").strip().lower()
    if status in ("meeting_booked", "won"):
        return {
            "code": "meeting",
            "label": "Reunión agendada",
            "deadline_at": None,
            "tone": "ok",
        }
    if status == "not_interested":
        return {
            "code": "closed",
            "label": "No interesado",
            "deadline_at": None,
            "tone": "muted",
        }

    started = getattr(prospect, "sequence_started_at", None) is not None
    if not started:
        if enrich == "timed_out":
            return {
                "code": "enrich_timeout",
                "label": "Sin datos extra · listo para iniciar",
                "deadline_at": None,
                "tone": "muted",
            }
        return {
            "code": "idle",
            "label": "Guardado · esperando inicio",
            "deadline_at": None,
            "tone": "muted",
        }

    conn = (getattr(prospect, "linkedin_connection_status", None) or "none").strip().lower()
    if conn in ("invite_pending", "invite_sent", "checking", "check_queued", "check_failed"):
        if conn == "check_failed":
            label = "No se pudo verificar LinkedIn"
        elif conn in ("checking", "check_queued"):
            label = "Verificando si es contacto en LinkedIn"
        else:
            label = "Esperando conexión en LinkedIn"
        return {
            "code": "linkedin_connect",
            "label": label,
            "deadline_at": None,
            "tone": "wait",
        }

    li_assist = (getattr(prospect, "linkedin_assist_status", None) or "").strip().lower()
    li_sent = getattr(prospect, "linkedin_sdr_marked_sent_at", None) is not None
    if conn == "connected" and not li_sent and li_assist in (
        "suggested",
        "prepared",
        "opened",
        "queued",
    ):
        return {
            "code": "linkedin_message",
            "label": "Esperando envío de mensaje LinkedIn",
            "deadline_at": None,
            "tone": "wait",
        }

    wa_assist = (getattr(prospect, "whatsapp_assist_status", None) or "").strip().lower()
    if wa_assist in ("suggested", "prepared", "opened", "queued"):
        return {
            "code": "whatsapp_message",
            "label": "Esperando envío de WhatsApp",
            "deadline_at": None,
            "tone": "wait",
        }

    # Labels de secuencia si están
    current = (getattr(prospect, "sequence_current_label", None) or "").strip()
    next_lbl = (getattr(prospect, "next_touch_label", None) or "").strip()
    day_lbl = (getattr(prospect, "sequence_current_day_label", None) or "").strip()
    if (current and "gmail" in current.lower()) or (
        next_lbl and "email" in next_lbl.lower()
    ):
        return {
            "code": "email",
            "label": current or next_lbl or "Enviando / pendiente Gmail",
            "deadline_at": None,
            "tone": "active",
        }
    if current or next_lbl or day_lbl:
        return {
            "code": "sequence",
            "label": current or next_lbl or day_lbl,
            "deadline_at": None,
            "tone": "active",
        }

    group = (getattr(prospect, "sequence_group", None) or "").strip().lower()
    if group == "encajonado":
        return {
            "code": "boxed",
            "label": "Encajonado · esperando respuesta",
            "deadline_at": None,
            "tone": "wait",
        }
    if group == "postergado":
        return {
            "code": "deferred",
            "label": "Postergado",
            "deadline_at": None,
            "tone": "muted",
        }

    # Nunca devolver «Iniciando secuencia…».
    return {
        "code": "none",
        "label": "",
        "deadline_at": None,
        "tone": "muted",
    }
