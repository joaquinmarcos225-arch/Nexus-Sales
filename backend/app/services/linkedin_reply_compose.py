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
    strip_email_sign_off,
)

logger = logging.getLogger(__name__)

_TEST_COMPANY_RE = re.compile(
    r"prueba|test[-_\s]|demo[-_\s]|fake|mock|sample|example",
    re.I,
)

_LINKEDIN_REPLY_MAX_WORDS = 70

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
    r"|automatiza(?:r|ción|mos)?\s+(?:entre\s+)?(?:\d+\s*%|prospec)|"
    r"\d+\s*%\s*y\s*\d+\s*%|"
    r"tareas\s+(?:manuales\s+)?de\s+prospec|"
    r"nuestra\s+plataforma\s+(?:reduce|automatiza|une)"
    r"|te\s+cuento\s+que\s+nuestra|propuesta\s+de\s+valor|leaving\s+que\s+el\s+sdr"
    r"|beneficio\s+concreto|ahorr(?:o|ás|ar)\s+(?:hasta\s+)?\d+"
    r"|unificando\s+email\s*,?\s*linkedin\s+y\s+whatsapp"
    r")",
    re.I,
)

PRICE_ASK_RE = re.compile(
    r"\b(precios?|cu[aá]nto\s+(sale|sal[ií]s|salen|cuesta|cuestan|cobran|cobr[aá]s|vale|valen|es|ser[ií]a)"
    r"|tarifas?|planes|pricing|price|"
    r"(?:el\s+)?n[uú]mero(?:s)?\s+(?:antes|cerrado|exacto|por\s+mail)|"
    r"(?:pasame|decime|mandame|necesito|quiero)\s+(?:el\s+)?(?:precio|n[uú]mero|costo)|"
    r"c[oó]mo\s+se\s+cobra|c[oó]mo\s+cobran)\b",
    re.I,
)
_COST_WORD_RE = re.compile(r"\bcostos?\b", re.I)


def price_asked(text: str | None) -> bool:
    t = text or ""
    if inbound_insists_price_before_meeting(t):
        return True
    return bool(PRICE_ASK_RE.search(t)) or ("?" in t and bool(_COST_WORD_RE.search(t)))


_PRICING_FACT_RE = re.compile(
    r"(?:"
    r"\$\s*\d|\busd\b|\burs\b|\beur\b|\bars\b|"
    r"\d[\d.,]*\s*(?:usd|u\$s|dólares|dolares|eur|€)|"
    r"\b(?:precio|pricing|tarifa|plan(?:es)?|licencia|seat|seats|usuario)\b.{0,40}\b\d|"
    r"\bcobr(?:o|amos|a)\b.{0,40}\b(?:por|mensual|anual|usuario|crédito|credito)"
    r")",
    re.I,
)

_PRICE_BEFORE_MEETING_RE = re.compile(
    r"(?:"
    # Requiere señal de precio/número (no basta "antes de agendar, ¿cómo funciona?").
    r"(?:precio|n[uú]mero|costo|tarifa|pricing).{0,50}"
    r"antes\s+de\s+(?:la\s+)?(?:reuni[oó]n|llamada|demo|call|agendar)|"
    r"antes\s+de\s+(?:la\s+)?(?:reuni[oó]n|llamada|demo|call|agendar).{0,50}"
    r"(?:precio|n[uú]mero|costo|tarifa|pricing)|"
    r"sin\s+(?:saber\s+)?(?:el\s+)?(?:precio|n[uú]mero|costo)|"
    r"(?:necesito|quiero|pasame|decime|mandame)\s+(?:el\s+)?(?:precio|n[uú]mero|costo)"
    r"(?:\s+\w+){0,6}\s+(?:primero|antes)|"
    r"no\s+agend\w*\s+sin\s+(?:precio|n[uú]mero)|"
    r"precio\s+cerrado\s+por\s+(?:mail|email|whatsapp)|"
    r"por\s+mail\s+(?:el\s+)?precio"
    r")",
    re.I,
)


def product_has_pricing_facts(*blobs: str | None) -> bool:
    """True si en ficha/educación hay números o modelo de cobro usable (no inventar)."""
    text = "\n".join((b or "").strip() for b in blobs if (b or "").strip())
    if not text:
        return False
    return bool(_PRICING_FACT_RE.search(text))


def inbound_insists_price_before_meeting(text: str | None) -> bool:
    """True si exige el número antes de agendar (igual: no inventar)."""
    return bool(_PRICE_BEFORE_MEETING_RE.search(text or ""))


def build_price_unknown_email_reply(
    *,
    insist: bool = False,
    team_hint: str | None = None,
) -> str:
    """
    Precio pedido sin dato confirmado en producto.
    Default: llevarlo a la call. Si insiste: honestidad + agenda, sin inventar cifra.
    """
    team = (team_hint or "").strip()
    team_bit = f" para un equipo como el tuyo ({team})" if team else ""
    if insist:
        return (
            "Entiendo que preferís ver números antes de agendar.\n\n"
            f"No te paso un precio cerrado por mail porque depende de seats, alcance y setup"
            f"{team_bit or ' de tu operación'}; una cifra inventada acá no te sirve.\n\n"
            "En 15 min te armo la propuesta con números reales para tu caso. "
            "¿Qué día y franja te quedan cómodos?"
        )
    return (
        "El pricing lo armamos según tamaño del equipo y alcance"
        f"{team_bit}; no hay un número único que valga para todos.\n\n"
        "¿Lo vemos en una call corta y te paso números concretos para tu caso?"
    )


def looks_like_invented_pricing(text: str | None) -> bool:
    """Heurística: el borrador afirma un modelo/cifra de cobro concreto."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(
        re.search(
            r"(?:"
            r"\$\s*\d|"
            r"\b(?:usd|u\$s|eur|€)\b\s*\d|\d[\d.,]*\s*(?:usd|u\$s|dólares|eur)|"
            r"\bcobr\w*\s+por\s+usuario|"
            r"\blicencia\w*\s+incluye|"
            r"\busuario\s+activo\s+mensual|"
            r"\bplan\s+(?:mensual|anual)\b.{0,30}\d|"
            r"\bpor\s+seat\b|\bpor\s+usuario\s+activo\b"
            r")",
            raw,
            re.I,
        )
    )


HOW_IT_WORKS_RE = re.compile(
    r"\bqu[eé]\s+(hace|hacen|hacés|haces|ofrecen|ofrec[eé]s)\b"
    r"|\bqu[eé]\s+es\s+(exactamente|eso|esto|el\s+producto|la\s+plataforma|la\s+herramienta)\b"
    r"|\bc[oó]mo\s+funciona\w*|\bpara\s+qu[eé]\s+sirve\b"
    r"|\bqu[eé]\s+es\b"
    r"|\bc[oó]mo\s+es\s+(el|la)\s+(producto|plataforma|herramienta|servicio)\b",
    re.I,
)
# Incluye typos de audio: differenzia, diferensia, etc.
DIFFERENTIATION_RE = re.compile(
    r"\b(difer+en[csz]i\w*|diferencian?|vs\.?|versus|compar\w*)\b",
    re.I,
)
SCHEDULING_RE = re.compile(
    r"\b(agend\w*|llamad\w*|reuni[oó]n\w*|demo|call|videollamada|meet|hablar|hablemos|charl\w*|"
    r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|"
    r"ma[nñ]ana|pasado\s+ma[nñ]ana|hoy|"
    r"a\s+esa\s+(?:hr|hora)|esa\s+(?:hr|hora)|"
    r"me\s+queda\s+bien|esta\s+bien|te\s+queda\s+bien)\b",
    re.I,
)
_PRODUCT_ASK_RE = re.compile(
    r"\b(integra\w*|m[aá]s\s+(info|informaci[oó]n|detalles)\s+(sobre|de)|saber\s+m[aá]s|m[aá]s\s+sobre"
    r"|quiero\s+saber\s+si|antes\s+quiero\s+saber|saber\s+si\s+(?:puedo|pueden|tienen|hacen)"
    r"|cont[aá]me\s+(qu[eé]|m[aá]s)"
    r"|cu[eé]ntame\s+(qu[eé]|m[aá]s)|explic\w*"
    r"|personaliz\w*|plantillas?|templates?|c[oó]mo\s+arma\w*|arma\s+mensaje\w*"
    r"|onboarding|implement\w*|setup|despliegue|capacitaci\w*|sla\b"
    r"|hacen\s+\w+|tienen\s+\w+|soportan\s+\w+|ofrecen\s+\w+"
    r"|puedo\s+(?:personaliz|configur|cambiar|editar|elegir)\w*)\b",
    re.I,
)
# Adopción / madurez del producto («¿ya lo usan?», «¿sigue siendo piloto?»).
_ADOPTION_ASK_RE = re.compile(
    r"\b("
    r"piloto|en\s+uso|en\s+producci[oó]n|ya\s+lo\s+usan|ya\s+est[aá]\s+en\s+uso|"
    r"tienen\s+clientes|cu[aá]ntos\s+clientes|casos?\s+de\s+[eé]xito|"
    r"referencias?\b|est[aá]\s+en\s+el\s+mercado|live\b|production\b|"
    r"equipos?\s+de\s+outbound"
    r")\b",
    re.I,
)
_PRODUCT_NOUN_RE = re.compile(
    r"\b(producto|plataforma|herramienta|servicio|soluci[oó]n|nexus(?:\s+sales)?)\b",
    re.I,
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


_DANGLING_WORD_TAIL_RE = re.compile(
    r"(?:\s+(?:con|de|del|la|el|los|las|y|o|u|por|para|en|a|al|que|un|una|sin|"
    r"sobre|entre|tu|su|cuando|como|cual|si|porque|aunque|mientras|donde|quien|"
    r"permite\w*|permitiendo|integrando|incluyendo|ayudando|usando|mediante|"
    r"haciendo|dejando|mostrando|logrando|evitando|"
    r"exclusivamente|unicamente|únicamente|solamente|respectivamente|"
    r"frente|mediante|respecto|acerca|dentro|fuera)\s*)$",
    re.I,
)

# Cola gerundio truncada (poca sustancia tras el gerundio).
# No marcar participios completos tipo «…, usando la red bajo demanda».
_INCOMPLETE_GERUND_CLAUSE_RE = re.compile(
    r"(?:,?\s+(?:permitiendo|integrando|incluyendo|haciendo|dejando|mostrando|"
    r"ayudando|usando|logrando|evitando)\b(?:\s+\S+){0,2})$",
    re.I,
)

_INTEGRATION_ASK_RE = re.compile(
    r"\b("
    r"integran?\s+con|integraci[oó]n\s+con|se\s+integra\s+con|"
    r"conectan?\s+con|conexi[oó]n\s+con|se\s+conecta\s+con|"
    r"sincronizan?\s+con|sync(?:hroniz\w*)?\s+with|"
    r"api\s+con|plugin\s+de|plugin\s+para|add[- ]?on\s+de|"
    r"compatible\s+con|tienen\s+integraci[oó]n"
    r")\b",
    re.I,
)


def inbound_asks_integration(inbound_text: str | None) -> bool:
    """True si pregunta si hay integración/conexión con otra herramienta (≠ comparación)."""
    raw = _normalize_inbound_for_reply(inbound_text or "")
    return bool(raw and _INTEGRATION_ASK_RE.search(raw))


def _strip_dangling_tails(text: str) -> str:
    """Quita colas a medias en varias pasadas («cuando el» → «cuando» → fin)."""
    out = (text or "").rstrip(".,;: ")
    for _ in range(16):
        nxt = _DANGLING_WORD_TAIL_RE.sub("", out).rstrip(".,;: ")
        nxt2 = _INCOMPLETE_GERUND_CLAUSE_RE.sub("", nxt).rstrip(".,;: ")
        nxt3 = _INCOMPLETE_CONTRAST_TAIL_RE.sub("", nxt2).rstrip(".,;: ")
        if nxt3 == out:
            break
        out = nxt3
    return out


_HANGING_TAIL_TOKEN_RE = re.compile(
    r"\b(cuando|como|cual|que|si|porque|aunque|mientras|donde|quien|"
    r"con|de|del|la|el|los|las|y|o|u|por|para|en|a|al|un|una|sin|sobre|entre|"
    r"tu|su|permitiendo|integrando|incluyendo|haciendo|dejando|mostrando|"
    r"ayudando|usando|logrando|evitando|mediante|"
    r"exclusivamente|unicamente|únicamente|solamente|respectivamente|"
    r"frente|respecto|acerca|dentro|fuera|"
    r"depender|mantener|utilizar|usar|operar)\s*$",
    re.I,
)

# Contraste a medias: «… o depender exclusivamente.» / «… frente a mantener».
_INCOMPLETE_CONTRAST_TAIL_RE = re.compile(
    r"(?:,?\s+(?:frente\s+a|en\s+lugar\s+de|en\s+vez\s+de|o\s+depender|"
    r"o\s+mantener|sin\s+tener\s+que)\b[^.?!]*)$",
    re.I,
)


def utterance_looks_truncated(text: str | None) -> bool:
    """True si el mensaje termina a medias (cualquier canal)."""
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.endswith(("…", "...")):
        return True
    # Clip/corrupción a mitad: «Direct e. comercio…» (letra suelta + punto).
    if re.search(r"(?<![0-9])\b[a-záéíóúñ]\.\s+[a-záéíóúñ]{3,}\b", raw, re.I):
        return True
    core = raw.rstrip(".!?…;: ")
    if not core:
        return True
    if _HANGING_TAIL_TOKEN_RE.search(core):
        return True
    if _INCOMPLETE_GERUND_CLAUSE_RE.search(core):
        return True
    if _INCOMPLETE_CONTRAST_TAIL_RE.search(core):
        return True
    return False


def draft_wrong_contrast_for_integration(inbound: str | None, draft: str | None) -> bool:
    """Integración preguntada pero el draft hace vs/comparación."""
    if not inbound_asks_integration(inbound):
        return False
    low = (draft or "").lower()
    if not low:
        return False
    return bool(
        re.search(
            r"mir[aá]\s+la\s+diferencia|\ben\s+cambio\b|\bvs\.?\b|\bversus\b|"
            r"\bcompar(?:ar|aci[oó]n|alo)\b|foco\s+principal",
            low,
            re.I,
        )
    )


def _finish_sentence(piece: str) -> str:
    piece = (piece or "").strip()
    if not piece:
        return piece
    if piece.endswith((".", "?", "!")):
        return piece
    return piece.rstrip(".,;:") + ("?" if "?" in piece else ".")


def _trim_words(text: str, max_words: int = _LINKEDIN_REPLY_MAX_WORDS) -> str:
    """Corta por oraciones completas; sin '…' ni colas a medias (cualquier producto)."""
    cleaned = strip_email_sign_off((text or "").strip())
    if not cleaned:
        return cleaned
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned if not utterance_looks_truncated(cleaned) else _finish_sentence(
            _strip_dangling_tails(cleaned)
        )

    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    kept: list[str] = []
    count = 0
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        n = len(piece.split())
        if kept and count + n > max_words:
            break
        if not kept and n > max_words:
            # Preferí oración completa aunque pase el cupo (techo ~2.2×) a cortar mal.
            soft = max(int(max_words * 2.2), max_words + 35)
            if n <= soft:
                return _finish_sentence(piece)
            clipped = _finish_sentence(
                _strip_dangling_tails(" ".join(piece.split()[:soft]))
            )
            if utterance_looks_truncated(clipped):
                # Último recurso: primera cláusula antes de gerundio largo.
                m = re.search(
                    r"^(.+?)(?:,\s+(?:permitiendo|integrando|incluyendo)\b)",
                    piece,
                    re.I,
                )
                if m:
                    return _finish_sentence(m.group(1))
            return clipped
        kept.append(piece)
        count += n
        if count >= max_words:
            break
    if kept:
        out = " ".join(kept).strip()
        if utterance_looks_truncated(out):
            out = _finish_sentence(_strip_dangling_tails(out))
        return out
    clipped = _finish_sentence(
        _strip_dangling_tails(" ".join(words[: max(max_words, int(max_words * 1.8))]))
    )
    return clipped


_EMAILISH_CHAT_CLOSE_RE = re.compile(
    r"(?:\n\s*)+"
    r"(?:Quedo atento(?:\s+a[^\n]*)?|Quedo a las \w+|Atentamente|Cordiales saludos)\s*\.?\s*$",
    re.I,
)

_LINKEDIN_INBOUND_COLD_TEMPLATE_RE = re.compile(
    r"(?:"
    r"vuelvo sobre el mensaje anterior|"
    r"armamos un espacio corto|"
    r"si te sirve,\s*avisame|"
    r"pudiste\s+(?:ver|leer|revisar)\s+mi\s+mensaje"
    r")",
    re.I,
)


def strip_linkedin_chat_signoff(draft: str | None) -> str:
    """DM LinkedIn: sin firma de mail ni cierre 'Quedo atento'."""
    text = strip_email_sign_off((draft or "").strip())
    if not text:
        return ""
    return _EMAILISH_CHAT_CLOSE_RE.sub("", text).rstrip()


def _looks_like_cold_email_template(draft: str | None) -> bool:
    """Réplica inbound no puede ser retomo frío de secuencia/mail."""
    return bool(_LINKEDIN_INBOUND_COLD_TEMPLATE_RE.search((draft or "").strip()))


def _ensure_seller_sign(draft: str, campaign: Campaign, db: Session | None = None) -> str:
    """LinkedIn/DM: sin firma de mail. Solo limpia sign-off si el modelo lo metió."""
    del campaign, db  # compat firma histórica
    return strip_linkedin_chat_signoff(draft)


def messages_to_conversation_history(
    messages: Sequence[OutreachMessage],
) -> list[dict[str, str]]:
    from app.services.conversation_history import conversation_payload

    return conversation_payload(messages)


def history_or_load(
    db: Session,
    prospect: Prospect,
    history: Sequence[OutreachMessage] | None,
    *,
    channel: str | None = None,
) -> list[OutreachMessage]:
    if history is not None:
        rows = list(history)
        if channel:
            ch = channel.strip().lower()
            rows = [m for m in rows if (getattr(m, "channel", None) or "").strip().lower() == ch]
        return rows
    from app.services.conversation_history import load_conversation

    try:
        return load_conversation(db, int(prospect.id), channel=channel)
    except Exception:
        logger.exception("load conversation failed prospect_id=%s", getattr(prospect, "id", None))
        return []


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

    if HOW_IT_WORKS_RE.search(low):
        return "how_it_works"

    # «¿Integran con HubSpot?» = integración, NO comparación de productos.
    if inbound_asks_integration(clean):
        return "substantive"

    # «Ya usamos Apollo / Lusha / BambooHR» → mismo path de contraste educado.
    try:
        from app.services.competitor_contrast import extract_competitor_name

        if extract_competitor_name(clean):
            return "differentiation"
    except Exception:
        pass

    if DIFFERENTIATION_RE.search(low):
        return "differentiation"

    if SCHEDULING_RE.search(low):
        return "scheduling"

    if inbound_text_needs_substantive_answer(clean) and _inbound_asks_about_product(clean):
        return "substantive"

    return "interest"


def _inbound_asks_about_product(inbound_text: str) -> bool:
    """True solo si el prospecto pide explicación del producto (no solo interés)."""
    low = _normalize_inbound_for_reply(inbound_text).lower()
    if not low:
        return False
    if price_asked(low) or HOW_IT_WORKS_RE.search(low) or DIFFERENTIATION_RE.search(low):
        return True
    if _PRODUCT_ASK_RE.search(low) or _ADOPTION_ASK_RE.search(low):
        return True
    return "?" in low and bool(_PRODUCT_NOUN_RE.search(low))


def _looks_like_unsolicited_product_pitch(draft: str) -> bool:
    return bool(_UNSOLICITED_PITCH_RE.search((draft or "").strip()))


def _product_short_value(campaign: Campaign) -> str:
    """Propuesta de valor usable en un toque (sin cortar a mitad de idea)."""
    return product_value_pitch(campaign, max_chars=160)


def _clip_complete_thought(text: str, max_chars: int) -> str:
    """
    Recorta la VP de CUALQUIER producto sin partir la idea a medias.
    - Preferí oraciones / ; completas.
    - Nunca cortes en coma (rompe listas tipo A, B y C).
    - Soft stretch (~1.35×) para terminar la primera oración si hace falta.
    """
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ""
    if len(raw) <= max_chars:
        return raw.rstrip(" .") + "."

    # Preferí pensamiento completo: soft stretch alto antes de cortar.
    soft_cap = min(len(raw), max(max_chars, int(max_chars * 1.8)))
    parts = re.split(r"(?<=[.!?])\s+", raw)
    kept: list[str] = []
    total = 0
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        add = len(piece) + (1 if kept else 0)
        if kept and total + add > soft_cap:
            break
        if not kept and len(piece) > soft_cap:
            hard = min(len(piece), int(soft_cap * 1.15))
            window = piece[:hard]
            for sep in (". ", "; "):
                i = window.rfind(sep)
                if i >= max(40, max_chars // 3):
                    return window[:i].strip().rstrip(" ,;:") + "."
            piece_cut = _strip_dangling_tails(window.rsplit(" ", 1)[0].strip())
            out = piece_cut + "."
            if utterance_looks_truncated(out):
                m = re.search(
                    r"^(.+?)(?:,\s+(?:permitiendo|integrando|incluyendo)\b)",
                    piece,
                    re.I,
                )
                if m:
                    return _finish_sentence(m.group(1))
            return out
        kept.append(piece)
        total += add
        if total >= max_chars:
            break
    if kept:
        out = " ".join(kept).rstrip(" .")
        out = out + "."
        if utterance_looks_truncated(out):
            return _finish_sentence(_strip_dangling_tails(out.rstrip(". ")))
        return out
    piece = _strip_dangling_tails(raw[:soft_cap].rsplit(" ", 1)[0].strip())
    out = piece + "."
    if utterance_looks_truncated(out):
        return _finish_sentence(_strip_dangling_tails(piece))
    return out


def _notes_section(notes: str, *headers: str) -> str:
    blob = (notes or "").replace("\r\n", "\n")
    if not blob.strip():
        return ""
    header_re = re.compile(
        r"^(beneficios|pain|problemas|casos de uso|objeciones|tono|p[uú]blico)",
        re.I,
    )
    wanted = tuple(h.lower() for h in headers)
    lines = [ln.strip() for ln in blob.split("\n")]
    capture = False
    out: list[str] = []
    for stripped in lines:
        if not stripped:
            if capture and out:
                break
            continue
        low = stripped.lower()
        if any(low.startswith(h) for h in wanted):
            capture = True
            continue
        if capture:
            if header_re.match(low):
                break
            out.append(stripped)
    return " ".join(out).strip()


def _pitch_seed_index(seed: int | str | None, n: int) -> int:
    if n <= 1:
        return 0
    if seed is None:
        return 0
    if isinstance(seed, int):
        return abs(seed) % n
    digest = 0
    for ch in str(seed).encode("utf-8"):
        digest = (digest * 31 + ch) & 0xFFFFFFFF
    return digest % n


def product_value_pitch_variants(
    campaign: Campaign,
    *,
    max_chars: int = 260,
) -> list[str]:
    """
    2–3 formas de decir la VP del producto (cualquier vertical).
    Misma sustancia, distinta redacción — para que la IA no suene a disco rayado.
    """
    product = _product_payload(campaign)

    def _str_field(key: str) -> str:
        raw = product.get(key)
        return raw.strip() if isinstance(raw, str) else ""

    name = _str_field("name") or "nuestra solución"
    core = _str_field("value_proposition") or _str_field("description")
    if not core:
        return []

    variants: list[str] = []
    # 1) Ficha literal (hechos), recorte seguro.
    variants.append(_clip_complete_thought(core, max_chars))

    def _keeps_list_facts(candidate: str) -> bool:
        """Si la VP nombra una lista A, B y C, la variante no puede perderla."""
        listed = re.findall(
            r"(?:^|,|\by\b)\s*([A-Za-zÁÉÍÓÚñ0-9+][A-Za-zÁÉÍÓÚñ0-9+.\-]{2,})",
            core,
            flags=re.I,
        )
        # Solo listas con 2+ ítems (p. ej. email, LinkedIn y WhatsApp / AWS, Azure y GCP).
        items = [x.lower() for x in listed if x.lower() not in {"con", "entre", "para", "por"}]
        if len(items) < 2:
            return True
        # Tomar los últimos 3 tokens de lista (más específicos).
        must = items[-3:]
        low = candidate.lower()
        return sum(1 for t in must if t in low) >= max(2, len(must) - 1)

    # 2–3) Reescribe SDR anclada a hechos (cualquier producto).
    try:
        from app.services.message_structure_variants import _rewrite_product_value

        for label in ("v0", "v1", "v2"):
            rewritten = (_rewrite_product_value(
                product,
                product_name=name,
                channel="linkedin",
                seed=f"{name}:{label}:{core[:48]}",
            ) or "").strip()
            if not rewritten:
                continue
            clipped = _clip_complete_thought(rewritten, max_chars)
            if _keeps_list_facts(clipped):
                variants.append(clipped)
    except Exception:
        logger.debug("product pitch variants rewrite failed", exc_info=True)

    # Fallback genérico si el rewrite no aportó diversidad.
    if len({re.sub(r"\s+", " ", v.lower()) for v in variants}) < 2:
        low = core[0].lower() + core[1:] if core else core
        variants.append(_clip_complete_thought(f"{name} {low}", max_chars))
        variants.append(
            _clip_complete_thought(
                f"En la práctica, {name} {low}",
                max_chars,
            )
        )

    uniq: list[str] = []
    seen: set[str] = set()
    for v in variants:
        key = re.sub(r"\s+", " ", (v or "").strip().lower())
        if len(key) < 12 or key in seen:
            continue
        seen.add(key)
        uniq.append(v.strip())
        if len(uniq) >= 3:
            break
    return uniq


def product_value_pitch(
    campaign: Campaign,
    *,
    max_chars: int = 260,
    seed: int | str | None = None,
) -> str:
    """Valor real del producto con variedad (seed → una de ~3 formas)."""
    prod_obj = getattr(campaign, "product", None)
    notes = str(getattr(prod_obj, "target_notes", None) or "") if prod_obj is not None else ""
    variants = product_value_pitch_variants(campaign, max_chars=max_chars)
    if not variants:
        return ""
    pitch = variants[_pitch_seed_index(seed, len(variants))]
    benefit = _notes_section(notes, "beneficios principales", "beneficios")
    if benefit:
        first_ben = re.split(r"[.;]", benefit, maxsplit=1)[0].strip()
        # No sumar un beneficio que solo repite métricas ya dichas en la VP.
        redundant = bool(
            re.search(r"\b\d{1,3}\s*%|\bcarga\s+manual\b|\btareas?\s+manual", first_ben, re.I)
        ) and bool(re.search(r"\b\d{1,3}\s*%|\bmanual", pitch, re.I))
        if first_ben and first_ben.lower() not in pitch.lower() and not redundant:
            merged = f"{pitch.rstrip('.')}. {first_ben}."
            if len(merged) <= max_chars + 50:
                pitch = _clip_complete_thought(merged, max_chars + 50)
    return pitch


_GENERIC_CATEGORY_RE = re.compile(
    r"\b(crm|erp|excel|spreadsheet|planilla|whatsapp|email|mailchimp|hubspot|"
    r"salesforce|pipedrive|sap|odoo)\b",
    re.I,
)
# Hasta 4 tokens: «Sales Navigator», «Apollo.io». Sin bare «de/con»
# (falso positivo: «antes de agendar» → “Agendar”).
_CONTRAST_TAIL_RE = re.compile(
    r"\b(?:vs\.?|versus|contra)\s+(?:un|una|el|la)?\s*"
    r"("
    r"[A-Za-zÁÉÍÓÚñ0-9+][A-Za-zÁÉÍÓÚñ0-9+.\-]*"
    r"(?:\s+[A-Za-zÁÉÍÓÚñ0-9+][A-Za-zÁÉÍÓÚñ0-9+.\-]*){0,3}"
    r")\s*\??\s*$",
    re.I,
)
_CONTRAST_TAIL_STOP = frozenset(
    {
        "que",
        "qué",
        "esto",
        "eso",
        "hoy",
        "ahora",
        "una",
        "un",
        "herramienta",
        "plataforma",
        "software",
        "sistema",
        "agendar",
        "agendo",
        "agenda",
        "hablar",
        "llamar",
        "reunir",
        "reunirnos",
        "ver",
        "saber",
        "charlar",
    }
)


def contrasted_tool(inbound: str | None) -> str:
    """Qué nombraron para comparar (CRM, HubSpot, Sales Navigator…). Vacío si no hay."""
    raw = (inbound or "").strip()
    if not raw:
        return ""
    tail = _CONTRAST_TAIL_RE.search(raw)
    if tail:
        word = tail.group(1).strip(" .,;:!?¿¡\"'")
        first = (word.split() or [""])[0].lower()
        # "de 30 a 15min" no es un competidor.
        if word and first not in _CONTRAST_TAIL_STOP and not first[:1].isdigit():
            return word
    hit = _GENERIC_CATEGORY_RE.search(raw)
    return hit.group(1).strip() if hit else ""


def product_contrast_reply(
    campaign: Campaign,
    inbound: str,
    *,
    close: str = "",
    prior_inbounds: list[str] | None = None,
) -> str:
    """Vs tool nombrada: research liviano + VP del producto. Fail-open a plantilla local."""
    try:
        from app.services.competitor_contrast import build_competitor_contrast_reply

        return build_competitor_contrast_reply(
            campaign,
            inbound,
            close=close,
            prior_inbounds=prior_inbounds,
        )
    except Exception:
        product = _product_payload(campaign)
        name = (product.get("name") or "").strip() or "Lo nuestro"
        pitch = product_value_pitch(campaign, max_chars=220)
        other = contrasted_tool(inbound)
        prod_obj = getattr(campaign, "product", None)
        notes = str(getattr(prod_obj, "target_notes", None) or "") if prod_obj is not None else ""
        objections = _notes_section(notes, "objeciones comunes", "objeciones")
        extra_obj = ""
        if objections and other and other.lower() in objections.lower():
            extra_obj = re.split(r"[.;]", objections, maxsplit=1)[0].strip()
        if pitch:
            lead = pitch[0].lower() + pitch[1:] if pitch[0].isupper() else pitch
        else:
            lead = ""
        if other and extra_obj:
            body = extra_obj.rstrip(".") + "."
        elif other and lead:
            body = f"Entiendo que usan {other}. {name} aporta otra capa: {lead}"
        elif lead:
            body = f"La diferencia está en esto: {lead}"
        elif other:
            body = f"Entiendo que usan {other}; te muestro la diferencia en una call corta."
        else:
            body = f"Te cuento la diferencia de {name} en una call corta."
        close_s = (close or "").strip()
        if close_s:
            return (
                f"{body.rstrip('. ')}. {close_s}"
                if not close_s.startswith("¿")
                else f"{body.rstrip()} {close_s}"
            )
        return body


def product_value_sentence(product: str, value: str) -> str:
    """'{producto}: {valor}.' o una invitación a contarlo en llamada si la campaña no tiene propuesta."""
    v = value.strip() if isinstance(value, str) else ""
    v = v.rstrip(".…")
    pname = product.strip() if isinstance(product, str) else ""
    if not v:
        label = pname or "nuestra solución"
        return f"te cuento bien qué hace {label} en una llamada corta."
    # Evitar «Nexus: Con Nexus …» cuando la variante ya nombra el producto.
    if pname and re.match(rf"^(?:con\s+)?{re.escape(pname)}\b", v, flags=re.I):
        out = v[0].upper() + v[1:]
        return out if out.endswith(".") else out + "."
    label = pname or "Producto"
    return f"{label}: {v[0].lower() + v[1:]}."


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
    angle = _inbound_reply_angle(inbound_text)
    pitch_seed = int(getattr(prospect, "id", 0) or 0)
    value = product_value_pitch(campaign, max_chars=260, seed=pitch_seed)
    greet = f"Hola {first}, " if allow_opening_greeting and first else (
        "Hola, " if allow_opening_greeting else ""
    )

    summary = product_value_sentence(product, value)
    how_frames = (
        ("En corto, {summary}", "¿Te sirve una charla breve esta semana?"),
        ("Te lo resumo: {summary}", "¿Lo vemos en una llamada corta?"),
        ("En una línea, {summary}", "¿Te sirve una demo breve esta semana?"),
    )
    frame_lead, frame_cta = how_frames[pitch_seed % len(how_frames)]
    if price_asked(_normalize_inbound_for_reply(inbound_text)):
        body = (
            f"{greet}El pricing depende de lo que necesiten; "
            f"¿lo vemos en una llamada corta y te paso números concretos para tu caso?"
        )
    elif angle == "how_it_works":
        body = f"{greet}{frame_lead.format(summary=summary)} {frame_cta}"
    elif angle == "differentiation":
        contrast = product_contrast_reply(
            campaign,
            inbound_text,
            close="¿Lo comparamos en 15 min con tu caso?",
        )
        body = f"{greet}{contrast}" if greet else contrast
    elif angle == "substantive" and _inbound_asks_about_product(inbound_text):
        body = f"{greet}{frame_lead.format(summary=summary)} {frame_cta}"
    elif angle == "scheduling":
        body = (
            f"{greet}con gusto. ¿Qué día de esta semana te queda bien para una llamada de 15 min?"
            if greet
            else "Con gusto. ¿Qué día de esta semana te queda bien para una llamada de 15 min?"
        )
    else:
        body = (
            f"{greet}¿Te parece agendar una reunión breve de 15 min esta semana?"
            if greet
            else "¿Te parece agendar una reunión breve de 15 min esta semana?"
        )

    body = apply_opening_greeting_policy(body, allow_greeting=allow_opening_greeting)
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    return _trim_words(body)


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
    messages = history_or_load(db, prospect, history)
    conv = messages_to_conversation_history(messages)
    education = campaign_education_blob(
        db, campaign, inbound_reply_channel="linkedin", inbound_text=inbound_text
    )
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
            from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

            draft = _trim_words((draft or "").strip())
            draft = apply_opening_greeting_policy(draft, allow_greeting=allow_greeting)
            draft = strip_linkedin_chat_signoff(draft)
            draft = enforce_one_meeting_cta_per_message(draft)
            allow_pitch = _inbound_asks_about_product(inbound_text)
            if draft and utterance_looks_truncated(draft):
                logger.warning(
                    "linkedin inbound openai truncated prospect_id=%s — fallback",
                    prospect.id,
                )
            elif draft and draft_wrong_contrast_for_integration(inbound_text, draft):
                logger.warning(
                    "linkedin inbound openai contrast-for-integration prospect_id=%s — fallback",
                    prospect.id,
                )
            elif draft and (not allow_pitch) and _looks_like_unsolicited_product_pitch(draft):
                logger.warning(
                    "linkedin inbound openai pitched product without ask prospect_id=%s — fallback",
                    prospect.id,
                )
            elif draft and _looks_like_cold_email_template(draft):
                logger.warning(
                    "linkedin inbound openai returned cold/email template prospect_id=%s — fallback",
                    prospect.id,
                )
            elif draft:
                return draft
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
        from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

        draft = _trim_words(draft)
        draft = apply_opening_greeting_policy(draft, allow_greeting=allow_greeting)
        draft = strip_linkedin_chat_signoff(draft)
        draft = enforce_one_meeting_cta_per_message(draft)
        allow_pitch = _inbound_asks_about_product(inbound_text)
        if draft and (not allow_pitch) and _looks_like_unsolicited_product_pitch(draft):
            draft = ""
        if draft and _looks_like_cold_email_template(draft):
            draft = ""
        if draft:
            return draft
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
