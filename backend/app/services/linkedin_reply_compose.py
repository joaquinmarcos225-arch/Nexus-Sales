"""Borradores de réplica LinkedIn tras inbound — personalizados y breves (como toques de secuencia)."""

from __future__ import annotations

import logging
import re
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.multichannel_sequence import (
    _campaign_payload,
    _product_payload,
    _prospect_payload,
)
from app.services.sdr_outreach_compose import (
    generate_linkedin_inbound_reply_for_prospect,
    prior_touches_from_history,
)
from app.services.outbound_text_normalize import (
    apply_opening_greeting_policy,
    conversation_allows_opening_greeting,
)

logger = logging.getLogger(__name__)

_TEST_COMPANY_RE = re.compile(
    r"prueba|test[-_\s]|demo[-_\s]|fake|mock|sample|example",
    re.I,
)

_LINKEDIN_REPLY_MAX_WORDS = 48

_TEST_INBOUND_RE = re.compile(
    r"\btest\s+verify\b|\[test[^\]]*\]|verify-\d+",
    re.I,
)
_ISO_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
)
_GREETING_PREFIX_RE = re.compile(
    r"^(?:hola|buen[oa]s?\s+(?:d[ií]as|tardes|noches))(?:\s+[\wáéíóúñ]+)?[,.!]?\s*",
    re.I,
)
_ROLE_FIRST_NAMES = frozenset({"sdr", "seller", "vendedor", "vendedora", "demo", "test"})

# Pitch de producto no pedido (típico de cold open / réplica mal educada).
_UNSOLICITED_PITCH_RE = re.compile(
    r"(?:"
    r"reduce\s+hasta|\d+\s*%\s*del\s+trabajo|trabajo\s+manual\s+en\s+prospec"
    r"|automatiza(?:r|ción|mos)?\s+prospec|nuestra\s+plataforma\s+(?:reduce|automatiza|une)"
    r"|te\s+cuento\s+que\s+nuestra|propuesta\s+de\s+valor|leaving\s+que\s+el\s+sdr"
    r"|beneficio\s+concreto|ahorr(?:o|ás|ar)\s+(?:hasta\s+)?\d+"
    r")",
    re.I,
)

_PRODUCT_ASK_HINTS = (
    "qué hace",
    "que hace",
    "cómo funciona",
    "como funciona",
    "cómo es",
    "como es",
    "precio",
    "cuánto",
    "cuanto",
    "costo",
    "tarifa",
    "diferencia",
    "diferenci",
    "vs ",
    " versus",
    "compar",
    "integrac",
    "qué es",
    "que es",
    "para qué sirve",
    "para que sirve",
    "producto",
    "plataforma",
    "herramienta",
    "más sobre",
    "mas sobre",
    "contame qué",
    "cuéntame qué",
    "cuentame que",
    "explica",
)


def sanitize_company_display(raw: str | None) -> str | None:
    """Oculta nombres de empresa de prueba en mensajes al prospecto."""
    name = (raw or "").strip()
    if not name or _TEST_COMPANY_RE.search(name):
        return None
    return name


def _prospect_first_name(prospect: Prospect) -> str:
    raw = getattr(prospect, "name", None)
    name = raw if isinstance(raw, str) else ""
    parts = name.strip().split()
    return parts[0] if parts else "Hola"


def _seller_first_name(campaign: Campaign, db: Session | None = None) -> str:
    sender = (getattr(campaign, "sender_name", None) or "").strip()
    if sender:
        return str(sender).split()[0]

    seller = getattr(campaign, "seller", None)
    if seller is None and db is not None and campaign.seller_id:
        from app.models.user import User

        seller = db.get(User, int(campaign.seller_id))

    if seller is not None:
        first = (getattr(seller, "first_name", None) or "").strip()
        last = (getattr(seller, "last_name", None) or "").strip()
        full = (getattr(seller, "name", None) or "").strip()
        if first and first.lower() not in _ROLE_FIRST_NAMES:
            return first
        if last and last.lower() not in _ROLE_FIRST_NAMES:
            return last
        if full:
            parts = [p for p in full.split() if p.lower() not in _ROLE_FIRST_NAMES]
            if parts:
                return parts[0]
            return full.split()[0]

    return ""


def _strip_channel_prefix(text: str) -> str:
    body = (text or "").strip()
    for prefix in ("[LinkedIn · respuesta real]", "[Gmail · respuesta real]"):
        if body.startswith(prefix):
            body = body.split("\n", 1)[-1].strip()
            if body.startswith("Asunto:"):
                parts = body.split("\n\n", 1)
                body = parts[1].strip() if len(parts) > 1 else body
    return body


def _trim_words(text: str, max_words: int = _LINKEDIN_REPLY_MAX_WORDS) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    return " ".join(words[:max_words]).rstrip(".,;:") + "…"


def _ensure_seller_sign(draft: str, campaign: Campaign, db: Session | None = None) -> str:
    text = (draft or "").strip()
    seller = _seller_first_name(campaign, db=db)
    if not text or not seller:
        return text
    if seller.lower() in text.lower():
        return text
    return f"{text.rstrip()}\n\nSaludos,\n{seller}"


def messages_to_conversation_history(
    messages: Sequence[OutreachMessage],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for m in messages:
        text = _strip_channel_prefix(m.message or "")
        if not text:
            continue
        if m.direction == "inbound":
            rows.append({"role": "prospect", "content": text[:800]})
        elif m.sender_type in ("user", "ai"):
            rows.append({"role": "sdr", "content": text[:800]})
    return rows


def _normalize_inbound_for_reply(inbound_text: str) -> str:
    """Limpia ruido de test/timestamps/saludos para no citarlos en la réplica."""
    text = _strip_channel_prefix(inbound_text).strip()
    text = _TEST_INBOUND_RE.sub("", text)
    text = _ISO_TS_RE.sub("", text)
    text = _GREETING_PREFIX_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text).strip(" .,;:-")


def _inbound_reply_angle(inbound_text: str) -> str:
    """
    Ángulo humano para personalizar sin repetir el mensaje del prospecto entre comillas.
    """
    from app.services.openai_service import inbound_text_needs_substantive_answer

    clean = _normalize_inbound_for_reply(inbound_text)
    low = clean.lower()

    if not clean:
        return "interest"

    if any(h in low for h in ("agenda", "agendame", "agendá", "agendar", "llamada", "reunión", "reunion", "demo", "hablar")):
        return "scheduling"

    if any(h in low for h in ("diferencia", "diferenci", "vs ", " versus", "compar")):
        return "differentiation"

    if any(
        h in low
        for h in (
            "qué hace",
            "que hace",
            "cómo funciona",
            "como funciona",
            "qué es",
            "que es",
            "para qué sirve",
            "para que sirve",
        )
    ):
        return "how_it_works"

    if inbound_text_needs_substantive_answer(clean) and _inbound_asks_about_product(clean):
        return "substantive"

    if any(h in low for h in ("me interesa", "interesad", "quiero saber", "dale", "ok", "genial", "bueno")):
        return "interest"

    return "interest"


def _inbound_asks_about_product(inbound_text: str) -> bool:
    """True solo si el prospecto pide explicación del producto (no solo interés)."""
    low = _normalize_inbound_for_reply(inbound_text).lower()
    if not low:
        return False
    if any(h in low for h in ("precio", "cuánto", "cuanto", "costo", "tarifa", "plan")):
        return True
    return any(h in low for h in _PRODUCT_ASK_HINTS)


def _looks_like_unsolicited_product_pitch(draft: str) -> bool:
    return bool(_UNSOLICITED_PITCH_RE.search((draft or "").strip()))


def _product_short_value(campaign: Campaign) -> str:
    vp = (_product_payload(campaign).get("value_proposition") or "").strip()
    if not vp:
        return "automatiza prospección outbound multicanal con menos trabajo manual"
    sentence = vp.split(".")[0].strip()
    low = sentence.lower()
    if "automatiz" in low and len(sentence) > 72:
        return "automatiza prospección outbound multicanal con menos trabajo manual"
    if len(sentence) > 72:
        return sentence[:69].rstrip(" ,;:-") + "…"
    return sentence


def linkedin_inbound_offline_draft(
    prospect: Prospect,
    campaign: Campaign,
    *,
    inbound_text: str,
    db: Session | None = None,
    allow_opening_greeting: bool = True,
) -> str:
    """
    Último recurso sin OpenAI: réplica corta, natural, sin citar el inbound literal.
    Pitch de producto solo si el prospecto preguntó por el producto.
    """
    first = _prospect_first_name(prospect)
    product = (_product_payload(campaign).get("name") or "").strip() or "nuestra solución"
    seller = _seller_first_name(campaign, db=db)
    angle = _inbound_reply_angle(inbound_text)
    value = _product_short_value(campaign)
    greet = f"Hola {first}, " if allow_opening_greeting and first else (
        "Hola, " if allow_opening_greeting else ""
    )

    if angle == "how_it_works":
        body = (
            f"{greet}buena pregunta. {product} centraliza prospección, secuencias y follow-ups "
            f"en un solo flujo para que el equipo venda más sin perder tiempo operativo. "
            f"¿Te sirve una charla breve esta semana?"
        )
    elif angle == "differentiation":
        body = (
            f"{greet}la diferencia clave es que {product} une búsqueda ICP, multicanal y "
            f"respuestas inbound en un solo lugar, no solo un CRM o un secuenciador. "
            f"¿Coordinamos 15 min para mostrártelo?"
        )
    elif angle == "substantive" and _inbound_asks_about_product(inbound_text):
        body = (
            f"{greet}en corto: {product} {value.rstrip('.')}. "
            f"¿Te parece si lo vemos en una llamada corta?"
        )
    elif angle == "scheduling":
        body = (
            f"{greet}con gusto. ¿Qué día de esta semana te queda bien para una llamada de 15 min?"
            if greet
            else "Con gusto. ¿Qué día de esta semana te queda bien para una llamada de 15 min?"
        )
    else:
        body = (
            f"Genial {first}! ¿Te parece agendar una reunión breve de 15 min esta semana?"
            if first
            else "Genial! ¿Te parece agendar una reunión breve de 15 min esta semana?"
        )

    body = apply_opening_greeting_policy(body, allow_greeting=allow_opening_greeting)
    # Capitalizar si quedó "buena pregunta..." sin saludo
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    signed = f"{body}\n\nSaludos,\n{seller}" if seller else body
    return _trim_words(signed)


def linkedin_reply_fallback_draft(
    prospect: Prospect,
    campaign: Campaign,
    *,
    inbound_text: str,
) -> str:
    """Compat: delegado al borrador offline inbound-aware."""
    return linkedin_inbound_offline_draft(prospect, campaign, inbound_text=inbound_text, db=None)


def compose_linkedin_inbound_reply(
    db: Session,
    *,
    prospect: Prospect,
    campaign: Campaign,
    inbound_text: str,
    history: Sequence[OutreachMessage] | None = None,
) -> str:
    """
    Genera réplica LinkedIn personalizada al inbound (OpenAI → motor playbook → offline contextual).
    Siempre adaptada al mensaje del prospecto; máximo ~55 palabras en la réplica principal.
    """
    from app.services.ai_instruction_context import campaign_education_blob
    from app.services import openai_service

    inbound_text = _strip_channel_prefix(inbound_text)
    messages = list(history) if history is not None else []
    conv = messages_to_conversation_history(messages)
    education = campaign_education_blob(db, campaign)
    prior = prior_touches_from_history(messages)
    allow_greeting = conversation_allows_opening_greeting(messages)

    if openai_service.openai_configured():
        try:
            draft = openai_service.generate_linkedin_inbound_reply(
                prospect=_prospect_payload(prospect),
                inbound_message=inbound_text,
                conversation_history=conv,
                campaign=_campaign_payload(campaign),
                product=_product_payload(campaign),
                education=education,
                interest_level=getattr(prospect, "interest_level", None) or "medium",
                allow_soft_meeting_close=True,
                allow_opening_greeting=allow_greeting,
            )
            draft = _trim_words((draft or "").strip())
            draft = apply_opening_greeting_policy(draft, allow_greeting=allow_greeting)
            allow_pitch = _inbound_asks_about_product(inbound_text)
            if draft and (not allow_pitch) and _looks_like_unsolicited_product_pitch(draft):
                logger.warning(
                    "linkedin inbound openai pitched product without ask prospect_id=%s — fallback",
                    prospect.id,
                )
            elif draft:
                return _ensure_seller_sign(draft, campaign, db=db)
        except Exception:
            logger.exception(
                "openai linkedin inbound reply failed prospect_id=%s — playbook fallback",
                prospect.id,
            )

    try:
        draft = generate_linkedin_inbound_reply_for_prospect(
            db,
            campaign=campaign,
            prospect=prospect,
            education=education,
            inbound_text=inbound_text,
            prior_touches=prior,
        )
        draft = _trim_words(draft)
        draft = apply_opening_greeting_policy(draft, allow_greeting=allow_greeting)
        allow_pitch = _inbound_asks_about_product(inbound_text)
        if draft and (not allow_pitch) and _looks_like_unsolicited_product_pitch(draft):
            draft = ""
        if draft:
            return _ensure_seller_sign(draft, campaign, db=db)
    except Exception:
        logger.exception(
            "playbook linkedin inbound reply failed prospect_id=%s — offline fallback",
            prospect.id,
        )

    return linkedin_inbound_offline_draft(
        prospect,
        campaign,
        inbound_text=inbound_text,
        db=db,
        allow_opening_greeting=allow_greeting,
    )
