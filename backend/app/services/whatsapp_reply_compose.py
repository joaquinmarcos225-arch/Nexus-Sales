"""Borradores de réplica WhatsApp tras inbound — contestan el texto del prospecto."""

from __future__ import annotations

import logging
import re
import time
from typing import Sequence

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect
from app.services.linkedin_reply_compose import (
    DIFFERENTIATION_RE,
    HOW_IT_WORKS_RE,
    draft_wrong_contrast_for_integration,
    inbound_asks_integration,
    price_asked,
    product_contrast_reply,
    product_value_pitch,
    product_value_sentence,
    utterance_looks_truncated,
    _inbound_asks_about_product,
    _inbound_reply_angle,
    _looks_like_unsolicited_product_pitch,
    _trim_words,
    history_or_load,
    messages_to_conversation_history,
)
from app.services.multichannel_sequence import (
    _campaign_payload,
    _product_payload,
    _prospect_payload,
)
from app.services.outbound_text_normalize import (
    apply_opening_greeting_policy,
    conversation_allows_opening_greeting,
)

logger = logging.getLogger(__name__)

_WHATSAPP_REPLY_MAX_WORDS = 35

_WA_PREFIX_RE = re.compile(
    r"^\[WhatsApp[^\]]*\]\s*",
    re.I,
)

_EMAIL_SIGN_RE = re.compile(
    r"(?:\n|\r\n){0,2}"
    r"(?:saludos|un\s+saludo|atte\.?|atentamente|cordiales?\s+saludos)\s*[,.]?\s*"
    r"(?:(?:\n|\r\n)+\s*\S[\s\S]*|,\s*\S.*)?$",
    re.I,
)
_SIGN_LINE_RE = re.compile(
    r"^(?:saludos|un\s+saludo|atte\.?|atentamente|cordialmente|slds)\s*"
    r"(?:,\s*[A-Za-zÁÉÍÓÚÑáéíóúñ][\wÁÉÍÓÚÑáéíóúñ.\-]{0,40})?$",
    re.I,
)
_NAME_ONLY_LINE_RE = re.compile(
    r"^[A-Za-zÁÉÍÓÚÑáéíóúñ][\wÁÉÍÓÚÑáéíóúñ.\-]{1,24}"
    r"(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ][\wÁÉÍÓÚÑáéíóúñ.\-]{1,24})?$"
)

# El playbook / modelos a veces inventan un Day1 frío en vez de responder.
_COLD_OPEN_RE = re.compile(
    r"\bmi nombre es\b|\bme presento\b|\bte hablo desde\b|\bte contacto desde\b|"
    r"\bsoy\s+\w+\s+de\b|\ble escribo desde\b|\bte contacto porque ayudamos\b",
    re.I,
)

_NOT_INTERESTED_RE = re.compile(
    r"\bno\s+(me|nos)\s+interesa\b|\bno,?\s+gracias\b|\bno\s+estamos\s+interesad\w*|"
    r"\bno\s+es\s+para\s+(nosotros|mi)\b|\bno\s+encaja\b|\bpasamos\b|"
    r"^\s*no\s*[.!,]?\s*$",
    re.I,
)

# Cierre ya mandado tras un «no» — no reabrir venta con CTA de llamada.
_REJECTION_CLOSE_SENT_RE = re.compile(
    r"no te molesto|lo dejamos ac[aá]|no molestarte|gracias por avisar|"
    r"si m[aá]s adelante|quedamos en contacto|quedo a disposici[oó]n|"
    r"\b[eé]xitos\b",
    re.I,
)

_COURTESY_AFTER_NO_RE = re.compile(
    r"^(gracias|graciass+|ok|okay|okey|dale|perfecto|listo|va|bueno|buen[ao]s?"
    r"|understood|thanks|thank you)[\s!.]*$",
    re.I,
)

_SOFT_STAY_IN_TOUCH = "Dale, cualquier cosa me avisás."

_CRM_INTEGRATION_RE = re.compile(
    r"\bintegran?\s+con\s+(?:un\s+)?crm\b|"
    r"\bintegraci[oó]n(?:es)?\s+con\s+(?:un\s+|los\s+)?crm|"
    r"\btienen\s+integraci[oó]n(?:es)?\s+con|"
    r"\b(?:hubspot|salesforce|pipedrive)\b.{0,24}\b(?:integr|conect|sync|plugin|api)\b|"
    r"\b(?:integr|conect|sync|plugin|api).{0,24}\b(?:hubspot|salesforce|pipedrive)\b",
    re.I,
)

# Afirmaciones inventadas sí/no sobre integración (ficha no documenta “no hay CRM”).
_INVENTED_INTEGRATION_CLAIM_RE = re.compile(
    r"("
    r"no\s+tenemos\s+(?:integraci|conexi|soporte|eso)|"
    r"no\s+contamos\s+con\s+(?:integr|eso)|"
    r"no\s+hay\s+integraci|"
    r"no\s+integra(?:mos|n)?\s+con|"
    r"no\s+se\s+integra|"
    r"sin\s+integraci[oó]n|"
    r"(?:lamentablemente|por\s+ahora),?\s+no\b.{0,48}integr|"
    r"s[ií],?\s+(?:tenemos|integramos|hay)\s+integr|"
    r"tenemos\s+integraci[oó]n|"
    r"integramos\s+con\s+(?:hubspot|salesforce|pipedrive|crm)"
    r")",
    re.I,
)

_PAUSE_SEQUENCES_RE = re.compile(
    r"\b(pausar|pausa|detener|frenar)\b.*?\b(secuenc|cadencia|playbook)\b",
    re.I,
)

_JUST_CURIOUS_RE = re.compile(
    r"era para saber|nada m[aá]s|solo preguntaba|era duda|"
    r"no era nada|era por las dudas",
    re.I,
)
_THIN_INBOUND_RE = re.compile(r"^[\s.!?…,;-]*$", re.I)
_QUOTED_MARK_RE = re.compile(r"\[WhatsApp · citado\]\s*(.*)$", re.I | re.S)

_SHORT_YES_RE = re.compile(
    r"^(dale|ok|okay|okey|bueno|de una|perfecto|genial|s[ií]|va|vamos|"
    r"joya|b[aá]rbaro|listo)[\s!.]*$",
    re.I,
)

# Solo horas de reloj 0–23 (evitar “24hs” de SLA/onboarding).
_TIME_HINT_RE = re.compile(
    r"\ba\s+las?\s+([01]?\d|2[0-3])(?::(\d{2}))?\b|"
    r"\b([01]?\d|2[0-3]):(\d{2})\b|"
    r"\b([01]?\d|2[0-3])\s*(?:hs|hrs?|h)\b",
    re.I,
)

_WA_REPLY_RULES = (
    "REGLAS WHATSAPP (réplica, NO primer contacto): "
    "Sos un vendedor experto del producto: respondé como un ChatGPT especializado "
    "en ESE producto (ficha, educación, contraste buscado), no como un bot de frases fijas. "
    "respondé SOLO al último mensaje del prospecto. "
    "Prohibido presentarte de nuevo ('mi nombre es' / 'te hablo desde'). "
    "PROHIBIDO firmar como email (nada de 'Saludos,' ni nombre al final). "
    "PREGUNTAS: leé TODO el mensaje y el hilo; contestá la intención real "
    "(integración ≠ comparación; precio ≠ pitch genérico). "
    "Contestá dudas de producto, precio, cómo funciona, integraciones, "
    "diferencias con otras tools, proceso, alcance. Usá la ficha + educación. "
    "Si preguntan '¿integran con X?': respondé sobre integración/conexión con X. "
    "PROHIBIDO pivotear a 'Mirá la diferencia' / vs / contraste salvo que pidan "
    "comparar o digan que ya usan esa tool. "
    "REGLA UNIVERSAL / GUARDRAIL ESTRICTO (cualquier tema): "
    "Leé TODO el mensaje y entendé la intención real. "
    "si el dato NO está en la ficha/contexto, PROHIBIDO afirmar o negar O describir "
    "capacidades ('sí', 'no', 'contamos con', 'tenemos', 'soportamos', 'ofrecemos', "
    "'hay API', 'puede manejar', 'en varios idiomas', 'ajustando mensajes'). "
    "Solo reconocé el tema con sus palabras y derivá a reunión/call corta con su caso. "
    "Variá siempre la redacción (nunca la misma plantilla). "
    "Si SÍ está en ficha, contestá concreto. "
    "Burbujas: cortá solo en fin de frase (punto/!). Nunca 'Genial la' / mitad de oración. "
    "PROHIBIDO ignorar una pregunta de producto/capacidad y responder solo "
    "'Genial, ¿qué día y horario?' — eso es un fallo grave. "
    "NO inventes features, integraciones, métricas, clientes ni precios. "
    "Nunca cortes una frase a medias. "
    "FORMATO: máximo ~40 palabras en contraste/producto; 2–3 micro-párrafos cortos "
    "(línea en blanco entre beats) para mandar como burbujas, no un choclo. "
    "NO expliques el producto ni beneficios/% si el prospecto NO preguntó "
    "(solo interés / sí / horario → agenda, sin pitch). "
    "Si dice que NO / no le interesa: cierre breve y respetuoso, quedan en contacto, "
    "SIN CTA de llamada ni reabrir venta. "
    "AGENDA — PROHIBIDO ABSOLUTO: no digas que la reunión quedó agendada, confirmada, "
    "reservada ni que 'te dejo agendada' / 'te mando el link un día antes'. "
    "Agendar de verdad = mail del prospecto en el chat + invitación Calendar con Meet. "
    "Si falta el mail, pedilo; PROHIBIDO decir que ya enviaste la invitación o que nos vemos "
    "en la reunión hasta que el mail esté y Calendar confirmó. "
    "Si propone día/hora, NO digas que te queda bien hasta que Calendar confirme. "
    "Pedile el mail para la invitación. NO inventes que ya está agendada. "
    "Máximo 45 palabras en preguntas de producto; 35 en el resto. Tono chat cercano. "
    "NO vuelvas a preguntar algo que el prospecto ya contestó en el hilo "
    "(horario, mail, 'sí a ese'): si ya lo dijo, avanzá. "
    "Si el último mensaje ofrecía un horario/reunión y el prospecto confirma "
    "(dale/sí/confirmado/me viene bien): avanzá el agendamiento (mail/Calendar), "
    "PROHIBIDO 'cualquier cosa me avisás'. "
    "Si son 2+ frases, separalas en micro-párrafos (línea en blanco). "
    "NO empieces con el nombre del prospecto ni lo repitas (nada de 'Juan, …' en cada burbuja). "
    "Nombre/rol/empresa son del primer toque frío, no de la charla. "
    "Si preguntan de dónde sacaste el celular/WhatsApp: bases de LinkedIn / contactos públicos; "
    "ofrecé parar si no quieren que les escribas más. "
    "Si preguntan si sos una IA, un bot o ChatGPT: NEGÁ con claridad. "
    "Hablás en primera persona como el vendedor. Nunca admitas ser IA."
)

_WA_PRODUCT_QA_MAX_WORDS = 45

_SLOT_OFFER_RE = re.compile(
    r"(queda\s+bien|te\s+viene\s+bien|te\s+parece|coordinamos|charla\s+breve|"
    r"llamada\s+corta|lunes|martes|mi[eé]rcoles|jueves|viernes|"
    r"\d{1,2}(?::\d{2})?\s*(?:hs|hrs?)?)",
    re.I,
)
_AFFIRM_MEETING_RE = re.compile(
    r"^(?:dale|ok|okay|okey|perfecto|genial|listo|va|vamos|de\s+una|joya|b[aá]rbaro|"
    r"s[ií]|confirmo|confirmado|me\s+viene\s+bien|me\s+queda\s+bien|"
    r"ah[ií]\s+estoy|nos\s+vemos|seguimos)(?:\s|[!.?,]|$)",
    re.I,
)

_PHONE_ORIGIN_RE = re.compile(
    r"("
    r"(de\s+d[oó]nde|c[oó]mo)\s+(sacaste|conseguiste|obtuviste|ten[eé]s|tienes|tienen)\s+"
    r"(mi\s+)?(celular|n[uú]mero|whatsapp|tel[eé]fono|tel|wsp|wa)\b"
    r"|qui[eé]n\s+te\s+(pas[oó]|dio)\s+(mi\s+)?(celular|n[uú]mero|whatsapp)"
    r"|how\s+did\s+you\s+get\s+my\s+(number|phone|whatsapp)"
    r"|where\s+did\s+you\s+get\s+my\s+(number|phone)"
    r")",
    re.I,
)


def phone_origin_asked(text: str | None) -> bool:
    return bool(_PHONE_ORIGIN_RE.search(text or ""))


def inbound_is_thin(text: str | None) -> bool:
    """Punto, vacío o solo signos: suele ser un reply citado ('ya te lo dije')."""
    return bool(_THIN_INBOUND_RE.match((text or "").strip()))


def looks_like_rejection_close(text: str | None) -> bool:
    if not isinstance(text, str):
        return False
    return bool(_REJECTION_CLOSE_SENT_RE.search(text))


def inbound_is_courtesy_after_no(text: str | None) -> bool:
    """«Gracias» / «ok» cortos tras un no: no reabrir con pitch ni agenda."""
    return bool(_COURTESY_AFTER_NO_RE.match((text or "").strip()))


def last_whatsapp_ai_was_rejection_close(
    db: Session | None, *, prospect_id: int | None
) -> bool:
    if db is None or not prospect_id:
        return False
    try:
        from sqlalchemy import select

        from app.models.outreach import OutreachMessage

        row = db.scalars(
            select(OutreachMessage)
            .where(
                OutreachMessage.prospect_id == int(prospect_id),
                OutreachMessage.direction == "outbound",
                OutreachMessage.channel == "whatsapp",
                OutreachMessage.sender_type == "ai",
            )
            .order_by(OutreachMessage.id.desc())
            .limit(1)
        ).first()
    except Exception:
        return False
    if row is None:
        return False
    return looks_like_rejection_close(row.message or "")


def merge_quoted_inbound(text: str, quoted: str | None = None) -> str:
    """Si citan un mensaje de arriba y mandan '.' / vacío, el citado es el contenido."""
    body = (text or "").strip()
    quote = (quoted or "").strip()
    marked = _QUOTED_MARK_RE.search(body)
    if marked and not quote:
        quote = (marked.group(1) or "").strip()
        body = body[: marked.start()].strip()
    if quote and (inbound_is_thin(body) or not body):
        return quote
    if quote and body:
        return f"{body}\n{quote}"
    return body


def inbound_is_product_aside(text: str | None) -> bool:
    """Pregunta de producto/origen: no re-pedir mail ni repetir el slot."""
    raw = _strip_whatsapp_prefix(text or "")
    if not raw.strip():
        return False
    angle = _inbound_reply_angle(raw)
    if angle in ("how_it_works", "differentiation", "substantive"):
        return True
    if price_asked(raw) or phone_origin_asked(raw):
        return True
    if _JUST_CURIOUS_RE.search(raw) and "@" not in raw:
        return True
    if _CRM_INTEGRATION_RE.search(raw):
        return True
    # «¿Nexus ya lo usan / es piloto?» aunque el ángulo caiga en interest.
    if _inbound_asks_about_product(raw) and ("?" in raw or "¿" in raw):
        return True
    return bool(_inbound_asks_about_product(raw))


def _phone_origin_reply() -> str:
    return (
        "Lo saqué de bases de LinkedIn (contactos públicos). "
        "Si preferís que no te escriba más por acá, avisame y no te molesto."
    )

# El LLM inventaba «te dejo agendada» o «sí, puedo a esa hora» sin Calendar. Bloqueo duro.
_FAKE_BOOKED_RE = re.compile(
    r"("
    r"te\s+dejo\s+agendad|"
    r"te\s+agend[eé]|"
    r"qued[oó]\s+agendad|"
    r"quedamos\s+agendad|"
    r"reuni[oó]n\s+(?:ya\s+)?(?:est[aá]\s+)?agendad|"
    r"llamada\s+(?:ya\s+)?(?:est[aá]\s+)?agendad|"
    r"te\s+mando\s+el\s+link\s+(?:de\s+la\s+reuni[oó]n\s+)?un\s+d[ií]a\s+antes|"
    r"te\s+envio\s+el\s+link\s+un\s+d[ií]a\s+antes|"
    r"perfecto,?\s+te\s+dejo\s+agend|"
    r"te\s+env[ií]o\s+la\s+invitaci[oó]n\s+al\s+mail|"
    r"nos\s+vemos\s+el\s+lunes|"
    r"quedo\s+atento\s+por\s+si\s+necesit|"
    r"s[ií],?\s+(?:puedo|me\s+queda|me\s+viene\s+bien)|"
    r"(?:puedo|me\s+queda\s+bien|me\s+viene\s+bien)\s+(?:a\s+las|ese\s+horario|esa\s+hora|el\s+\w+)|"
    r"dale,?\s+(?:a\s+las|ese\s+horario|esa\s+hora)|"
    r"perfecto,?\s+(?:a\s+las|ma[ñn]ana|el\s+\w+)\b"
    r")",
    re.IGNORECASE,
)

_UNVERIFIED_SLOT_OK_RE = re.compile(
    r"("
    r"s[ií],?\s+(?:puedo|me\s+queda|me\s+viene\s+bien|claro)|"
    r"(?:puedo|me\s+queda\s+bien|me\s+viene\s+bien|me\s+sirve)\s+"
    r"(?:a\s+esa|a\s+las|ese\s+horario|esa\s+hora)|"
    r"dale,?\s+(?:a\s+las|ese\s+horario|esa\s+hora|ma[ñn]ana)|"
    r"ese\s+horario\s+me\s+(?:queda|viene|sirve)|"
    r"esa\s+hora\s+me\s+(?:queda|viene|sirve)|"
    r"quedamos\s+(?:entonces\s+)?(?:a\s+las|el)|"
    r"perfecto,?\s+\d"
    r")",
    re.IGNORECASE,
)

_FOLLOWUP_MONOLOGUE_RE = re.compile(r"follow[\s-]?up\s+autom[aá]tico", re.I)
_RETURN_TO_MEETING_RE = re.compile(
    r"\b(volviendo\s+a\s+la\s+reuni[oó]n|retomando\s+la\s+reuni[oó]n|"
    r"sobre\s+la\s+reuni[oó]n|la\s+reuni[oó]n\s+del)\b",
    re.I,
)
_PENDING_STATUS_RE = re.compile(
    r"\b(qued[oó]\s+algo\s+pendiente|algo\s+pendiente|me\s+fui\s+un\s+rato)\b",
    re.I,
)
_GENERIC_WEEK_RE = re.compile(
    r"coordinamos\s+(?:un\s+)?espacio\s+(?:esta\s+)?semana|"
    r"¿te\s+parece\s+si\s+coordinamos",
    re.I,
)


def draft_off_topic_for_inbound(inbound_text: str, draft: str) -> bool:
    """True si la réplica ignora la pregunta concreta del prospecto."""
    inbound = _strip_whatsapp_prefix(inbound_text).lower()
    body = (draft or "").lower()
    if not inbound or not body:
        return False
    if _FOLLOWUP_MONOLOGUE_RE.search(body) and not re.search(
        r"follow|autom[aá]tico|secuencia", inbound
    ):
        return True
    if _CRM_INTEGRATION_RE.search(inbound) and not re.search(
        r"hubspot|crm|salesforce|sync", body
    ):
        return True
    if price_asked(inbound) and not re.search(
        r"presupuesto|precio|tarifa|plan|costo|call", body
    ):
        return True
    if HOW_IT_WORKS_RE.search(inbound) and not re.search(
        r"nexus|mail|linkedin|whatsapp|prospec|secuenc", body
    ):
        return True
    if DIFFERENTIATION_RE.search(inbound) and not re.search(
        r"diferenc|no es|vs|versus", body
    ):
        return True
    if phone_origin_asked(inbound) and not re.search(
        r"linkedin|bases|p[uú]blic", body
    ):
        return True
    if _JUST_CURIOUS_RE.search(inbound) and re.search(
        r"necesito tu mail|pas[aá]s tu mail|invitaci", body
    ):
        return True
    if _GENERIC_WEEK_RE.search(body) and not re.search(
        r"semana|coordin|horario|d[ií]a", inbound
    ):
        return True
    return False


def _operator_first_name(campaign: Campaign, db: Session | None) -> str:
    sid = getattr(campaign, "seller_id", None)
    if not sid or db is None:
        return ""
    try:
        from app.models.user import User

        user = db.get(User, int(sid))
    except Exception:
        return ""
    if user is None:
        return ""
    first = (getattr(user, "first_name", None) or "").strip()
    if first:
        return first
    name = (getattr(user, "name", None) or "").strip()
    return name.split()[0] if name else ""


_CALENDAR_ONLY_PUSH_RE = re.compile(
    r"(?i)("
    r"qu[eé]\s+d[ií]a\s+y\s+horario|"
    r"horario\s+(?:de\s+)?esta\s+semana|"
    r"te\s+queda\s+bien\s+para\s+una\s+charla|"
    r"charla\s+corta\s+de\s+15|"
    r"quedan\s+c[oó]modos\s+para\s+una\s+charla"
    r")"
)
_DEFER_OR_ANSWER_OK_RE = re.compile(
    r"(?i)("
    r"buena\s+pregunta|lo\s+resolvemos|lo\s+vemos|lo\s+aclaramos|lo\s+charlamos|"
    r"lo\s+conversamos|lo\s+revisamos|lo\s+bajamos|te\s+cuento\s+(?:bien|mejor)|"
    r"en\s+la\s+(?:call|llamada|reuni|demo)|depende\s+del\s+caso|con\s+tu\s+caso|"
    r"te\s+lo\s+(?:paso|cuento|detallo|aclaro|explico)\s+en|call\s+corta|"
    r"llamadita|15\s+min|demo\s+corta|lo\s+vemos\s+ah[ií]|sin\s+el\s+dato\s+exacto|"
    r"mejor\s+lo\s+vemos|te\s+conviene\s+(?:una\s+)?(?:call|llamada|reuni)|"
    r"prefer[ií]a\s+verlo\s+en|lo\s+miramos\s+juntos"
    r")"
)
# Guardrail estricto: sí/no o “contamos con X” sin ficha → invento (aunque también diga call).
_INVENTED_UNDOCUMENTED_CLAIM_RE = re.compile(
    r"(?i)("
    r"\bs[ií]\b[,.]?\s*(?:tenemos|hacemos|ofrecemos|incluye|contamos|disponemos|soportamos)|"
    r"\b(?:contamos\s+con|disponemos\s+de|soportamos|ofrecemos)\b|"
    r"\b(?:no\s+tenemos|no\s+ofrecemos|no\s+hacemos|no\s+contamos|no\s+incluye|no\s+soportamos)\b|"
    r"\bno\s+hay\s+(?:eso|esa|integr|onboard|soporte|api|sso)\b|"
    r"\bclaro\s+que\s+s[ií]\b|\bpor\s+supuesto\s+que\s+s[ií]\b|\bobvio\s+que\s+s[ií]\b|"
    r"\btenemos\s+(?:una?\s+)?(?:api|sso|integraci|onboarding|white\s*label)\b|"
    r"\bhay\s+(?:una?\s+)?(?:api|sso)\b|"
    # Afirmaciones suaves sin «sí» literal («API pública que permite…»).
    r"\bapi\b.{0,40}\b(?:permite|p[uú]blica|nativa|rest|graphql)\w*|"
    r"\b(?:permite|habilita)\s+integraciones?\b|"
    # «puede manejar campañas en varios idiomas / ajustando mensajes…»
    r"\b(?:puede|pueden|pod[eé]s|maneja|manejan|soporta|soportan)\b.{0,80}\b(?:idiomas?|multidioma|api|sso|integraci|white\s*label)\b|"
    r"\b(?:la\s+plataforma|el\s+producto|nexus)\b.{0,40}\b(?:puede|permite|soporta|maneja)\b|"
    r"\bcampa[nñ]as?\s+en\s+varios\s+idiomas\b|"
    r"\bajustando\s+mensajes\s+seg[uú]n\b|"
    r"\ben\s+varios\s+idiomas\b|"
    r"\bes\s+capaz\s+de\b"
    r")"
)
# Tokens cortos: match por palabra (evitar «api» dentro de otras cadenas).
_STRICT_FICHA_TOPIC_TOKENS = frozenset(
    {
        "api",
        "sso",
        "sla",
        "iso",
        "saml",
        "crm",
        "hubspot",
        "salesforce",
        "pipedrive",
        "whatsapp",
        "linkedin",
        "trial",
        "piloto",
    }
)
_QUESTION_STOPWORDS = frozenset(
    {
        "hace",
        "hacen",
        "hacer",
        "tienen",
        "tiene",
        "tener",
        "pueden",
        "puede",
        "quiero",
        "saber",
        "como",
        "cómo",
        "cual",
        "cuál",
        "cuanto",
        "cuánto",
        "donde",
        "dónde",
        "cuando",
        "cuándo",
        "para",
        "sobre",
        "este",
        "esta",
        "esto",
        "esos",
        "esas",
        "una",
        "unos",
        "unas",
        "del",
        "los",
        "las",
        "por",
        "con",
        "sin",
        "mas",
        "más",
        "muy",
        "que",
        "qué",
        "hay",
        "son",
        "esa",
        "eso",
        "tambien",
        "también",
        "algo",
        "nada",
        "todo",
        "hola",
        "buenas",
        "gracias",
        "porfa",
        "porfis",
        "horas",
        "hora",
        "dias",
        "días",
        # Timing / agenda (no son el tema de producto).
        "antes",
        "despues",
        "después",
        "agendar",
        "agenda",
        "reunion",
        "reunión",
        "llamada",
        "call",
        "semana",
        "demo",
        "charla",
        "minutos",
        "minuto",
    }
)


_CAPACITY_WITHOUT_QMARK_RE = re.compile(
    r"(?i)\b("
    r"quiero\s+saber(?:\s+si)?|antes\s+quiero|"
    r"puedo\s+(?:personaliz|configur|cambiar|editar|elegir)|"
    r"personaliz\w*|plantillas?|templates?|"
    r"c[oó]mo\s+arma\w*|arma\s+(?:el\s+|los\s+|la\s+)?mensaje\w*"
    r")\b"
)


def inbound_is_info_question(inbound_text: str | None) -> bool:
    """
    Pregunta de info/capacidad/producto: la lee OpenAI.
    Nunca forzar agenda offline por un regex (ej. «24hs» ≠ horario).
    «Sí, pero quiero saber si…» cuenta como info (no como confirmación de slot).
    """
    clean = _strip_whatsapp_prefix(inbound_text or "")
    if not clean.strip():
        return False
    low = clean.lower()
    if _NOT_INTERESTED_RE.search(low):
        return False
    if _SHORT_YES_RE.match(clean.strip()):
        return False
    try:
        if price_asked(clean) or inbound_asks_integration(clean):
            return True
    except Exception:
        pass
    if _inbound_asks_about_product(clean):
        return True
    if _CAPACITY_WITHOUT_QMARK_RE.search(low):
        return True
    if _FEATURE_TOPIC_RE.search(low):
        return True
    if "?" in clean or "¿" in clean:
        # ¿Día/hora de la reunión? = agenda, no info de producto.
        if _extract_weekday_hint(clean) or _extract_time_hint(clean):
            if not _inbound_asks_about_product(clean) and not _FEATURE_TOPIC_RE.search(low):
                return False
        try:
            from app.services.inbound_turn_orchestrator import _CLEAR_MEETING_INTENT_RE

            if _CLEAR_MEETING_INTENT_RE.search(low) and not _FEATURE_TOPIC_RE.search(low):
                if not _inbound_asks_about_product(clean):
                    return False
        except Exception:
            pass
        return True
    return False


def draft_calendar_push_ignores_question(
    inbound_text: str | None, draft: str | None
) -> bool:
    """True si ignoró la pregunta y solo empujó calendario (Uber-grade fail)."""
    if not inbound_is_info_question(inbound_text):
        return False
    body = (draft or "").strip()
    if not body:
        return False
    if _DEFER_OR_ANSWER_OK_RE.search(body):
        return False
    topic = _feature_topic_label(inbound_text or "")
    if topic != "eso" and topic[:5] in body.lower():
        return False
    return bool(_CALENDAR_ONLY_PUSH_RE.search(body))


def prefer_offline_whatsapp_reply(inbound_text: str) -> bool:
    """
    Solo ramas duras (sí/no/agenda pura/identidad).
    Preguntas de info → SIEMPRE OpenAI (pagamos para que lea el mensaje entero).
    """
    clean = _strip_whatsapp_prefix(inbound_text)
    low = clean.lower()
    angle = _inbound_reply_angle(clean)

    # Cierre / opt-out duro: nunca LLM.
    if _NOT_INTERESTED_RE.search(low):
        return True
    from app.services.whatsapp_humanize import is_ai_identity_question

    if is_ai_identity_question(clean) or phone_origin_asked(clean):
        return True

    # Info/producto/capacidad: jamás offline por heurística de “horario”.
    if inbound_is_info_question(clean):
        return False

    # Agenda determinística: evita «te dejo agendada» inventada.
    if angle == "scheduling" or _extract_time_hint(clean) or _inbound_looks_like_slot_pick(clean):
        return True
    if _RETURN_TO_MEETING_RE.search(low) or _PENDING_STATUS_RE.search(low):
        return True
    if _SHORT_YES_RE.match(clean.strip()):
        return True
    # Solo interés sin pregunta → empujar agenda, sin pitch.
    if angle == "interest" and not _inbound_asks_about_product(clean):
        return True
    if _PAUSE_SEQUENCES_RE.search(low) or ("pausar" in low and "secuenc" in low):
        return True
    # how_it_works / differentiation / precio / CRM / substantive → LLM + education.
    return False


def apply_whatsapp_rejection_closeout(
    prospect: Prospect, *, stop_watching: bool = True
) -> None:
    """
    Tras un «no»: encajonar + no interesado.
    `stop_watching=True` (post-cierre enviado) pone ai_paused para no mirar más el chat.
    En el sync del inbound usar stop_watching=False para no silenciar antes del cierre.
    """
    from app.models.enums import ProspectStatus
    from app.services import multichannel_sequence as mseq

    if (getattr(prospect, "status", None) or "") != ProspectStatus.meeting_booked.value:
        prospect.status = ProspectStatus.not_interested.value
    mseq.mark_encajonado(prospect)
    if stop_watching:
        prospect.ai_paused = True


def _last_whatsapp_outbound_plain(prospect: Prospect, db: Session | None) -> str:
    if db is None or getattr(prospect, "id", None) is None:
        return ""
    from sqlalchemy import select

    row = db.scalars(
        select(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == int(prospect.id),
            OutreachMessage.direction == "outbound",
            OutreachMessage.channel == "whatsapp",
        )
        .order_by(OutreachMessage.id.desc())
    ).first()
    if row is None:
        return ""
    raw = getattr(row, "message", None)
    if not isinstance(raw, str):
        return ""
    return strip_whatsapp_email_signature(_strip_whatsapp_prefix(raw))


def continue_outbound_thread(
    prospect: Prospect,
    campaign: Campaign,
    *,
    db: Session | None = None,
) -> str:
    """Siguiente beat de prospección según estado + último toque, no un caso suelto."""
    when = _stored_slot_phrase(prospect, campaign)
    from app.services.commercial_conversation_state import _commercial, prospect_booking_email

    email = prospect_booking_email(prospect)
    comm = _commercial(prospect)
    status = str(comm.get("status") or "").strip()
    last = _last_whatsapp_outbound_plain(prospect, db).lower()
    if status == "booked" and when:
        return f"Seguimos con {when}."
    if email and when:
        return (
            f"Sigo con {when} y {email}. "
            "Te mando la invitación en cuanto esté Calendar."
        )
    if status == "collecting_email" or (when and not email) or (
        "mail" in last and "invitaci" in last
    ):
        return "¿Me pasás tu mail y te armo la invitación?"
    if email or ("día" in last and "horario" in last) or "charla" in last or "llamada" in last:
        return "¿Qué día y horario te quedan cómodos para una charla corta?"
    return "¿Qué día y horario te quedan cómodos para una charla corta?"


def _stored_slot_phrase(prospect: Prospect, campaign: Campaign) -> str | None:
    from app.services.commercial_conversation_state import _commercial

    raw = (_commercial(prospect).get("slot_at") or "").strip()
    if not raw:
        return None
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from app.services.meeting_booking import _format_slot_for_confirmation

        tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _format_slot_for_confirmation(dt, tz)
    except Exception:
        return None


def draft_claims_unverified_booking(text: str | None) -> bool:
    """True si el borrador afirma que ya se agendó (sin pasar por Calendar real)."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_FAKE_BOOKED_RE.search(raw))


def draft_confirms_unverified_slot(text: str | None) -> bool:
    """True si el borrador dice que ese horario le queda, sin haber cruzado Calendar."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_UNVERIFIED_SLOT_OK_RE.search(raw)) or draft_claims_unverified_booking(raw)


def _strip_whatsapp_prefix(text: str) -> str:
    body = text if isinstance(text, str) else ""
    body = body.strip()
    for prefix in ("[WhatsApp · respuesta real]", "[WhatsApp · inbound]"):
        if body.startswith(prefix):
            body = body.split("\n", 1)[-1].strip()
    body = _WA_PREFIX_RE.sub("", body).strip()
    return body


def strip_whatsapp_email_signature(draft: str) -> str:
    """WhatsApp no usa firma tipo email ('Saludos, Joaquín')."""
    text = (draft or "").strip()
    if not text:
        return text
    cleaned = _EMAIL_SIGN_RE.sub("", text).strip()
    lines = cleaned.splitlines()
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if _SIGN_LINE_RE.match(last):
            lines.pop()
            continue
        if _NAME_ONLY_LINE_RE.match(last) and not re.search(r"[.?!:]", last):
            if len(lines) >= 2:
                prev = lines[-2].strip()
                if _SIGN_LINE_RE.match(prev) or not prev:
                    lines.pop()
                    continue
                # Última línea solo nombre (firma), el párrafo anterior ya cerró la pregunta.
                if re.search(r"[?]$", prev) or _SIGN_LINE_RE.match(prev):
                    lines.pop()
                    continue
            if len(lines) >= 2 and re.search(r"[?]$", lines[-2].strip()):
                lines.pop()
                continue
        break
    return "\n".join(lines).rstrip()


def _looks_like_cold_open(draft: str) -> bool:
    return bool(_COLD_OPEN_RE.search((draft or "").strip()))


def _extract_time_hint(inbound: str) -> str | None:
    """Devuelve hora legible si el prospecto mencionó una (ej. '15hs' → '15:00')."""
    m = _TIME_HINT_RE.search(inbound or "")
    if not m:
        return None
    hour = m.group(1) or m.group(3) or m.group(5)
    minute = m.group(2) or m.group(4) or "00"
    if not hour:
        return None
    h = int(hour)
    if h > 23:
        return None
    return f"{h}:{minute.zfill(2)}"


_WEEKDAY_HINT_RE = re.compile(
    r"\b(el\s+)?(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
    re.I,
)
_SLOT_ACCEPT_RE = re.compile(
    r"("
    r"\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b|"
    r"\ba\s+esa\s+(?:hr|hora)\b|\besa\s+(?:hr|hora)\b|"
    r"\b(me\s+)?(queda|viene|sirve|parece)\s+bien\b|"
    r"\besta\s+bien\b|\bese\s+(?:horario|d[ií]a)\b"
    r")",
    re.I,
)


def _extract_weekday_hint(inbound: str) -> str | None:
    m = _WEEKDAY_HINT_RE.search(inbound or "")
    if not m:
        return None
    day = (m.group(2) or "").strip().lower()
    return f"el {day}" if day else None


def _inbound_looks_like_slot_pick(inbound: str) -> bool:
    """Eligió / confirmó un día u horario ofrecido (aunque no diga 'a las 10')."""
    text = (inbound or "").strip()
    if not text:
        return False
    # Pregunta de info/capacidad ≠ pick de slot (aunque diga «24hs»).
    if inbound_is_info_question(text) and not (
        _extract_weekday_hint(text) and _extract_time_hint(text)
    ):
        # Día+hora juntos puede ser agenda real; info sola no.
        if not (_extract_weekday_hint(text) or _extract_time_hint(text)):
            return False
        if _FEATURE_TOPIC_RE.search(text) or _inbound_asks_about_product(text):
            return False
    try:
        from app.services.inbound_turn_orchestrator import inbound_mentions_scheduling

        if inbound_mentions_scheduling(text):
            return True
    except Exception:
        pass
    return bool(_SLOT_ACCEPT_RE.search(text))


def _budget_or_price_asked(low: str) -> bool:
    return price_asked(low) or bool(re.search(r"\bpresupuesto\b", low, re.I))


def _inbound_has_substance_beyond_affirm(inbound: str) -> bool:
    """
    True si tras un «sí/dale» viene otra cosa (pero/antes/pregunta/capacidad).
    Evita tratar «Sí, pero quiero saber si personalizo…» como confirmación de slot.
    """
    raw = (inbound or "").strip()
    if not raw:
        return False
    rest = re.sub(
        r"^(?:dale|ok|okay|okey|perfecto|genial|listo|va|vamos|de\s+una|joya|"
        r"b[aá]rbaro|s[ií]|confirmo|confirmado|me\s+viene\s+bien|me\s+queda\s+bien|"
        r"ah[ií]\s+estoy|nos\s+vemos|seguimos)[\s!,.]*",
        "",
        raw,
        count=1,
        flags=re.I,
    ).strip()
    if not rest:
        return False
    if re.match(
        r"(?i)^(pero|aunque|antes|igual|solo|solamente|tambi[eé]n|y\s+antes|y\s+quiero)\b",
        rest,
    ):
        return True
    if "?" in rest or "¿" in rest:
        return True
    if _CAPACITY_WITHOUT_QMARK_RE.search(rest) or _inbound_asks_about_product(rest):
        return True
    if _FEATURE_TOPIC_RE.search(rest):
        return True
    # Resto largo = no es un sí pelado.
    return len(rest) >= 18


def _inbound_affirms_offered_slot(inbound: str, *, db: Session | None, prospect: Prospect) -> bool:
    """Último outbound ofreció horario y el prospecto confirma (dale/sí/confirmado)."""
    raw = (inbound or "").strip()
    if not raw or not _AFFIRM_MEETING_RE.search(raw):
        return False
    # «Sí, pero + pregunta» ≠ confirmación de la call ofrecida.
    if _inbound_has_substance_beyond_affirm(raw):
        return False
    if inbound_is_info_question(raw):
        return False
    # Si trae día/hora propia, ya lo cubre scheduling; igual cuenta.
    last = _last_whatsapp_outbound_plain(prospect, db)
    if not last:
        return False
    return bool(_SLOT_OFFER_RE.search(last))


def _wa_beats(*parts: str) -> str:
    """Une beats con línea en blanco → burbujas WA."""
    chunks = [p.strip() for p in parts if (p or "").strip()]
    return "\n\n".join(chunks)


_TOPIC_DISPLAY = {
    "sso": "SSO",
    "api": "API",
    "crm": "CRM",
    "sla": "SLA",
    "saml": "SAML",
    "iso": "ISO",
    "hubspot": "HubSpot",
    "salesforce": "Salesforce",
    "pipedrive": "Pipedrive",
    "linkedin": "LinkedIn",
    "whatsapp": "WhatsApp",
}


def _display_topic(topic: str) -> str:
    raw = (topic or "eso").strip() or "eso"
    return _TOPIC_DISPLAY.get(raw.lower(), raw)


def _offline_defer_variants(topic: str, *, booked_when: str | None) -> list[str]:
    t = _display_topic(topic)
    if booked_when:
        return [
            _wa_beats(
                f"Sobre {t} prefiero no inventarte un sí/no por acá.",
                f"En la call del {booked_when} te lo bajo a tu caso.",
            ),
            _wa_beats(
                f"Eso de {t} depende del setup.",
                f"Lo aclaramos en la reunión del {booked_when}.",
            ),
            _wa_beats(
                f"Lo de {t} lo vemos con precisión en la call del {booked_when}.",
            ),
            _wa_beats(
                f"Sobre {t} te respondo fino en la call del {booked_when}.",
            ),
        ]
    return [
        _wa_beats(
            f"Mirá, lo de {t} lo quiero responderte fino, no a ciegas por chat.",
            "¿Lo vemos 15 min y te lo bajo a tu caso?",
        ),
        _wa_beats(
            f"Sobre {t} no te quiero tirar un sí/no genérico.",
            "¿Lo vemos en una call corta y te lo bajamos a tu caso?",
        ),
        _wa_beats(
            f"Eso de {t} suele depender del stack / del volumen.",
            "Te conviene verlo en 15 min y te digo fino. ¿Te sirve?",
        ),
        _wa_beats(
            f"Sobre {t} preferiría contártelo bien en una llamada corta.",
            "¿Agendamos?",
        ),
        _wa_beats(
            f"Para {t} hay matices según cómo lo usen.",
            "¿Lo miramos juntos en una call corta?",
        ),
        _wa_beats(
            f"No quiero inventarte la respuesta de {t} por chat.",
            "En una call corta te lo dejo claro con tu caso. ¿Te parece?",
        ),
        _wa_beats(
            f"Sobre {t} te lo explico mejor en vivo.",
            "¿Te va una call corta esta semana?",
        ),
        _wa_beats(
            f"Sobre {t} lo bajamos en una reun corta y te digo exactamente cómo aplica.",
            "¿Dale?",
        ),
    ]


_DEFER_MEETING_HINT_RE = re.compile(
    r"(?i)\b(call|llamad\w*|reuni\w*|demo|charla|15\s*min|videollam)\b"
)
_DEFER_BANNED_OPENERS_RE = re.compile(
    r"(?i)^\s*("
    r"buena\s+pregunta\s+sobre|buena\s+esa\s+(?:de|sobre)|"
    r"lo\s+resolvemos\s+en\s+una\s+call\s+corta\s+con\s+tu\s+caso|"
    r"genial\s+la\s+consulta|"
    # Meta-obvio (“Veo que te interesa X”) = huele a bot.
    r"veo\s+que\s+(?:te\s+)?(?:interesa|pregunt)|"
    r"vi\s+que\s+(?:te\s+)?(?:interesa|pregunt)|"
    r"not[eé]\s+que\s+(?:quer[eé]s|pregunt)|"
    r"entiendo\s+que\s+(?:quer[eé]s\s+saber|te\s+interesa|pregunt)"
    r")"
)
# Social proof / capacidades que el defer no puede afirmar sin ficha.
_DEFER_SOFT_LIE_RE = re.compile(
    r"(?i)("
    r"muchos\s+clientes|varios\s+clientes|nuestros\s+clientes|"
    r"muchas\s+empresas|muchas\s+compa[nñ][ií]as|muchos\s+equipos|"
    r"clave\s+para\s+(?:muchos|varios|muchas)|"
    r"relevante\s+para\s+(?:muchos|muchas|varios)|"
    r"la\s+(?:plataforma|herramienta|soluci[oó]n)\s+"
    r"(?:puede|maneja|soporta|permite|se\s+adapta)|"
    r"puede\s+manejar|en\s+varios\s+idiomas|ajustando\s+mensajes|"
    r"c[oó]mo\s+se\s+(?:ajusta|adapta)|"
    r"contamos\s+con|soportamos|incluimos|"
    r"tenemos\s+(?:iso|api|sso|compliance)"
    r")"
)


def _defer_draft_is_usable(inbound_text: str | None, topic_s: str, draft: str) -> bool:
    body = (draft or "").strip()
    if not body or len(body) < 12:
        return False
    probe_inbound = (inbound_text or "").strip() or f"¿{topic_s}?"
    if draft_calendar_push_ignores_question(probe_inbound, body):
        return False
    if _INVENTED_UNDOCUMENTED_CLAIM_RE.search(body):
        return False
    if _DEFER_SOFT_LIE_RE.search(body):
        return False
    if _draft_has_hard_feature_claim(body, probe_inbound):
        return False
    if not (
        _DEFER_OR_ANSWER_OK_RE.search(body) or _DEFER_MEETING_HINT_RE.search(body)
    ):
        return False
    return True


def _openai_defer_unknown_fact(
    *,
    topic: str,
    booked_when: str | None,
    inbound_text: str | None,
    avoid_repeat: str | None = None,
) -> str | None:
    """Respuesta natural vía OpenAI anclada al inbound; None si no hay API / falla."""
    try:
        from app.services import openai_service

        if not openai_service.openai_configured():
            return None
        topic_s = _display_topic(topic)
        inbound = (inbound_text or "").strip()
        if not inbound:
            # Sin el mensaje real la IA inventa / repite plantilla → mejor offline.
            return None
        when = (booked_when or "").strip()
        when_line = (
            f"Ya tienen reunión el {when}: referí a ESA call (no pidas otro horario)."
            if when
            else (
                "Todavía no hay reunión: cerrá invitando a una call/charla corta "
                "(sin inventar día ni hora concreta)."
            )
        )
        avoid = (avoid_repeat or "").strip()
        avoid_line = (
            f"NO repitas ni parafrasees este mensaje anterior tuyo:\n\"\"\"{avoid[:280]}\"\"\""
            if avoid
            else ""
        )
        system = (
            "Sos un SDR B2B por WhatsApp (español rioplatense), humano y comercial.\n"
            "El prospecto preguntó algo que NO está en la ficha del producto.\n"
            "GUARDRAIL DURO — PROHIBIDO:\n"
            "- Afirmar o negar capacidades (sí/no/tenemos/contamos/soportamos/"
            "ofrecemos/incluye/puede manejar/en varios idiomas/ajustando mensajes).\n"
            "- Inventar clientes/empresas, casos, métricas o "
            "'es clave/relevante para muchos'.\n"
            "- Describir cómo funciona el producto, qué 'se adapta' o 'se ajusta'.\n"
            "SÍ hacé: mostrar que leíste el mensaje (nombrá el ángulo: japonés, "
            "white label, SOC2, API…), decir que preferís no tirar data floja por "
            "chat, e invitar a una call corta para bajarlo a SU caso. "
            "Vendés la conversación, no el feature.\n"
            "ESTILO: 1–2 micro-párrafos (línea en blanco = burbuja). Sin firma. "
            "Sin 'Saludos'. Sin markdown.\n"
            "PROHIBIDO abrir con 'Buena pregunta sobre…' / 'Buena esa de…' / "
            "'Lo resolvemos en una call corta con tu caso concreto' / "
            "'Genial la consulta' / 'Veo que te interesa…' / 'Vi que preguntás…' / "
            "'Noté que querés…' / 'Entiendo que querés saber…' "
            "(suena a bot que parafrasea lo obvio).\n"
            "Entrá directo: ángulo concreto + honestidad + CTA a call.\n"
            "Cada respuesta debe sonar distinta: variá apertura, ritmo y CTA."
        )
        # Hasta 3 intentos: plantilla / soft-lie / eco → reescribí.
        for attempt in range(3):
            spice = (
                "Primera versión."
                if attempt == 0
                else (
                    "REESCRIBÍ distinto: otra apertura, otras palabras; "
                    "PROHIBIDO 'Buena pregunta', 'muchos clientes', "
                    "describir features o plantillas."
                )
            )
            user = (
                f"Tema detectado: {topic_s}\n"
                f"Mensaje EXACTO del prospecto:\n\"\"\"{inbound}\"\"\"\n"
                f"{when_line}\n"
                f"{avoid_line}\n"
                f"{spice}\n"
                "Escribí SOLO el mensaje final para WhatsApp."
            )
            raw = openai_service._single_openai_chat(
                system,
                user,
                temperature=1.0 if attempt == 0 else 1.05,
                max_output_tokens=160,
            )
            draft = strip_whatsapp_email_signature((raw.text or "").strip())
            draft = re.sub(r"\n{3,}", "\n\n", draft).strip()
            if not draft:
                continue
            if _DEFER_BANNED_OPENERS_RE.search(draft):
                continue
            if avoid and draft.strip().lower() == avoid.strip().lower():
                continue
            if _defer_draft_is_usable(inbound, topic_s, draft):
                return draft
        return None
    except Exception:
        logger.debug("openai defer unknown fact failed", exc_info=True)
        return None


def unknown_fact_defer_to_meeting(
    *,
    topic: str,
    booked_when: str | None = None,
    inbound_text: str | None = None,
    use_llm: bool = True,
    avoid_repeat: str | None = None,
) -> str:
    """No inventar hechos: la duda se resuelve en la reunión (IA fresca por inbound)."""
    if use_llm:
        llm = _openai_defer_unknown_fact(
            topic=topic,
            booked_when=booked_when,
            inbound_text=inbound_text,
            avoid_repeat=avoid_repeat,
        )
        if llm:
            return llm
        logger.info(
            "unknown_fact_defer fell back to offline topic=%s inbound_len=%s",
            topic,
            len((inbound_text or "").strip()),
        )
    variants = _offline_defer_variants(topic, booked_when=booked_when)
    # Variedad por inbound + topic + tiempo (evita siempre el mismo beat).
    seed = f"{topic}|{booked_when or ''}|{(inbound_text or '').strip()}|{time.time_ns()}"
    idx = abs(hash(seed)) % len(variants)
    return variants[idx]


_FEATURE_TOPIC_RE = re.compile(
    r"\b(onboarding|implementaci[oó]n|implementar|setup|despliegue|capacitaci[oó]n|"
    r"sla|api|soporte|trial|piloto|seguridad|compliance|iso|sso|saml|"
    r"integraci[oó]n(?:es)?|white\s*label|multidioma|idioma|idiomas|"
    r"whatsapp|linkedin|crm|hubspot|salesforce)\b",
    re.I,
)


def _content_keywords(inbound: str) -> list[str]:
    words = re.findall(r"[a-záéíóúüñ]{4,}", (inbound or "").lower())
    out: list[str] = []
    for w in words:
        if w in _QUESTION_STOPWORDS:
            continue
        if w.isdigit():
            continue
        if w not in out:
            out.append(w)
    return out[:8]


def _feature_topic_label(inbound: str) -> str:
    """Etiqueta legible del tema preguntado (cualquier cosa, no solo CRM)."""
    text = inbound or ""
    low = text.lower()
    # Integración CRM: etiqueta rica para el defer (no solo «integración»).
    if inbound_asks_integration(text) or _CRM_INTEGRATION_RE.search(text):
        tool = (
            "HubSpot"
            if "hubspot" in low
            else (
                "Salesforce"
                if "salesforce" in low
                else (
                    "Pipedrive"
                    if "pipedrive" in low
                    else ("CRM" if re.search(r"\bcrms?\b", low) else "ese CRM")
                )
            )
        )
        return f"integración con {tool}"
    if _budget_or_price_asked(low):
        return "precio"
    m = _FEATURE_TOPIC_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", (m.group(1) or "eso")).strip().lower()
    kws = _content_keywords(inbound)
    if kws:
        return kws[0]
    return "eso"


def _product_brief_blob(campaign: Campaign) -> str:
    parts: list[str] = []
    for attr in (
        "product_name",
        "product_description",
        "product_summary",
        "product_notes",
        "value_proposition",
        "notes",
    ):
        val = getattr(campaign, attr, None)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    try:
        payload = _product_payload(campaign)
        for key in (
            "name",
            "description",
            "summary",
            "value",
            "value_proposition",
            "notes",
            "brief",
            "target_notes",
        ):
            val = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(val, str) and val.strip():
                parts.append(val)
        # Objeto product ORM / SimpleNamespace (tests).
        prod = getattr(campaign, "product", None)
        if prod is not None:
            for attr in (
                "name",
                "description",
                "value_proposition",
                "target_notes",
                "notes",
            ):
                val = getattr(prod, attr, None)
                if isinstance(val, str) and val.strip():
                    parts.append(val)
    except Exception:
        pass
    return " ".join(parts).lower()


def _blob_has_topic_token(blob: str, topic: str) -> bool:
    """Match estricto: tokens cortos/técnicos por límite de palabra."""
    t = (topic or "").strip().lower()
    if not t or t == "eso":
        return False
    if " " in t:
        return t in blob
    if t in _STRICT_FICHA_TOPIC_TOKENS or len(t) <= 4:
        return bool(
            re.search(
                rf"(?<![a-záéíóúüñ0-9]){re.escape(t)}(?![a-záéíóúüñ0-9])",
                blob,
                re.I,
            )
        )
    stem = t[:6] if len(t) >= 6 else t
    return bool(
        re.search(rf"(?<![a-záéíóúüñ0-9]){re.escape(stem)}", blob, re.I)
    )


def _ficha_covers_inbound_ask(campaign: Campaign, inbound: str) -> bool:
    """True si la ficha documenta el tema concreto (no substring flojo)."""
    blob = _product_brief_blob(campaign)
    if not blob.strip():
        return False
    # Cómo funciona / en qué se diferencia: basta ficha con sustancia (VP/descripción).
    # No exigir token «antes»/«diferencia» — eso mandaba siempre a call aunque hubiera datos.
    angle = _inbound_reply_angle(inbound)
    if angle in ("how_it_works", "differentiation") and len(blob) > 40:
        return True
    # Tema explícito (sso/api/onboarding…): tiene que aparecer en la ficha.
    topic = _feature_topic_label(inbound)
    if topic and topic != "eso":
        # «precio» sin números en ficha → no cubre (regla: call, no inventar).
        if topic == "precio" or topic.startswith("integración"):
            return _blob_has_topic_token(blob, topic.split()[-1] if topic.startswith("integración") else topic)
        return _blob_has_topic_token(blob, topic)
    kws = _content_keywords(inbound)
    if not kws:
        # «¿Qué hace?» genérico: alcanza con tener ficha.
        return len(blob) > 40
    hits = 0
    for kw in kws:
        if _blob_has_topic_token(blob, kw):
            hits += 1
    # Al menos un keyword distintivo en ficha.
    return hits >= 1


def _ficha_covers_inbound_topic(campaign: Campaign, inbound: str) -> bool:
    """Alias estable para callers viejos."""
    return _ficha_covers_inbound_ask(campaign, inbound)


def draft_invents_integration_answer(inbound_text: str | None, draft: str | None) -> bool:
    """True si inventa sí/no de integración (ficha no afirma ‘no hay CRM’)."""
    inbound = _strip_whatsapp_prefix(inbound_text or "")
    if not (
        inbound_asks_integration(inbound) or _CRM_INTEGRATION_RE.search(inbound)
    ):
        return False
    body = (draft or "").strip()
    if not body:
        return False
    return bool(_INVENTED_INTEGRATION_CLAIM_RE.search(body))


def _draft_has_hard_feature_claim(body: str, inbound: str) -> bool:
    """True si el draft afirma/niega capacidad (sí/no/contamos/permite/puede…)."""
    text = (body or "").strip()
    if not text:
        return False
    if draft_invents_integration_answer(inbound, text):
        return True
    if _INVENTED_UNDOCUMENTED_CLAIM_RE.search(text):
        return True
    topic = _feature_topic_label(inbound)
    if topic and topic != "eso":
        if re.search(
            rf"(?i)\b(?:s[ií]|tenemos|contamos|soportamos|ofrecemos|hay|"
            rf"puede|pueden|maneja|manejan|soporta|soportan|permite|incluye)\b"
            rf".{{0,80}}\b{re.escape(topic)}\b",
            text,
        ):
            return True
        if re.search(
            rf"(?i)\b{re.escape(topic)}\b.{{0,48}}\b(?:permite|p[uú]blica|nativa|inclu|soport|disponible)\w*",
            text,
        ):
            return True
        # Plural cercano: preguntó «idioma» y afirma «varios idiomas».
        if topic.rstrip("s") in ("idioma",) and re.search(
            r"(?i)\b(?:varios\s+)?idiomas?\b|\bmultidioma\b",
            text,
        ) and re.search(
            r"(?i)\b(?:puede|pueden|maneja|manejan|soporta|ajustando|campa[nñ]as?)\b",
            text,
        ):
            return True
    return False


def draft_invents_undocumented_claim(
    campaign: Campaign,
    inbound_text: str | None,
    draft: str | None,
) -> bool:
    """True si afirma sí/no duro sobre un tema que la ficha no documenta."""
    inbound = _strip_whatsapp_prefix(inbound_text or "")
    if not inbound_is_info_question(inbound):
        return False
    body = (draft or "").strip()
    if not body:
        return False
    # Primero detectar afirmación dura; recién después mirar ficha.
    if not _draft_has_hard_feature_claim(body, inbound):
        return False
    # Invento gana aunque también diga «lo vemos en call».
    return not _ficha_covers_inbound_ask(campaign, inbound)


def ensure_info_question_handled(
    campaign: Campaign,
    inbound_text: str | None,
    draft: str | None,
    *,
    booked_when: str | None = None,
    avoid_repeat: str | None = None,
) -> str:
    """
    Gate universal (cualquier pregunta de producto/info):
    - Si hay dato en ficha → se respeta la respuesta (OpenAI).
    - Si no hay dato → SIEMPRE defer fresco a reunión (nunca conservar el draft:
      puede colar hechos suaves aunque suene a call).
    - Nunca agenda ciega («Genial, ¿qué día?») ni invento.
    """
    inbound = _strip_whatsapp_prefix(inbound_text or "")
    body = (draft or "").strip()
    if not inbound_is_info_question(inbound):
        return body
    topic = _feature_topic_label(inbound)
    covers = _ficha_covers_inbound_ask(campaign, inbound)
    if covers:
        # Con ficha: igual bloqueamos inventos fuera de tema / agenda ciega.
        if draft_calendar_push_ignores_question(inbound, body):
            return unknown_fact_defer_to_meeting(
                topic=topic,
                booked_when=booked_when,
                inbound_text=inbound,
                avoid_repeat=avoid_repeat,
            )
        return body
    # Sin cobertura: nunca confiar en el draft del modelo.
    return unknown_fact_defer_to_meeting(
        topic=topic,
        booked_when=booked_when,
        inbound_text=inbound,
        avoid_repeat=avoid_repeat or body or None,
    )


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
    inbound = merge_quoted_inbound(_strip_whatsapp_prefix(inbound_text))
    from app.models.enums import ProspectStatus
    from app.services.commercial_conversation_state import _commercial

    booked_block = _commercial(prospect)
    if (getattr(prospect, "status", None) or "") == ProspectStatus.meeting_booked.value or (
        booked_block.get("status") == "booked" and booked_block.get("calendar_event_id")
    ):
        return ""
    from app.services.whatsapp_humanize import ai_denial_reply, is_ai_identity_question

    if is_ai_identity_question(inbound):
        return ai_denial_reply(
            prospect=prospect,
            operator_first=_operator_first_name(campaign, db),
            continue_with=continue_outbound_thread(prospect, campaign, db=db),
        )
    if phone_origin_asked(inbound):
        return _phone_origin_reply()
    if _JUST_CURIOUS_RE.search(inbound) and "@" not in inbound:
        return "Dale, cualquier cosa me avisás."
    product = (_product_payload(campaign).get("name") or "").strip() or "Nexus Sales"
    angle = _inbound_reply_angle(inbound)
    value = product_value_pitch(
        campaign,
        max_chars=260,
        seed=int(getattr(prospect, "id", 0) or 0),
    )
    low = inbound.lower()
    time_hint = _extract_time_hint(inbound)

    summary = product_value_sentence(product, value)

    from app.services.commercial_conversation_state import (
        _commercial,
        apply_stated_email,
        build_ack_email_have_need_time,
        inbound_already_gave_data_complaint,
        prospect_booking_email as _pbe_early,
    )
    from app.services import meeting_scheduling_extras as extras_early

    comm_early = _commercial(prospect)
    already_booked = bool(
        comm_early.get("status") == "booked" and comm_early.get("calendar_event_id")
    )
    waiting_slot = extras_early.pending_slot(prospect) or extras_early.reserved_slot_for_email_booking(
        prospect
    )
    if (
        waiting_slot is not None
        and not already_booked
        and not inbound_is_product_aside(inbound)
    ):
        when = _stored_slot_phrase(prospect, campaign)
        if not when:
            try:
                from app.services.meeting_booking import _format_slot_for_confirmation

                tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
                when = _format_slot_for_confirmation(waiting_slot["at"], tz)
            except Exception:
                when = None
        known = _pbe_early(prospect) or extras_early.proposed_invite_email(prospect)
        if extras_early.extract_email(inbound) or extras_early.inbound_confirms_proposed_email(
            prospect, inbound
        ):
            em = extras_early.extract_email(inbound) or known
            if em and when:
                return (
                    f"Genial, {when} a {em}. "
                    "Te mando la invitación ahora."
                )
            if em:
                return f"Genial, a {em}. ¿Qué día y horario te queda bien?"
        if when:
            return (
                f"Para {when} necesito tu mail para la invitación. "
                "¿Me lo pasás o confirmás el que te pregunté?"
            )
    from app.services.commercial_conversation_state import prospect_booking_email
    from app.services import meeting_scheduling_extras as extras

    just_email = apply_stated_email(prospect, extras.extract_email(inbound))
    when = _stored_slot_phrase(prospect, campaign)
    known = prospect_booking_email(prospect)
    if just_email and when and known and not already_booked:
        body = (
            f"Perfecto, recibí {known}. Tengo anotado {when}. "
            "Reviso disponibilidad y te mando la invitación."
        )
        return _trim_words(strip_whatsapp_email_signature(body), max_words=_WHATSAPP_REPLY_MAX_WORDS)
    if just_email and known and not when:
        body = build_ack_email_have_need_time(prospect_name=prospect.name, email=known)
        return _trim_words(strip_whatsapp_email_signature(body), max_words=_WHATSAPP_REPLY_MAX_WORDS)

    comm = comm_early
    booked_when = None
    if comm.get("status") == "booked" and comm.get("calendar_event_id"):
        booked_when = _stored_slot_phrase(prospect, campaign)

    if _PENDING_STATUS_RE.search(low):
        email = prospect_booking_email(prospect)
        when = booked_when or _stored_slot_phrase(prospect, campaign)
        if booked_when and email:
            body = (
                f"Sí, la invitación quedó para {when} en {email}. "
                "Si no te llegó al mail, avisame y lo reviso."
            )
        elif email and when:
            body = (
                f"Del lado nuestro falta cerrar la invitación "
                f"para {when} ({email}). Te aviso en cuanto esté."
            )
        elif email:
            body = build_ack_email_have_need_time(prospect_name=prospect.name, email=email)
        else:
            body = (
                "Para la call solo falta que me pases tu mail "
                "y un horario que te sirva."
            )
    elif _RETURN_TO_MEETING_RE.search(low) or (
        inbound_already_gave_data_complaint(inbound) and prospect_booking_email(prospect)
    ):
        email = prospect_booking_email(prospect)
        when = booked_when or _stored_slot_phrase(prospect, campaign) or time_hint
        if booked_when and email:
            body = (
                f"Sí, la invitación quedó para {when} en {email}. "
                "Si no te llegó al mail, avisame y lo reviso."
            )
        elif email and when:
            body = (
                f"Sí, sigo teniendo anotado {when} con {email}. "
                "Reviso disponibilidad y te mando la invitación si el horario está libre."
            )
        elif email:
            body = build_ack_email_have_need_time(prospect_name=prospect.name, email=email)
        elif time_hint:
            body = (
                f"Anoté {time_hint}. Lo cruzo con el calendario y te confirmo. "
                "¿Me pasás tu mail para la invitación?"
            )
        else:
            body = "¿Qué día y horario te queda bien para la charla?"
    elif _SHORT_YES_RE.match(inbound.strip()):
        if _inbound_affirms_offered_slot(inbound, db=db, prospect=prospect):
            known = prospect_booking_email(prospect)
            last = _last_whatsapp_outbound_plain(prospect, db)
            noted = _extract_time_hint(last) or _extract_weekday_hint(last) or "ese horario"
            if known:
                body = _wa_beats(
                    f"Perfecto, {noted}.",
                    f"Lo cruzo con el calendario y te mando la invitación a {known}.",
                )
            else:
                body = _wa_beats(
                    f"Perfecto, {noted}.",
                    "¿Me pasás tu mail para la invitación?",
                )
        else:
            # Sin latiguillo («Genial.»): directo al pedido de horario.
            body = "¿Qué día y horario te queda bien para una charla corta?"
    elif _NOT_INTERESTED_RE.search(low):
        # Cierre duro: respetuoso, quedan en contacto, SIN CTA de llamada.
        body = (
            "Entendido, gracias por avisar. "
            "Lo dejamos acá y quedamos en contacto; si más adelante cambia, me escribís. Chau."
        )
    elif last_whatsapp_ai_was_rejection_close(db, prospect_id=getattr(prospect, "id", None)) and (
        inbound_is_courtesy_after_no(inbound) or inbound_is_thin(inbound)
    ):
        # Ya dijo que no; un «gracias» no reabre la venta.
        return ""
    elif _budget_or_price_asked(low):
        if booked_when:
            body = (
                f"El número depende del caso. "
                f"Te lo paso con precisión en la call del {booked_when}."
            )
        else:
            body = (
                "Depende de lo que necesiten; "
                "¿lo vemos en una call corta y te paso números para tu caso?"
            )
    elif inbound_asks_integration(inbound) or _CRM_INTEGRATION_RE.search(low):
        # Sin inventar sí/no: la integración se aclara en la reunión.
        tool = "HubSpot" if "hubspot" in low else (
            "Salesforce" if "salesforce" in low else (
                "Pipedrive" if "pipedrive" in low else "ese CRM"
            )
        )
        if re.search(r"\bcrms?\b", low) and tool == "ese CRM":
            tool = "CRM"
        body = unknown_fact_defer_to_meeting(
            topic=f"integración con {tool}",
            booked_when=booked_when,
            inbound_text=inbound,
        )
    elif "pausar" in low and "secuenc" in low:
        body = "Sí, se puede pausar cuando hace falta; no sigue mandando toques en pausa."
    elif _PAUSE_SEQUENCES_RE.search(low):
        body = "Sí, se puede pausar cuando hace falta; no sigue mandando toques en pausa."
    elif angle == "how_it_works":
        cta = (
            f"Lo vemos en la call del {booked_when}."
            if booked_when
            else "¿Querés que te lo muestre en una demo corta?"
        )
        if summary:
            # Una idea corta + CTA en burbujas (no choclo).
            lead = summary
            if len(lead.split()) > 28:
                lead = _trim_words(lead, max_words=28)
            body = _wa_beats(lead, cta)
        else:
            body = _wa_beats(
                f"Te cuento bien qué hace {product} en una call corta.",
                cta,
            )
    elif angle == "differentiation":
        extra = (
            f"Lo vemos en la call del {booked_when}."
            if booked_when
            else "¿Lo vemos en una call corta?"
        )
        prior: list[str] = []
        if db is not None and getattr(prospect, "id", None) is not None:
            try:
                from app.services.conversation_history import history_text, load_conversation

                hist = load_conversation(db, int(prospect.id), channel="whatsapp") or []
                for row in reversed(hist[-16:]):
                    if (getattr(row, "direction", None) or "").strip().lower() != "inbound":
                        continue
                    msg = (history_text(row) or "").strip()
                    if msg and msg != inbound:
                        prior.append(msg)
            except Exception:
                prior = []
        contrast = product_contrast_reply(
            campaign, inbound, close="", prior_inbounds=prior
        )
        # Corto + CTA aparte → 2–3 burbujas.
        contrast = _trim_words(contrast, max_words=32)
        body = _wa_beats(contrast, extra)
    elif angle == "substantive" and _inbound_asks_about_product(inbound):
        topic = _feature_topic_label(inbound)
        # Cualquier pregunta concreta: ficha o defer — nunca pitch genérico.
        if not _ficha_covers_inbound_ask(campaign, inbound):
            body = unknown_fact_defer_to_meeting(
                topic=topic,
                booked_when=booked_when,
                inbound_text=inbound,
            )
        else:
            cta = (
                f"Lo vemos en la call del {booked_when}."
                if booked_when
                else "¿Te sirve una demo rápida?"
            )
            lead = (
                _trim_words(summary, max_words=28)
                if summary
                else f"Te lo detallo en una call corta."
            )
            body = _wa_beats(lead, cta)
    elif (
        time_hint
        or angle == "scheduling"
        or _inbound_looks_like_slot_pick(inbound)
        or _inbound_affirms_offered_slot(inbound, db=db, prospect=prospect)
    ) and not (
        # Cualquier pregunta de info ≠ “elegí horario”.
        # «Sí, pero quiero saber si…» tampoco.
        (
            inbound_is_info_question(inbound)
            or _inbound_has_substance_beyond_affirm(inbound)
        )
        and not time_hint
        and not _extract_weekday_hint(inbound)
    ):
        known = prospect_booking_email(prospect)
        day_hint = _extract_weekday_hint(inbound)
        noted = time_hint or day_hint
        if (
            not noted
            and _inbound_affirms_offered_slot(inbound, db=db, prospect=prospect)
        ):
            # Confirmó el horario que ofrecimos: seguir agendamiento.
            last = _last_whatsapp_outbound_plain(prospect, db)
            noted = _extract_time_hint(last) or _extract_weekday_hint(last) or "ese horario"
        if inbound_already_gave_data_complaint(inbound) and known:
            body = build_ack_email_have_need_time(prospect_name=prospect.name, email=known)
        elif known and noted:
            body = _wa_beats(
                f"Anoté {noted}.",
                f"Lo cruzo con el calendario y, si está libre, te mando la invitación a {known}.",
            )
        elif known:
            body = build_ack_email_have_need_time(prospect_name=prospect.name, email=known)
        elif noted:
            body = _wa_beats(
                f"Anoté {noted}. Lo cruzo con el calendario y te confirmo.",
                "¿Me pasás tu mail para la invitación?",
            )
        else:
            body = _wa_beats(
                "Genial.",
                "¿Qué día y horario de esta semana te queda bien para una charla corta?",
            )
    elif (
        re.search(r"\b(hola|buenas)\b", low)
        and re.search(r"\b(me interesa|nos interesa|interesa)\b", low)
        and not _inbound_looks_like_slot_pick(inbound)
    ):
        # Solo interés, sin día/hora: empujar a agenda — NUNCA pitch de producto.
        body = (
            "Genial. ¿Qué día y horario te queda bien para una charla corta "
            "de 15 minutos?"
        )
    elif re.search(r"equipo\s+chico|pyme|startup", low):
        body = (
            "Sí, Nexus suele encajar bien en equipos chicos: un SDR cubre más cuentas "
            "con secuencias en mail, LinkedIn y WhatsApp. ¿Querés ver un ejemplo concreto?"
        )
    else:
        # Fallback si OpenAI no corrió: si parece pregunta, respondé con lo que hay del producto.
        try:
            from app.services.openai_service import inbound_text_needs_substantive_answer

            looks_q = inbound_text_needs_substantive_answer(inbound) or ("?" in inbound)
        except Exception:
            looks_q = "?" in inbound
        if looks_q:
            # Pregunta sin dato claro → reunión, no inventar ni “cualquier cosa”.
            body = unknown_fact_defer_to_meeting(
                topic=_feature_topic_label(inbound),
                booked_when=booked_when,
                inbound_text=inbound,
            )
        elif _inbound_affirms_offered_slot(inbound, db=db, prospect=prospect):
            body = continue_outbound_thread(prospect, campaign, db=db)
        else:
            # Si el hilo venía de agenda, no cortar con soft-stay.
            last = _last_whatsapp_outbound_plain(prospect, db).lower()
            if _SLOT_OFFER_RE.search(last) or "mail" in last or "invitaci" in last:
                body = continue_outbound_thread(prospect, campaign, db=db)
            else:
                body = _SOFT_STAY_IN_TOUCH

    cap = (
        _WA_PRODUCT_QA_MAX_WORDS
        if angle in ("how_it_works", "differentiation", "substantive")
        or _inbound_asks_about_product(inbound)
        or inbound_is_info_question(inbound)
        else _WHATSAPP_REPLY_MAX_WORDS
    )
    from app.services.whatsapp_humanize import strip_chat_name_vocative

    body = ensure_info_question_handled(
        campaign,
        inbound,
        body,
        booked_when=booked_when,
        avoid_repeat=_last_whatsapp_outbound_plain(prospect, db) or None,
    )
    from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

    allow_greet = conversation_allows_opening_greeting(
        history_or_load(db, prospect, None) if db is not None else []
    )
    body = apply_opening_greeting_policy(
        strip_whatsapp_email_signature(body),
        allow_greeting=allow_greet,
    )
    body = enforce_one_meeting_cta_per_message(body)
    return strip_chat_name_vocative(
        _trim_words(body, max_words=cap),
        prospect,
    )


def _meeting_already_booked_rule(db: Session, prospect: Prospect, campaign: Campaign) -> str:
    from app.services.meeting_booking import _active_meeting_row, _format_slot_for_confirmation

    try:
        row = _active_meeting_row(db, prospect)
    except Exception:
        return ""
    if row is None or row.scheduled_for is None:
        return ""
    tz = (getattr(campaign, "timezone", None) or "America/Argentina/Buenos_Aires").strip()
    when = _format_slot_for_confirmation(row.scheduled_for, tz)
    return (
        f"\n\nYA HAY REUNIÓN AGENDADA con esta persona ({when}). No propongas otra reunión ni horarios. "
        "No preguntes si le parece charlarlo ni pidas confirmar la reunión: ya está confirmada. "
        "Respondé su duda en 1-2 líneas; si no tenés el dato exacto, afirmá que lo ven en esa llamada "
        "(ej. «Depende del volumen; en la llamada del viernes te paso la propuesta exacta.»)."
    )


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
    from app.services.whatsapp_humanize import ai_denial_reply, is_ai_identity_question

    if is_ai_identity_question(inbound_text):
        return ai_denial_reply(
            prospect=prospect,
            operator_first=_operator_first_name(campaign, db),
            continue_with=continue_outbound_thread(prospect, campaign, db=db),
        )
    if not inbound_text:
        return continue_outbound_thread(prospect, campaign, db=db)

    if prefer_offline_whatsapp_reply(inbound_text):
        return whatsapp_inbound_offline_draft(
            prospect, campaign, inbound_text=inbound_text, db=db
        )

    # Pregunta de producto sin dato en ficha → defer IA directo (no dejar que el
    # modelo principal invente capacidades y después haya que limpiar).
    if inbound_is_info_question(inbound_text) and not _ficha_covers_inbound_ask(
        campaign, inbound_text
    ):
        from app.services.whatsapp_humanize import strip_chat_name_vocative

        last_out = _last_whatsapp_outbound_plain(prospect, db)
        safe = unknown_fact_defer_to_meeting(
            topic=_feature_topic_label(inbound_text),
            booked_when=None,
            inbound_text=inbound_text,
            avoid_repeat=last_out or None,
        )
        return strip_chat_name_vocative(safe, prospect)

    messages = history_or_load(db, prospect, history, channel="whatsapp")
    conv = messages_to_conversation_history(messages)
    education = campaign_education_blob(
        db, campaign, inbound_reply_channel="whatsapp", inbound_text=inbound_text
    )
    allow_pitch = _inbound_asks_about_product(inbound_text)
    seller_country = ""
    try:
        from app.services.user_outreach_style import style_record_for_user

        st = style_record_for_user(db, getattr(campaign, "seller_id", None))
        seller_country = (getattr(st, "country", None) or "").strip() if st is not None else ""
    except Exception:
        seller_country = ""
    from app.services.outreach_language import dialect_from_country, dialect_prompt_block

    education = (
        f"{education}\n\n{_WA_REPLY_RULES}{_meeting_already_booked_rule(db, prospect, campaign)}"
        f"{dialect_prompt_block(dialect_from_country(seller_country))}"
    )

    product_qa = allow_pitch or _inbound_reply_angle(inbound_text) in (
        "how_it_works",
        "differentiation",
        "substantive",
    )
    word_cap = _WA_PRODUCT_QA_MAX_WORDS if product_qa else _WHATSAPP_REPLY_MAX_WORDS
    if product_qa:
        education = (
            f"{education}\n\n"
            "MODO PRODUCTO-QA (cualquier pregunta sobre el producto): "
            "1) Leé TODO el mensaje. 2) Si el dato está en ficha/educación, contestá concreto. "
            "3) Si NO está: PROHIBIDO sí/no/'contamos con'/'tenemos API'/'puede manejar'/"
            "'en varios idiomas' — solo defer a call, redacción NUEVA cada vez. "
            "4) PROHIBIDO responder solo pidiendo día/horario. "
            "5) Burbujas solo en fin de frase. "
            "No inventes. No pivotees a pitch genérico ni a agenda ciega."
        )

    if openai_service.openai_configured():
        try:
            draft = openai_service.generate_linkedin_inbound_reply(
                prospect=_prospect_payload(prospect),
                inbound_message=inbound_text,
                conversation_history=conv,
                campaign={
                    **_campaign_payload(campaign),
                    "name": f"{campaign.name or ''} (WhatsApp réplica — NO primer contacto)",
                    "seller_country": seller_country,
                },
                product=_product_payload(campaign),
                education=education,
                interest_level=getattr(prospect, "interest_level", None) or "medium",
                allow_soft_meeting_close=True,
                allow_opening_greeting=False,
                max_words=word_cap,
            )
            from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

            draft = _trim_words((draft or "").strip(), max_words=word_cap)
            draft = strip_whatsapp_email_signature(draft)
            # WA casi nunca saluda; igual sacamos latiguillos (Buena pregunta / Perfecto…).
            allow_greet = conversation_allows_opening_greeting(messages)
            draft = apply_opening_greeting_policy(draft, allow_greeting=allow_greet)
            draft = enforce_one_meeting_cta_per_message(draft)
            if draft and utterance_looks_truncated(draft):
                logger.warning(
                    "whatsapp inbound openai truncated prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft and draft_wrong_contrast_for_integration(inbound_text, draft):
                logger.warning(
                    "whatsapp inbound openai contrast-for-integration prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft and _looks_like_cold_open(draft):
                logger.warning(
                    "whatsapp inbound openai returned cold-open prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft and (not allow_pitch) and _looks_like_unsolicited_product_pitch(draft):
                logger.warning(
                    "whatsapp inbound openai pitched product without ask prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft and draft_off_topic_for_inbound(inbound_text, draft):
                logger.warning(
                    "whatsapp inbound openai off-topic prospect_id=%s — offline",
                    prospect.id,
                )
            elif draft and draft_invents_integration_answer(inbound_text, draft):
                logger.warning(
                    "whatsapp inbound openai invented integration yes/no "
                    "prospect_id=%s — llm defer",
                    prospect.id,
                )
                from app.services.whatsapp_humanize import strip_chat_name_vocative

                safe = unknown_fact_defer_to_meeting(
                    topic=_feature_topic_label(inbound_text),
                    booked_when=None,
                    inbound_text=inbound_text,
                    avoid_repeat=draft,
                )
                return strip_chat_name_vocative(safe, prospect)
            elif draft and draft_invents_undocumented_claim(
                campaign, inbound_text, draft
            ):
                logger.warning(
                    "whatsapp inbound openai invented undocumented yes/no "
                    "prospect_id=%s — llm defer",
                    prospect.id,
                )
                from app.services.whatsapp_humanize import strip_chat_name_vocative

                safe = unknown_fact_defer_to_meeting(
                    topic=_feature_topic_label(inbound_text),
                    booked_when=None,
                    inbound_text=inbound_text,
                    avoid_repeat=draft,
                )
                return strip_chat_name_vocative(safe, prospect)
            elif draft and draft_calendar_push_ignores_question(inbound_text, draft):
                logger.warning(
                    "whatsapp inbound openai calendar-push ignored question "
                    "prospect_id=%s — llm defer",
                    prospect.id,
                )
                from app.services.whatsapp_humanize import strip_chat_name_vocative

                safe = unknown_fact_defer_to_meeting(
                    topic=_feature_topic_label(inbound_text),
                    booked_when=None,
                    inbound_text=inbound_text,
                    avoid_repeat=draft,
                )
                return strip_chat_name_vocative(safe, prospect)
            elif draft:
                if draft_claims_unverified_booking(draft):
                    logger.warning(
                        "whatsapp inbound openai fake-booking prospect_id=%s — offline",
                        prospect.id,
                    )
                else:
                    from app.services.whatsapp_humanize import strip_chat_name_vocative

                    safe = ensure_info_question_handled(
                        campaign, inbound_text, draft, booked_when=None
                    )
                    return strip_chat_name_vocative(safe, prospect)
        except Exception:
            logger.exception(
                "openai whatsapp inbound reply failed prospect_id=%s — offline",
                prospect.id,
            )

    return whatsapp_inbound_offline_draft(
        prospect, campaign, inbound_text=inbound_text, db=db
    )
