"""Borradores de réplica WhatsApp tras inbound — contestan el texto del prospecto."""

from __future__ import annotations

import logging
import re
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.linkedin_reply_compose import (
    _inbound_asks_about_product,
    _inbound_reply_angle,
    _looks_like_unsolicited_product_pitch,
    _product_short_value,
    _prospect_first_name,
    _trim_words,
    messages_to_conversation_history,
)
from app.services.multichannel_sequence import (
    _campaign_payload,
    _product_payload,
    _prospect_payload,
)

logger = logging.getLogger(__name__)

_WHATSAPP_REPLY_MAX_WORDS = 45

_WA_PREFIX_RE = re.compile(
    r"^\[WhatsApp[^\]]*\]\s*",
    re.I,
)

_EMAIL_SIGN_RE = re.compile(
    r"(?:\n|\r\n){1,2}(?:saludos|atte\.?|atentamente|cordiales?\s+saludos)\s*[,.]?\s*(?:\n|\r\n)+\s*\S[\s\S]*$",
    re.I,
)

# El playbook / modelos a veces inventan un Day1 frío en vez de responder.
_COLD_OPEN_RE = re.compile(
    r"\bmi nombre es\b|\bme presento\b|\bte hablo desde\b|\bte contacto desde\b|"
    r"\bsoy\s+\w+\s+de\b|\ble escribo desde\b|\bte contacto porque ayudamos\b",
    re.I,
)

_TIME_HINT_RE = re.compile(
    r"\b(?:a\s+las?\s+)?(\d{1,2})(?::(\d{2}))?\s*(?:hs?|hrs?|horas?)?\b|"
    r"\b(\d{1,2})\s*(?:hs|hrs)\b",
    re.I,
)

_WA_REPLY_RULES = (
    "REGLAS WHATSAPP (réplica, NO primer contacto): "
    "respondé SOLO al último mensaje del prospecto. "
    "Prohibido presentarte de nuevo ('mi nombre es' / 'te hablo desde'). "
    "PROHIBIDO firmar como email (nada de 'Saludos,' ni nombre al final). "
    "NO expliques el producto ni beneficios/% salvo que el prospecto pregunte "
    "qué hace / cómo funciona / precio / diferencia. "
    "Si pide agenda con hora concreta (ej. 'agendame a las 15'), confirmá o "
    "proponé alternativa — NO vuelvas a preguntar si quiere agendar. "
    "Máximo 45 palabras, tono chat cercano y chill. "
    "Si son 2+ frases, separalas en micro-párrafos (línea en blanco)."
)


def _strip_whatsapp_prefix(text: str) -> str:
    body = (text or "").strip()
    for prefix in ("[WhatsApp · respuesta real]", "[WhatsApp · inbound]"):
        if body.startswith(prefix):
            body = body.split("\n", 1)[-1].strip()
    body = _WA_PREFIX_RE.sub("", body).strip()
    return body


def strip_whatsapp_email_signature(draft: str) -> str:
    """WhatsApp no usa firma tipo email."""
    text = (draft or "").strip()
    if not text:
        return text
    cleaned = _EMAIL_SIGN_RE.sub("", text).strip()
    # También líneas finales sueltas "Saludos," / "Saludos"
    cleaned = re.sub(r"(?:\n|\r\n)+(?:saludos|atte\.?)[,.]?\s*$", "", cleaned, flags=re.I)
    return cleaned.strip()


def _looks_like_cold_open(draft: str) -> bool:
    return bool(_COLD_OPEN_RE.search((draft or "").strip()))


def _extract_time_hint(inbound: str) -> str | None:
    """Devuelve hora legible si el prospecto mencionó una (ej. '15hs' → '15:00')."""
    m = _TIME_HINT_RE.search(inbound or "")
    if not m:
        return None
    hour = m.group(1) or m.group(3)
    minute = m.group(2) or "00"
    if not hour:
        return None
    h = int(hour)
    if h > 23:
        return None
    return f"{h}:{minute.zfill(2)}"


def whatsapp_inbound_offline_draft(
    prospect: Prospect,
    campaign: Campaign,
    *,
    inbound_text: str,
    db: Session | None = None,
) -> str:
    """
    Réplica corta anclada al inbound (sin OpenAI).
    Nunca usa plantilla de primer contacto ni firma email.
    Pitch de producto solo si el prospecto preguntó por el producto.
    """
    del db  # seller sign no aplica en WA
    first = _prospect_first_name(prospect)
    product = (_product_payload(campaign).get("name") or "").strip() or "nuestra solución"
    inbound = _strip_whatsapp_prefix(inbound_text)
    angle = _inbound_reply_angle(inbound)
    value = _product_short_value(campaign)
    low = inbound.lower()
    time_hint = _extract_time_hint(inbound)

    if any(k in low for k in ("no me interesa", "no gracias", "sacame", "baja", "stop", "dejar de")):
        body = (
            f"Hola {first}, entendido, gracias por avisar. "
            f"No te molesto más por este canal."
        )
    elif any(k in low for k in ("precio", "cuánto", "cuanto", "costo", "vale", "tarifa", "plan")):
        body = (
            f"Hola {first}, buen punto. El plan de {product} se ajusta al volumen; "
            f"te lo detallo en una call corta. ¿Preferís por acá o una videollamada?"
        )
    elif angle == "how_it_works":
        body = (
            f"Hola {first}, te resumo: {product} {value.rstrip('.')}. "
            f"¿Querés que te lo muestre en una demo corta?"
        )
    elif angle == "differentiation":
        body = (
            f"Hola {first}, la diferencia es que {product} une búsqueda ICP, multicanal "
            f"y respuestas inbound en un solo flujo. ¿Lo vemos en una call corta?"
        )
    elif angle == "substantive" and _inbound_asks_about_product(inbound):
        body = (
            f"Hola {first}, en corto: {product} {value.rstrip('.')}. "
            f"¿Te sirve una demo rápida?"
        )
    elif angle == "scheduling" and time_hint:
        # Contestar la hora pedida — no re-preguntar si quiere agendar.
        body = (
            f"Perfecto {first}, anoto las {time_hint}. "
            f"Te confirmo en un momento si ese horario está libre "
            f"o te paso la alternativa más cercana."
        )
    elif angle == "scheduling":
        body = (
            f"Genial {first}. ¿Qué día y horario de esta semana te queda bien "
            f"para una charla corta?"
        )
    else:
        # Interés / ok / genérico: sin pitch de producto.
        body = (
            f"Genial {first}! ¿Te parece si coordinamos un espacio esta semana "
            f"para verlo juntos?"
        )

    return _trim_words(strip_whatsapp_email_signature(body), max_words=_WHATSAPP_REPLY_MAX_WORDS)


def compose_whatsapp_inbound_reply(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound_text: str,
    history: Sequence[OutreachMessage] | None = None,
) -> str:
    """
    Réplica WhatsApp al inbound: OpenAI anclado al mensaje → offline contextual.
    Sin playbook de secuencia (inventaba Day1 frío). Sin firma email.
    """
    from app.services.ai_instruction_context import campaign_education_blob
    from app.services import openai_service

    inbound_text = _strip_whatsapp_prefix(inbound_text)
    if not inbound_text:
        first = _prospect_first_name(prospect)
        return (
            f"Hola {first}, gracias por escribir. "
            f"¿Te parece si coordinamos un espacio breve esta semana?"
        )

    messages = list(history) if history is not None else []
    conv = messages_to_conversation_history(messages)
    education = campaign_education_blob(db, campaign)
    allow_pitch = _inbound_asks_about_product(inbound_text)

    if openai_service.openai_configured():
        try:
            draft = openai_service.generate_linkedin_inbound_reply(
                prospect=_prospect_payload(prospect),
                inbound_message=inbound_text,
                conversation_history=conv,
                campaign={
                    **_campaign_payload(campaign),
                    "name": f"{campaign.name or ''} (WhatsApp réplica — NO primer contacto)",
                },
                product=_product_payload(campaign),
                education=f"{education}\n\n{_WA_REPLY_RULES}",
                interest_level=getattr(prospect, "interest_level", None) or "medium",
                allow_soft_meeting_close=True,
            )
            draft = _trim_words((draft or "").strip(), max_words=_WHATSAPP_REPLY_MAX_WORDS)
            draft = strip_whatsapp_email_signature(draft)
            if draft and _looks_like_cold_open(draft):
                logger.warning(
                    "whatsapp inbound openai returned cold-open prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft and (not allow_pitch) and _looks_like_unsolicited_product_pitch(draft):
                logger.warning(
                    "whatsapp inbound openai pitched product without ask prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft:
                return draft
        except Exception:
            logger.exception(
                "openai whatsapp inbound reply failed prospect_id=%s — offline",
                prospect.id,
            )

    return whatsapp_inbound_offline_draft(
        prospect, campaign, inbound_text=inbound_text, db=db
    )
