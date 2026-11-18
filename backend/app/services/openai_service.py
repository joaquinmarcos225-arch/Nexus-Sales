"""
Capa OpenAI para outreach y asistente interno.

Separado de conectores de envío (LinkedIn/Gmail/WhatsApp) para reutilizar
mensajes cuando existan integraciones reales.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Reintentos ante rate limit: 1 intento inicial + 3 reintentos con esperas 2s → 5s → 10s.
OPENAI_RATE_LIMIT_RETRY_DELAYS_SEC = (2.0, 5.0, 10.0)
OPENAI_RATE_LIMIT_MAX_ATTEMPTS = 1 + len(OPENAI_RATE_LIMIT_RETRY_DELAYS_SEC)

from app.services.ai_behavior_policy import (
    DEFAULT_POLICY,
    AiBehaviorPolicy,
    behavior_prompt_section,
    should_inject_calendar_link,
)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_INBOUND = (os.getenv("OPENAI_MODEL_INBOUND") or "").strip() or MODEL
OPENAI_ENDPOINT = "responses.create"


def openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())

_BANNED_SNIPPETS = re.compile(
    r"\b(?:optimizar|gesti[oó]n|equipo comercial|"
    r"coordinar\s+(?:una\s+)?reuni[oó]n|coordinaci[oó]n\s+de\s+reuni[oó]n)\b",
    re.IGNORECASE,
)
_SOFT_BANNED = re.compile(r"\bayud(ar|arte|emos)\b", re.IGNORECASE)
_FOLLOWUP_AUTOMATION_BANNED = re.compile(
    r"(solo quer[ií]a hacer seguimiento|retomo este mensaje|circling back|"
    r"te escribo de nuevo para|quer[ií]a retomar|un seguimiento r[aá]pido|"
    r"siguiendo mi mensaje anterior|como te comentaba en mi (?:mail|mensaje) anterior)",
    re.IGNORECASE,
)

VOICE_GLOBAL = """
Sos SDR/AE por mensaje (B2B, español): natural, breve, con criterio comercial.

OBJETIVO #1: cuando hay encaje o interés, llevar la conversación hacia una llamada breve (10–15 min).
NO usar el chat para "explicar el producto completo", mandar resúmenes largos ni substituir una demo por texto.

OBLIGATORIO:
- Texto plano. Sin markdown, sin listas numeradas ni viñetas.
- Mensajes CORTOS: outbound frío 3–4 líneas máximo; follow-ups 1–2 líneas; respuesta a prospecto 1–3 líneas salvo objeción puntual mínima.
- Curiosidad y una sola pregunta clara; no encadenar 5 temas.
- No digas que sos IA, bot o herramienta.

PROHIBIDO (no uses estas ideas ni variantes):
- "te envío / te paso / te mando un resumen, material, más información, brochure"
- "te explico todo acá / por chat"
- bloques largos tipo mail de marketing

Preferí:
- Si el prospecto muestra interés: respondé mínimo y proponé coordinar 15 min en voz (valor: ver en vivo, ahorrar ida y vuelta).
- Si pide info: una frase breve + ofrecé cerrarlo en una llamada corta en vez de prometer paquetes de material.
- Si hubo objeción: tratá esa capa en una frase y volvé a la llamada breve si encaja.
- léxico humano; nada de discourse corporativo infinito.

EVITA además:
- clichés "optimizar", "gestión", "equipo comercial" de forma repetida.
- repetir el mismo CTA de reunión texto por texto que en mensajes anteriores.
- follow-ups tipo "solo quería hacer seguimiento", "retomo", "circling back".
- en campañas con muchos prospectos: copiar la misma frase de ancla o valor en todos;
  cada persona debe sonar levemente distinta (sinónimos, orden, gancho) manteniendo el mismo tono.
- mencionar dominios técnicos, emails internos, errores de entrega, mail.nexus-sales.local,
  infraestructura de prueba o que el correo del prospecto "no funciona".
"""

VOICE_GLOBAL_B2C = """
Sos vendedor/a consultivo por mensaje (B2C / consumidor final, español): natural, cercano, breve.

OBJETIVO #1: cuando hay interés, coordinar una llamada corta o el siguiente paso concreto (demo, prueba, compra guiada).
NO escribir como si hablaras con una empresa (evitar "su equipo", "en su organización", "ROI para la compañía").

OBLIGATORIO:
- Texto plano. Sin markdown ni listas.
- Mensajes CORTOS: cold 3–4 líneas; follow-ups 1–2; respuestas 1–3 salvo una aclaración mínima.
- Hablarle a la persona: beneficios personales, tiempo, dinero, comodidad, bienestar o gusto según el producto.
- Una sola pregunta clara. No digas que sos IA.

PROHIBIDO:
- pitch B2B ("escalar ventas", "pipeline", "equipo comercial")
- "te mando un brochure / material / PDF" como única respuesta
- bloques de marketing largos

Preferí:
- Gancho con interés real de la persona (hobby, necesidad, ubicación) si está en contexto.
- Si hay interés: una frase útil + proponé 10–15 min o el CTA del producto.
- Léxico humano y cercano; sin jerga corporativa.
"""

# Respuestas a inbound (hilo con preguntas del prospecto): consultivo, no "calendar pusher".
VOICE_INBOUND_CONSULTATIVE = """
Sos un vendedor consultivo B2B por email (español): útil, claro, humano, técnico cuando hace falta, confiable.

PRIORIDAD #1 — OBLIGATORIO:
Respondé primero de forma concreta y útil lo que el prospecto preguntó o comentó.
Leé TODO el mensaje/hilo y contestá la intención real (integración ≠ comparación).
«¿Integran / conectan con X?» → respondé sobre integración/conexión con X.
PROHIBIDO armar un vs / «Mirá la diferencia» si solo preguntaron si hay integración.
Usá SOLO hechos del producto/campaña en contexto. PROHIBIDO inventar precios, planes,
clientes, métricas o features que no estén escritos ahí.
REGLA UNIVERSAL / GUARDRAIL ESTRICTO (cualquier tema): leé TODO el mensaje.
Si el dato NO está en contexto/ficha, PROHIBIDO afirmar o negar
(«sí», «no», «contamos con», «tenemos», «soportamos», «ofrecemos», «hay API»).
Solo reconocé el tema y derivá a verlo en la reunión / call corta.
Si SÍ está en contexto, contestá concreto.
PROHIBIDO ignorar una pregunta (onboarding, integración, feature, etc.) y
responder solo pidiendo día/horario («Genial, ¿qué día…?»): eso es un fallo grave.
Terminá cada idea: nunca cortes una frase a medias ni con «…».
Si preguntan qué hace / cómo funciona / implementación y HAY dato en contexto: respondé claro.
Si preguntan precio/cobro y NO hay pricing en contexto: no inventes cifra ni “por usuario/mensual”.
  Decí que depende del caso y proponé verlo en una call corta (honestidad > invento).
Si el prospecto INSISTE en el número antes de agendar y no hay dato: reconocé el pedido,
explicá que sin seats/alcance no hay cifra seria por mail, y ofrecé armar la propuesta en 15 min.

PRIORIDAD #2 — SECUNDARIO:
Recién al final, sin presión, podés invitar a profundizar en una llamada corta o usar el link de agenda
solo si está en contexto y el tono del hilo lo permite (una frase suave, no insistir).

PROHIBIDO:
- Inventar precios, “licencia por usuario”, planes o números que no estén en el contexto.
- Responder solo "te lo cuento en la reunión" cuando SÍ hay dato en contexto para contestar.
- "Te paso material después" o "te mando un PDF" como sustituto de una respuesta útil.
- CTA agresivo o repetir "agendemos" en cada mensaje.
- Sonar a bot, script o SDR robótico.
- Mencionar dominios internos, mail.nexus-sales.local, errores de entrega o infraestructura de prueba.

ESTILO:
- Texto plano; sin markdown ni listas numeradas largas.
- FORMATO MAIL: línea en blanco SOLO al cambiar de tema (no cada oración).
  Típico: saludo · respuesta (puede tener 2–3 oraciones juntas) · cierre suave.
  PROHIBIDO muro sin saltos; también PROHIBIDO atomizar cada frase en su párrafo.
- Sé concreto y breve: preferí 2–3 párrafos cortos; evitá mails largos.
- Cierre opcional y suave hacia siguiente paso (demo/reunión) solo después de aportar valor.
"""

VOICE_INBOUND_CONSULTATIVE_B2C = VOICE_INBOUND_CONSULTATIVE.replace(
    "vendedor consultivo B2B",
    "vendedor consultivo B2C (consumidor final)",
    1,
)

# Etapa AGENDAR: el prospecto ya quiere hablar — objetivo = fecha/hora, no re-pitch.
VOICE_INBOUND_SCHEDULE = """
Sos un SDR experimentado B2B (español): cálido, directo, humano.

CONTEXTO: el prospecto YA mostró interés y pidió llamada, reunión o demo.
ETAPA COMERCIAL: AGENDAR — no estás en prospección ni en primer contacto.

OBJETIVO ÚNICO: conseguir día y franja concreta (o confirmar un horario ya ofrecido).

ESTRUCTURA (3–4 líneas cortas):
1. Agradecé la respuesta con naturalidad.
2. Confirmá que coordinás la llamada/reunión/demo.
3. Pedí día + franja (ej. jueves a la tarde) o confirmá si eligió un horario ya listado.
   Si el contexto trae horarios libres reales, usá SOLO esos — nunca inventes horas.

PROHIBIDO:
- Inventar horarios o fingir disponibilidad de calendario.
- Pitch de prospección, explicar el producto desde cero, listar funcionalidades.
- Copiar la propuesta de valor completa ni párrafos largos de marketing.
- Ignorar que pidió reunión para volver a vender por email.

Si preguntó "cómo funciona" o "entender mejor" JUNTO con pedir llamada:
- UNA frase breve: "con gusto te lo mostramos en la llamada" — y pasá a coordinar horario.
- NO escribas 5+ líneas explicando el producto.

Texto plano; sin markdown.
"""

VOICE_INBOUND_SCHEDULE_B2C = VOICE_INBOUND_SCHEDULE.replace(
    "SDR experimentado B2B",
    "vendedor experimentado B2C",
    1,
)


def _campaign_is_b2c(campaign: dict | None) -> bool:
    if not campaign:
        return False
    return str(campaign.get("outreach_mode") or "").strip().lower() == "b2c"


def resolve_voice_global(campaign: dict | None = None) -> str:
    from app.services.outreach_language import language_from_campaign_dict, language_prompt_block

    base = VOICE_GLOBAL_B2C if _campaign_is_b2c(campaign) else VOICE_GLOBAL
    camp = campaign if isinstance(campaign, dict) else {}
    return base + language_prompt_block(
        language_from_campaign_dict(campaign),
        country=camp.get("seller_country") or camp.get("style_country"),
    )


def resolve_voice_inbound_consultative(campaign: dict | None = None) -> str:
    from app.services.outreach_language import language_from_campaign_dict, language_prompt_block

    base = VOICE_INBOUND_CONSULTATIVE_B2C if _campaign_is_b2c(campaign) else VOICE_INBOUND_CONSULTATIVE
    camp = campaign if isinstance(campaign, dict) else {}
    return base + language_prompt_block(
        language_from_campaign_dict(campaign),
        country=camp.get("seller_country") or camp.get("style_country"),
    )


def resolve_voice_inbound_schedule(campaign: dict | None = None) -> str:
    from app.services.outreach_language import language_from_campaign_dict, language_prompt_block

    base = VOICE_INBOUND_SCHEDULE_B2C if _campaign_is_b2c(campaign) else VOICE_INBOUND_SCHEDULE
    camp = campaign if isinstance(campaign, dict) else {}
    return base + language_prompt_block(
        language_from_campaign_dict(campaign),
        country=camp.get("seller_country") or camp.get("style_country"),
    )


VOICE_LINKEDIN_INBOUND_DM = """
Sos SDR en un DM de LinkedIn (español, según el país de Mi estilo): humano, directo, conversacional.

CONTEXTO CRÍTICO: el prospecto YA respondió. Esto NO es cold open ni primer contacto.

OBJETIVO: réplica corta al mensaje concreto. Sin plantillas genéricas.

REGLAS ESTRICTAS:
- Máximo 70 palabras (~2-4 líneas). LinkedIn exige brevedad.
- Terminá la idea: prohibido cortar con "…" / puntos suspensivos a mitad de frase.
- Si explicás el producto, NO cortes listas ni cláusulas a medias (cualquier vertical).
- Leé TODO el mensaje y el hilo. Contestá la intención real.
- «¿Integran / conectan con X?» = pregunta de INTEGRACIÓN. Contestá eso.
  PROHIBIDO pivotear a «Mirá la diferencia» / vs / contraste salvo que pidan comparar
  o digan que ya usan esa tool.
- USO DEL PRODUCTO: explicá beneficios / % / value prop SOLO si el prospecto preguntó
  (qué hace / cómo / precio / diferencia / integración / feature). Si no preguntó: ack + CTA,
  PROHIBIDO soltar pitch. Si preguntó y el dato está en ficha: usalo. Si no está: call, sin inventar.
- VARIEDAD: si hablás de la ficha, la redacción puede variar un poco a veces (no obligatorio
  cambiar de ángulo cada vez). Evitá que suene calcado siempre.
- Si solo muestra interés (ok, dale, me interesa): CTA simple tipo
  "¿Te parece agendar una reunión breve de 15 min?" — sin pitch de producto.
- El pitch de producto es SOLO para mensajes outbound iniciales, nunca para réplicas.
- Prohibido presentarte de nuevo ("mi nombre es", "te hablo desde").
- Cierre opcional: CTA suave a reunión solo si encaja — una frase.
- Texto plano; sin subject; sin markdown; SIN firma de mail
  (nada de "Saludos," / "Cordialmente" / nombre al final — esto es DM, no email).
- SALUDO: solo en la primera réplica del hilo: "Hola [nombre]," y después
  la respuesta directa (sin latiguillo).
- En mensajes siguientes: PROHIBIDO Hola / Buen día / Hey.
- PROHIBIDO latiguillos de relleno en cualquier réplica: "Buena pregunta",
  "Perfecto", "Genial", "Listo", "Dale", "Tenés razón", "Claro", "Entendido"
  como apertura vacía. Arrancá con la respuesta misma.
"""

_INBOUND_EVASIVE_REPLY = re.compile(
    r"(?:"
    r"te\s+(?:lo\s+)?(?:cuento|explico|muestro|paso|comento|digo)\s+(?:todo\s+)?(?:en\s+la\s+)?(?:reuni[oó]n|llamada|demo)"
    r"|lo\s+vemos\s+(?:mejor\s+)?(?:en\s+la\s+)?(?:reuni[oó]n|llamada|demo)"
    r"|mejor\s+(?:lo\s+)?(?:vemos|hablamos)\s+en\s+la\s+reuni[oó]n"
    r"|sin\s+spoilear.*?(?:reuni[oó]n|llamada)"
    r"|en\s+la\s+reuni[oó]n\s+te\s+(?:cuento|explico|muestro)"
    r")",
    re.IGNORECASE,
)

_SUBSTANTIVE_QUESTION_HINTS = (
    "qué hace",
    "que hace",
    "cómo funciona",
    "como funciona",
    "cómo es",
    "como es",
    "precio",
    "costo",
    "integrac",
    "automatiz",
    "roi",
    "implement",
    "seguridad",
    "ejemplo",
    "caso de uso",
    "casos de uso",
    "plataforma",
    "herramienta",
    "producto",
    "cuéntame",
    "cuentame",
    "introduc",
    "antes de la reunión",
    "antes de la reunion",
    "más sobre",
    "mas sobre",
    "qué es",
    "que es",
    "para qué sirve",
    "para que sirve",
)


def inbound_text_needs_substantive_answer(text: str | None) -> bool:
    """Heurística: el prospecto pide explicación concreta (no solo 'ok' o timing)."""
    t = (text or "").strip().lower()
    if len(t) < 8:
        return False
    if "?" in t:
        return True
    return any(h in t for h in _SUBSTANTIVE_QUESTION_HINTS)


def _load_openai():
    try:
        from openai import APIError, AuthenticationError, OpenAI, RateLimitError

        return OpenAI, APIError, AuthenticationError, RateLimitError
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail="SDK de OpenAI no instalado. Ejecutá `pip install -r requirements.txt`.",
        ) from exc


def _client():
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY no configurada. Definila para habilitar mensajes IA.",
        )
    OpenAI, _, _, _ = _load_openai()
    # Timeout holgado: mensajes SDR necesitan espacio para razonar + JSON completo.
    timeout_sec = float(os.getenv("OPENAI_TIMEOUT_SEC", "90") or "90")
    return OpenAI(api_key=key, timeout=timeout_sec)


@dataclass
class RawChatResult:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    retry_attempts: int = 1
    fallback: bool = False
    fallback_label: str | None = None


@dataclass
class OpenAIRequestError(Exception):
    """Fallo definitivo de OpenAI tras reintentos automáticos."""

    message: str
    status_code: int
    model: str
    error_type: str
    retryable: bool
    attempts: int
    last_error: str
    timestamp: str

    def to_http_detail(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "summary": self.message,
            "retryable": self.retryable,
            "openai": {
                "model": self.model,
                "error_type": self.error_type,
                "error": self.last_error,
                "attempts": self.attempts,
                "timestamp": self.timestamp,
                "retryable": self.retryable,
            },
        }


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_insufficient_quota(exc: Exception) -> bool:
    """Sin saldo en OpenAI: reintentar no sirve."""
    if getattr(exc, "code", None) == "insufficient_quota":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if err.get("code") == "insufficient_quota" or err.get("type") == "insufficient_quota":
            return True
    return "insufficient_quota" in str(exc)


def _is_rate_limit_api_error(exc: Exception) -> bool:
    _, APIError, _, RateLimitError = _load_openai()
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, APIError):
        code = getattr(exc, "status_code", None)
        if code == 429:
            return True
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error") or {}
            if isinstance(err, dict) and err.get("type") == "rate_limit_exceeded":
                return True
    return False


def log_openai_failure(
    *,
    model: str,
    error_type: str,
    error: str,
    attempts: int,
    retryable: bool,
) -> None:
    ts = _utc_now_iso()
    logger.error(
        "openai_request_failed model=%s error_type=%s attempts=%s retryable=%s timestamp=%s error=%s",
        model,
        error_type,
        attempts,
        retryable,
        ts,
        error[:500],
    )


def openai_http_exception_from_error(err: OpenAIRequestError) -> HTTPException:
    return HTTPException(status_code=err.status_code, detail=err.to_http_detail())


def is_retryable_openai_http_detail(detail: Any) -> bool:
    if isinstance(detail, dict):
        if detail.get("retryable"):
            return True
        openai = detail.get("openai")
        if isinstance(openai, dict) and openai.get("retryable"):
            return True
    return False


def _openai_error_from_exception(exc: Exception, *, error_type: str, status_code: int, retryable: bool) -> OpenAIRequestError:
    from app.services import openai_diagnostics as od

    details = od.extract_error_details(exc)
    od.record_error(
        endpoint=OPENAI_ENDPOINT,
        model=MODEL,
        error_type=error_type,
        status_code=details.get("status_code") or status_code,
        error_full=details.get("error_full") or str(exc),
        error_body=details.get("error_body"),
        rate_limit_headers=details.get("rate_limit_headers"),
    )
    return OpenAIRequestError(
        message="OpenAI rate limit excedido. Reintentá en unos segundos."
        if error_type == "rate_limit"
        else (
            "OpenAI API key inválida o sin permisos."
            if error_type == "authentication"
            else f"Error al generar contenido con OpenAI: {exc}"
        ),
        status_code=status_code,
        model=MODEL,
        error_type=error_type,
        retryable=retryable,
        attempts=1,
        last_error=details.get("error_full") or str(exc),
        timestamp=_utc_now_iso(),
    )


def _single_openai_chat(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    model: str | None = None,
) -> RawChatResult:
    from app.services import openai_diagnostics as od

    use_model = (model or MODEL).strip() or MODEL
    _, APIError, AuthenticationError, RateLimitError = _load_openai()
    try:
        res = _client().responses.create(
            model=use_model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        usage = getattr(res, "usage", None)
        od.record_request(endpoint=OPENAI_ENDPOINT, model=use_model, success=True)
        try:
            from app.services.lead_sourcing.cogs_runtime_metrics import record_openai

            record_openai(
                input_tokens=getattr(usage, "input_tokens", None) if usage else None,
                output_tokens=getattr(usage, "output_tokens", None) if usage else None,
                total_tokens=getattr(usage, "total_tokens", None) if usage else None,
            )
        except Exception:  # noqa: BLE001
            pass
        return RawChatResult(
            text=(res.output_text or "").strip(),
            model=use_model,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            total_tokens=getattr(usage, "total_tokens", None) if usage else None,
            retry_attempts=1,
        )
    except RateLimitError as exc:
        od.record_request(endpoint=OPENAI_ENDPOINT, model=use_model, success=False)
        if _is_insufficient_quota(exc):
            raise _openai_error_from_exception(
                exc, error_type="insufficient_quota", status_code=429, retryable=False
            ) from exc
        raise _openai_error_from_exception(
            exc, error_type="rate_limit", status_code=429, retryable=True
        ) from exc
    except AuthenticationError as exc:
        od.record_request(endpoint=OPENAI_ENDPOINT, model=use_model, success=False)
        raise _openai_error_from_exception(
            exc, error_type="authentication", status_code=503, retryable=False
        ) from exc
    except APIError as exc:
        od.record_request(endpoint=OPENAI_ENDPOINT, model=use_model, success=False)
        if _is_rate_limit_api_error(exc) and not _is_insufficient_quota(exc):
            raise _openai_error_from_exception(
                exc, error_type="rate_limit", status_code=429, retryable=True
            ) from exc
        raise _openai_error_from_exception(
            exc, error_type="api_error", status_code=502, retryable=False
        ) from exc


def extract_text_from_image_bytes(
    data: bytes,
    *,
    content_type: str | None = None,
    filename: str = "",
) -> str:
    """OCR de capturas (WhatsApp, mail, LinkedIn) para ejemplos de Mi estilo."""
    if not data:
        raise ValueError("Imagen vacía.")
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("La imagen supera 8 MB.")
    if not openai_configured():
        raise ValueError(
            "OpenAI no configurado — no se puede leer texto de imágenes."
        )
    import base64

    mime = (content_type or "").split(";")[0].strip().lower() or "image/jpeg"
    if mime not in {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif"}:
        mime = "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    prompt = (
        "Transcribí el texto visible en esta imagen (captura de chat, email o mensaje). "
        "Devolvé solo el contenido legible, en el mismo idioma, sin comentarios ni markdown."
    )
    _, APIError, AuthenticationError, RateLimitError = _load_openai()
    try:
        res = _client().responses.create(
            model=MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime};base64,{b64}",
                        },
                    ],
                }
            ],
            temperature=0.1,
            max_output_tokens=2500,
        )
    except (RateLimitError, AuthenticationError, APIError) as exc:
        raise _openai_error_from_exception(
            exc, error_type="api_error", status_code=502, retryable=False
        ) from exc
    text = (res.output_text or "").strip()
    if len(text) < 20:
        raise ValueError(
            "No se leyó texto suficiente en la imagen. Probá una captura más nítida o pegá el texto."
        )
    return text


def _raw_chat_with_meta(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    fallback_factory: Any | None = None,
    model: str | None = None,
) -> RawChatResult:
    from app.services import openai_diagnostics as od
    from app.services.openai_fallback import FALLBACK_MARKER, is_openai_fallback_enabled

    last_err: OpenAIRequestError | None = None
    max_attempts = OPENAI_RATE_LIMIT_MAX_ATTEMPTS
    delays = OPENAI_RATE_LIMIT_RETRY_DELAYS_SEC

    for attempt in range(1, max_attempts + 1):
        try:
            result = _single_openai_chat(
                system_prompt,
                user_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                model=model,
            )
            result.retry_attempts = attempt
            if attempt > 1:
                logger.info(
                    "openai_rate_limit_recovered model=%s attempts=%s",
                    model or MODEL,
                    attempt,
                )
            return result
        except OpenAIRequestError as exc:
            last_err = exc
            if not exc.retryable or attempt >= max_attempts:
                break
            delay = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
            logger.warning(
                "openai_rate_limit_retry attempt=%s/%s delay_sec=%s model=%s error=%s",
                attempt,
                max_attempts,
                delay,
                model or MODEL,
                exc.last_error[:240],
            )
            time.sleep(delay)

    assert last_err is not None
    if is_openai_fallback_enabled() and last_err.error_type == "rate_limit":
        od.record_fallback()
        if callable(fallback_factory):
            text = str(fallback_factory())
        else:
            from app.services.openai_fallback import build_generic_fallback_text

            text = build_generic_fallback_text(system_prompt=system_prompt, user_prompt=user_prompt)
        logger.warning(
            "openai_fallback_test model=%s endpoint=%s attempts=%s",
            MODEL,
            OPENAI_ENDPOINT,
            max_attempts,
        )
        od.record_error(
            endpoint=OPENAI_ENDPOINT,
            model=MODEL,
            error_type="rate_limit_fallback",
            status_code=429,
            error_full=last_err.last_error,
            attempts=max_attempts,
        )
        return RawChatResult(
            text=text,
            model=MODEL,
            retry_attempts=max_attempts,
            fallback=True,
            fallback_label=FALLBACK_MARKER,
        )

    final = OpenAIRequestError(
        message=last_err.message,
        status_code=last_err.status_code,
        model=last_err.model,
        error_type=last_err.error_type,
        retryable=last_err.retryable,
        attempts=max_attempts,
        last_error=last_err.last_error,
        timestamp=_utc_now_iso(),
    )
    od.record_error(
        endpoint=OPENAI_ENDPOINT,
        model=MODEL,
        error_type=final.error_type,
        status_code=final.status_code,
        error_full=final.last_error,
        attempts=final.attempts,
    )
    log_openai_failure(
        model=final.model,
        error_type=final.error_type,
        error=final.last_error,
        attempts=final.attempts,
        retryable=final.retryable,
    )
    detail = final.to_http_detail()
    detail["openai"]["endpoint"] = OPENAI_ENDPOINT
    detail["openai"]["requests_per_minute"] = od.requests_per_minute()
    detail["openai"]["diagnostics_url"] = "/health/openai"
    raise HTTPException(status_code=final.status_code, detail=detail)


def _raw_chat(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    model: str | None = None,
) -> str:
    return _raw_chat_with_meta(
        system_prompt,
        user_prompt,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        model=model,
    ).text


def _maybe_scrub_followup_automation(text: str, system_prompt: str) -> str:
    if not _FOLLOWUP_AUTOMATION_BANNED.search(text):
        return text
    fix_user = (
        "Reescribí el mensaje en el MISMO idioma: seguís la intención de un touch liviano, "
        "pero sin frases de 'seguimiento automático', sin 'retomo', sin 'solo quería'. "
        "Soná humano, directo. Sin markdown.\n\n"
        f"MENSAJE:\n{text}"
    )
    return _raw_chat(
        system_prompt + "\n\nAnti-plantilla de follow-up genérico.",
        fix_user,
        temperature=0.92,
        max_output_tokens=160,
    ) or text


def _maybe_scrub_inbound_evasive(body: str, system_prompt: str, *, inbound_snippet: str) -> str:
    """Reescribe si el modelo evadió preguntas con 'te lo cuento en la reunión'."""
    if not inbound_text_needs_substantive_answer(inbound_snippet):
        return body
    if not _INBOUND_EVASIVE_REPLY.search(body):
        return body
    # Precio sin datos en contexto: diferir a la call NO es evasión — no forzar invento.
    from app.services.linkedin_reply_compose import price_asked

    if price_asked(inbound_snippet) and "PRECIO_CONFIRMADO_EN_CONTEXTO=1" not in (system_prompt or ""):
        return body
    fix_user = (
        "El borrador EVADE la pregunta del prospecto. Reescribí el CUERPO del email en español.\n"
        "OBLIGATORIO: respondé primero la pregunta con hechos del contexto de producto/campaña. "
        "Si el dato NO está en el contexto, NO lo inventes: reconocé el límite y proponé "
        "cerrarlo en una call corta. Recién al final, UNA frase suave opcional.\n"
        "Mantené el mismo tono. Texto plano, sin markdown.\n\n"
        f"PREGUNTA/COMENTARIO DEL PROSPECTO:\n{inbound_snippet[:1200]}\n\n"
        f"BORRADOR A CORREGIR:\n{body}"
    )
    fixed = _raw_chat(
        system_prompt + "\n\nCorrección anti-evasión consultiva.",
        fix_user,
        temperature=0.55,
        max_output_tokens=480,
    )
    return (fixed or body).strip() or body


def _maybe_scrub_repeat(text: str, system_prompt: str) -> str:
    """Una segunda pasada suave solo si cayó demasiado en patrones marcados."""
    if not (_BANNED_SNIPPETS.search(text) or _SOFT_BANNED.search(text)):
        return text
    fix_user = (
        "Reescribí el siguiente mensaje de outreach en el MISMO idioma manteniendo intención, "
        "pero usando palabras distintas. Evitá: optimizar, gestión, ayudar, equipo comercial, "
        "coordinar reunión. Sin markdown.\n\n"
        f"MENSAJE:\n{text}"
    )
    return _raw_chat(
        system_prompt + "\n\nReescritura forzada anti-cliché sin perder calidez.",
        fix_user,
        temperature=0.9,
        max_output_tokens=180,
    ) or text


def _chat(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_output_tokens: int,
    scrub_followup_cliche: bool = False,
    model: str | None = None,
) -> str:
    text = _raw_chat(
        system_prompt,
        user_prompt,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        model=model,
    )
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI devolvió una respuesta vacía.")
    text = _maybe_scrub_repeat(text, system_prompt)
    if scrub_followup_cliche:
        text = _maybe_scrub_followup_automation(text, system_prompt)
    return text


def classify_inbound_json_raw(
    *,
    inbound_text: str,
    conversation_digest: str,
    education: str,
    outreach_language: str | None = None,
) -> str:
    """Devuelve texto JSON (sin envoltorio)."""
    from app.services.outreach_language import classify_language_prompt_block

    system = (
        resolve_voice_global()
        + _education_block(education)
        + "\n\nRol: clasificador. Respondé SOLO JSON válido, sin markdown, sin comentarios."
        + classify_language_prompt_block(outreach_language)
        + "\n\nSi el prospecto pide volver en un mes/trimestre/fecha relativa, estimá defer_resume_at "
        "como fecha futura razonable en UTC (no uses fechas pasadas). "
        "Frases como 'hablame dentro de 2 dias', 'en 3 días', 'un par de días' / 'in 2 days' / 'next week' "
        "DEBEN llevar defer_resume_at coherente (p. ej. ~2 días desde ahora, mediodía o tarde UTC)."
    )
    user_prompt = (
        "Analizá el último mensaje INBOUND del prospecto.\n\n"
        "Claves JSON obligatorias:\n"
        '- "objection": uno de '
        '["none","no_time","competitor","not_interested","timing","not_priority","send_info","other"]\n'
        '- "interest": uno de ["low","medium","high"]\n'
        '- "wants_meeting": true/false — menciona reunión/llamada o curiosidad genérica por hablar.\n'
        '- "explicit_meeting_commitment": true/false — true SOLO si acepta explícitamente coordinar '
        "reunión/llamada/demo o pide el link de calendario/horarios para cerrar. "
        "true también si dice que quiere agendar YA (ej. 'agendémosla ahora', 'coordinemos la reunión', "
        "\"pasame el link\", \"cuándo podés\", \"let's schedule\", \"send me the calendar link\"). "
        "false si solo dice 'me interesa', 'contame más', 'cómo funciona', pide material o precio sin coordinar.\n"
        '- "asks_questions": true/false\n'
        '- "brushoff": true/false (cortante / cierre suave negativo)\n'
        '- "prospect_timing_hold": true/false — true si pide espacio, volver después, "ahora no", '
        '"más adelante", "escribime en X meses", "hablamos en julio", "not now", "next quarter", '
        "timing incómodo pero sin rechazo definitivo. "
        "false si en el mismo mensaje pide agendar o link de calendario con intención de reunión.\n"
        '- "defer_resume_at": string ISO8601 en UTC (ej. "2026-09-01T14:00:00+00:00") o null — '
        "solo si el mensaje permite inferir una fecha/ventana concreta de re-contacto; si no, null. "
        "Si dice N días / 'un par de días' / 'in a couple of days', reflejá N (o 2) en la fecha. "
        "Frases vagas ('escribime después', 'otro día', 'por ahora no', 'más adelante') → null: "
        "no inventes una fecha.\n\n"
        f"Contexto reciente (truncado):\n{conversation_digest}\n\n"
        f"MENSAJE:\n{inbound_text}"
    )
    return _raw_chat(system, user_prompt, temperature=0.15, max_output_tokens=360)


def _education_block(blob: str) -> str:
    from app.services.nexus_sales_playbook import sales_playbook_prompt_section

    parts = [sales_playbook_prompt_section()]
    if blob and blob.strip():
        parts.append(
            "FICHA / CONTEXTO DEL PRODUCTO (fuente de verdad para réplicas):\n"
            "Si el prospecto pregunta algo que ESTÁ escrito acá, RESPONDELO con esos hechos. "
            "PROHIBIDO decir solo «lo vemos en la call» cuando el dato sí está. "
            "Si el dato NO está, no inventes: ahí sí derivá a call.\n"
            f"{blob.strip()}"
        )
    return "\n\n" + "\n\n".join(parts)


def _thread_summary_block(prospect: dict | None) -> str:
    if not isinstance(prospect, dict):
        return ""
    from app.services.conversation_summary import conversation_summary_prompt_block

    block = conversation_summary_prompt_block(prospect.get("conversation_summary"))
    return f"\n{block}\n" if block else ""


def _conversation_digest(history: Sequence[dict[str, str]]) -> str:
    from app.services.conversation_history import speaker_line

    lines: list[str] = []
    for item in history[-18:]:
        if not isinstance(item, dict):
            continue
        if "direction" not in item and "role" in item:
            item = {
                "direction": "inbound" if item.get("role") == "prospect" else "outbound",
                "message": item.get("content") or "",
            }
        line = speaker_line(item)
        if line:
            lines.append(line)
    return "\n".join(lines) if lines else "(sin historial cargado)"


def _past_outbounds(history: Sequence[dict[str, str]]) -> str:
    chunks: list[str] = []
    for item in history:
        if item.get("direction") == "outbound":
            m = (item.get("message") or "").strip()
            if m:
                chunks.append(m)
    merged = "\n---\n".join(chunks[-6:])
    if len(merged) > 2600:
        merged = merged[-2600:]
    return merged if merged else "(primer mensaje: no repetir formulaciones triviales conocidas)"


def _follow_style_note() -> str:
    variants = (
        "Hacelo MUY corto (1 línea corta + 1 mini pregunta).",
        "Solo tirá una duda muy puntual, sin repetir ninguna frase del historial propio outbound.",
        "Soná medio off-hand, como chequeando si cayó bien el último mensaje.",
        "Puede ser incluso medio telegráfico, sin adornos corporativos.",
        "Dos frases cómodas, ninguna muy larga, sin sonar automatizado.",
    )
    return random.choice(variants)


def generate_outreach_message(
    *,
    prospect: dict,
    campaign: dict,
    product: dict,
    tone: str,
    education: str,
) -> str:
    system = resolve_voice_global(campaign) + _education_block(education)
    tone_use = (tone or campaign.get("tone") or "").strip()
    pname = prospect.get("name") or "(nombre)"
    pco = prospect.get("company_name") or ""
    prol = prospect.get("role") or ""
    pint = prospect.get("industry") or ""
    pcountry = prospect.get("country") or ""
    pname_first = pname.split()[0] if pname else "Hola"

    channel_hint = campaign.get("preferred_channel_hint", "")
    desc = (product.get("description") or "")[:300]
    user_prompt = f"""Generá UN solo primer mensaje outbound (simulamos envío, sin disclaimers).

Canal sugerido: {channel_hint}
Tono pedido por la campaña: {tone_use}
Campaña: {campaign.get("name")}

Prospecto (usá apenas lo que sirva para sonar específicos, sin checklist rígido):
- nombre: {pname}
- primer nombre plausible: {pname_first}
- empresa: {pco}
- rol: {prol}
- industria: {pint}
- país: {pcountry}

Producto (no hagas datasheet; mención muy ligera):
{product.get("name")}
Propuesta interna muy corta (no copy literal): {(product.get("value_proposition") or "")[:400]}
Opcional sólo léxico de contexto desde descripción corta truncada:
{desc}

Construcción sugerida:
1) Saludo corto con el primer nombre.
2) Una línea de contexto real (empresa/rol/industria) sin frase hecha tipo "vi tu perfil".
3) Una línea de valor o ángulo (sin datasheet).
4) Cierre: curiosidad + propuesta suave de charla breve en voz (no prometas "mandar resumen").

Formato: como máximo 3-4 líneas cortas en total. Nada de párrafos largos.

Este primer touch debe sentirse distinto persona a persona: variá léxico incluso ante datos parecidos.
"""
    temp = random.uniform(0.78, 0.93)
    return _chat(system, user_prompt, temperature=temp, max_output_tokens=112)


def _strip_json_fence(raw: str) -> str:
    t = (raw or "").strip()
    if not t.startswith("```"):
        return t
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _subject_from_product(product_name: str) -> str:
    """2–4 palabras a partir del producto; nunca un nombre de persona."""
    banned = {
        "pipeline",
        "crm",
        "saas",
        "hub",
    }
    name = re.sub(r"\s+", " ", (product_name or "").strip())
    if not name:
        return "Seguimiento"
    raw_tokens = [w for w in re.split(r"[\s|,;/]+", name) if w and len(w) > 1][:6]
    tokens = []
    for w in raw_tokens:
        if w.lower() in banned:
            continue
        tokens.append(w)
        if len(tokens) >= 3:
            break
    if not tokens:
        return "Seguimiento"
    s = " ".join(tokens[:3])
    if len(s) > 44:
        s = s[:41].rstrip(" ,.;:") + "…"
    return s


_SUBJECT_INTERNAL_PAT = re.compile(
    r"\b(pipeline|crm|saas|hub|plataforma|gestión\s+comercial|comercial)\b",
    re.I,
)


def _subject_is_prospect_name(s: str, *, first_name: str, full_name: str) -> bool:
    sub = s.strip().lower().rstrip("?!¿.,")
    if not sub:
        return True
    fn = (first_name or "").strip().lower()
    if fn and sub == fn:
        return True
    parts_full = (full_name or "").strip().split()
    fp = parts_full[0].lower() if parts_full else ""
    if fp and sub == fp:
        return True
    bits = sub.split()
    if len(bits) == 1 and fn and bits[0] == fn:
        return True
    return False


def _normalize_gmail_subject_human(
    raw: str,
    *,
    first_name: str,
    full_name: str,
    company: str,
    product_name: str,
) -> str:
    """Asuntos cortos (2–4 palabras típico): valor/producto/problema — nunca nombre del prospecto."""
    fallbacks = (
        _subject_from_product(product_name),
        "Automatización",
        "Seguimiento",
        "Operaciones",
        "Consulta",
        "Ventas",
        "Una idea",
    )
    s = re.sub(r"\s+", " ", (raw or "").strip())
    s = s.rstrip("?¿ ").strip()
    low = s.lower()
    bad_internal = bool(_SUBJECT_INTERNAL_PAT.search(low))
    bad_meeting = any(
        x in low
        for x in (
            "podemos charlar",
            "10 minutos",
            "15 minutos",
            "coordinamos",
            "reunión",
            "reunion",
            "agendar",
            "tenés tiempo",
            "tienes tiempo",
        )
    )
    if (
        len(s) > 52
        or bad_meeting
        or bad_internal
        or _subject_is_prospect_name(s, first_name=first_name, full_name=full_name)
    ):
        for cand in fallbacks:
            if cand and len(cand) <= 44 and not _subject_is_prospect_name(
                cand, first_name=first_name, full_name=full_name
            ):
                s = cand
                break
        else:
            s = "Seguimiento"
    parts = s.split()
    if len(parts) > 5:
        s = " ".join(parts[:4])
    if _subject_is_prospect_name(s, first_name=first_name, full_name=full_name):
        s = _subject_from_product(product_name)
    if len(s) > 44:
        s = s[:41].rstrip(" ,.;:") + "…"
    return s or "Seguimiento"


def _inbound_body_line_hint(policy: AiBehaviorPolicy, *, substantive: bool) -> str:
    fmt = (
        "línea en blanco solo al cambiar de tema "
        "(saludo / respuesta / cierre); varias oraciones del mismo tema juntas"
    )
    if substantive:
        if policy.response_length == "detailed":
            return f"respuesta útil pero breve (máx ~90–110 palabras); {fmt}"
        if policy.response_length == "short":
            return f"1 bloque de respuesta + cierre corto; {fmt}"
        return f"respuesta concreta y breve (máx ~60–80 palabras); {fmt}"
    if policy.response_length == "detailed":
        return f"breve y útil; {fmt}"
    if policy.response_length == "short":
        return f"muy corto; {fmt}"
    return f"corto y claro; {fmt}"


def generate_gmail_draft_email(
    *,
    prospect: dict,
    campaign: dict,
    product: dict,
    tone: str,
    education: str,
    conversation_history: Sequence[dict[str, str]],
    last_prospect_inbound: str | None = None,
    prospect_timing_soft: bool = False,
    prospect_booking_priority: bool = False,
    prospect_substantive_questions: bool = False,
    ai_policy: AiBehaviorPolicy | None = None,
    interest_level: str | None = None,
    prospect_wants_meeting: bool = False,
    explicit_meeting_commitment: bool = False,
    reply_objective: str | None = None,
    response_class: str | None = None,
    meeting_already_booked: bool = False,
) -> tuple[str, str]:
    """
    Genera asunto + cuerpo para un borrador Gmail (no enviado).
    Con postergación/timing: responde al hilo sin empujar reunión.
    Con booking_priority: empuja link de calendario y prohíbe "te escribo después".
    Respuestas a inbound con preguntas: modo consultivo (valor primero, reunión después).
    """
    from app.services.conversation_intelligence import (
        build_professional_closure_reply,
        resolve_closure_kind,
    )

    policy = ai_policy or DEFAULT_POLICY
    closure_kind = resolve_closure_kind(
        text=inbound_raw if (inbound_raw := (last_prospect_inbound or "").strip()) else "",
        response_class=response_class,
        reply_objective=reply_objective,
    )
    if closure_kind:
        body = build_professional_closure_reply(
            prospect_name=prospect.get("name"),
            closure_kind=closure_kind,
            inbound_text=last_prospect_inbound or "",
        )
        return "Gracias por responder", body

    behavior_block = behavior_prompt_section(policy)
    tone_use = (tone or campaign.get("tone") or "").strip()
    hist = _conversation_digest(conversation_history)
    hist_has_content = any(
        (h.get("message") or "").strip()
        for h in conversation_history
        if isinstance(h, dict)
    )
    inbound_raw = (last_prospect_inbound or "").strip()
    substantive = prospect_substantive_questions or inbound_text_needs_substantive_answer(inbound_raw)
    # Precio sin ficha: respuesta determinística (no dejar que el LLM invente “por usuario”).
    from app.services.linkedin_reply_compose import (
        build_price_unknown_email_reply,
        inbound_insists_price_before_meeting,
        looks_like_invented_pricing,
        price_asked,
        product_has_pricing_facts,
    )

    pricing_known = product_has_pricing_facts(
        education,
        product.get("value_proposition") if isinstance(product, dict) else None,
        product.get("description") if isinstance(product, dict) else None,
        product.get("target_notes") if isinstance(product, dict) else None,
        product.get("notes") if isinstance(product, dict) else None,
    )
    if (
        inbound_raw
        and price_asked(inbound_raw)
        and not pricing_known
        and not prospect_timing_soft
        and not meeting_already_booked
    ):
        team_hint = None
        m_team = re.search(
            r"(\d+)\s*[-–]?\s*(\d+)?\s*sdrs?",
            inbound_raw,
            re.I,
        )
        if m_team:
            a, b = m_team.group(1), m_team.group(2)
            team_hint = f"{a}–{b} SDRs" if b else f"{a} SDRs"
        body = build_price_unknown_email_reply(
            insist=inbound_insists_price_before_meeting(inbound_raw),
            team_hint=team_hint,
        )
        from app.services.outbound_text_normalize import normalize_outbound_email_body

        return "Pricing", normalize_outbound_email_body(body)

    is_cold_first = (
        not inbound_raw
        and not hist_has_content
        and not prospect_timing_soft
        and not prospect_booking_priority
    )
    inbound_block = ""
    if inbound_raw:
        answer_rule = (
            "PRIORIDAD ABSOLUTA: contestá cada pregunta o tema del mensaje con información concreta "
            "del producto/contexto ANTES de cualquier invitación a reunión. "
            "Si falta el dato en contexto, NO inventes: reconocé el límite y ofrecé cerrarlo en call.\n"
            if substantive
            else "Respondé lo que dijo de forma directa y útil antes de empujar agenda.\n"
        )
        inbound_block = (
            "\n\nÚLTIMO MENSAJE DEL PROSPECTO (leé TODO el texto; no te quedes con la 1ª palabra "
            "ni con un Dale/Ok/Perfecto inicial; respondé al mensaje entero de forma coherente; "
            "no ignores el hilo ni actúes como primer cold outreach):\n"
            f"{inbound_raw}\n\n"
            "CONVERSACIÓN ACTIVA: ya hay intercambio previo. Respondé como en un chat en curso "
            "(no repitas pitch inicial; sí contestá lo nuevo).\n\n"
            f"{answer_rule}"
        )

    if is_cold_first:
        system = (
            resolve_voice_global(campaign)
            + behavior_block
            + _education_block(education)
            + "\n\nRol: PRIMER correo frío B2B en español (sin hilo previo). "
            "Filosofía Solución + Intriga:\n"
            "- Línea 1: gancho con empresa/rol del prospecto.\n"
            "- Líneas 2-3: qué logra tu solución (superpoder, no features) para empresas como la suya.\n"
            "- Cierre: UNA pregunta de interés ('¿Te interesaría ver cómo lo implementamos para ustedes?').\n\n"
            "PROHIBIDO: brochure, 'consolidar prospectos/campañas/reportes', link de calendario, "
            "preguntar cómo llevan X hoy, párrafos largos.\n\n"
            "Respondé SOLO con JSON válido (sin markdown):\n"
            '{"subject":"...","body":"..."}\n'
            "- subject: 2–4 palabras (valor/problema), sin nombre del prospecto.\n"
            "- body: máximo 4 líneas cortas + pregunta final.\n"
        )
        max_tokens = 380
        temp = 0.58
    elif meeting_already_booked and not prospect_booking_priority:
        system = (
            resolve_voice_global(campaign)
            + behavior_block
            + _education_block(education)
            + (
                "\n\nRol: el prospecto hizo una pregunta concreta. La reunión (día, hora, invitación) "
                "ya se resuelve aparte en este mismo mail: no menciones fechas ni horarios.\n"
                "Contestá la pregunta con información real del producto/contexto "
                "en 2-3 líneas (si no tenés el dato exacto, decí que lo ven en la llamada).\n"
                "PROHIBIDO: pitch, listar features, pedir o proponer otra reunión, ofrecer horarios, "
                "link de calendario, volver a confirmar la reunión.\n\n"
                if substantive
                else "\n\nRol: el prospecto YA tiene reunión agendada. Modo silencio comercial.\n"
                "Solo: confirmar logística, recordatorio breve o responder una duda puntual en 1-2 líneas.\n\n"
                "PROHIBIDO ABSOLUTO: explicar producto, listar features, propuesta de valor, "
                "link genérico de calendario, CTA de venta, 'Plataforma Nexus integra…'.\n\n"
            )
            + "Respondé SOLO con JSON válido:\n"
            '{"subject":"...","body":"..."}\n'
            "- body: máximo 4 líneas.\n"
        )
        max_tokens = 280
        temp = 0.45
    elif prospect_timing_soft:
        system = (
            resolve_voice_global(campaign)
            + behavior_block
            + _education_block(education)
            + "\n\nRol: redactás UN correo en español (B2B) como respuesta en un hilo existente. "
            "El prospecto pidió espacio, timing o volver más adelante (postergación blanda). "
            "Objetivo: reconocer su pedido, cerrar con calidez y confirmar que vas a respetar el timing. "
            "PROHIBIDO: pedir reunión esta semana, '¿tenés 15 minutos?', '¿coordinamos?', link de calendario, "
            "o cualquier CTA agresivo hacia agenda. Máximo 3–4 líneas cortas en el cuerpo.\n\n"
            "Respondé SOLO con JSON válido (sin markdown) con claves exactas:\n"
            '{"subject":"...","body":"..."}\n'
            "- subject: 2–4 palabras en español, relacionadas con valor/producto/problema/solución "
            "(ej. Automatización comercial, Pipeline, Seguimiento comercial, Operaciones, Costos). "
            "PROHIBIDO usar nombres propios del prospecto, empresa como única palabra si suena a persona, "
            "preguntas largas o frases de reunión en el asunto.\n"
            "- body: saludo con primer nombre; tono humano (podés un solo emoji tipo 👍 si encaja); "
            "confirmá que le vas a escribir cuando acordaron / en unos días; sin listas ni markdown.\n"
        )
        max_tokens = 420
        temp = 0.55
    elif prospect_booking_priority:
        booking_body_rules = (
            "- body: saludo con primer nombre; tono humano.\n"
            "  Máximo 3 líneas: celebrá el avance hacia reunión + link de agenda O pregunta día/hora.\n"
            "  PROHIBIDO explicar producto, features o propuesta de valor.\n"
        )
        system = (
            resolve_voice_inbound_schedule(campaign)
            + behavior_block
            + _education_block(education)
            + "\n\nRol: el prospecto quiere coordinar reunión. ETAPA AGENDAR.\n"
            "OBJETIVO ÚNICO: día/hora o link de agenda. Cero pitch.\n\n"
            "Respondé SOLO con JSON válido (sin markdown) con claves exactas:\n"
            '{"subject":"...","body":"..."}\n'
            "- subject: 2–4 palabras (reunión/agenda); sin nombre del prospecto.\n"
            f"{booking_body_rules}"
        )
        max_tokens = 320
        temp = 0.5
    else:
        body_lines = _inbound_body_line_hint(policy, substantive=substantive)
        system = (
            resolve_voice_inbound_consultative(campaign)
            + behavior_block
            + _education_block(education)
            + "\n\nRol: redactás UN correo en español (B2B) como respuesta en un hilo existente (borrador Gmail). "
            "Always answer the user's actual question first before pushing for a meeting. "
            "Meeting advancement is secondary to being genuinely useful. "
            "Mantené la conversación: cada inbound recibe respuesta; nunca dejar el hilo sin contestar.\n\n"
            "Respondé SOLO con JSON válido (sin markdown) con claves exactas:\n"
            '{"subject":"...","body":"..."}\n'
            "- subject: 2–4 palabras (valor, producto, tema); sin nombre del prospecto ni preguntas largas.\n"
            f"- body: saludo con primer nombre; {body_lines}; "
            "OBLIGATORIO: línea en blanco entre párrafos (un tema por párrafo); "
            "usá datos de producto/campaña; referencia empresa/rol si aplica; "
            "cierre opcional SUAVE (una frase) hacia charla o reunión solo al final si encaja; "
            "sin presión ni repetir CTA de agenda; PROHIBIDO un solo bloque sin saltos.\n"
        )
        max_tokens = 580 if substantive else 460
        temp = 0.62

    if _campaign_is_b2c(campaign):
        icp_block = (
            f"Modo: B2C\n"
            f"ICP campaña — región: {campaign.get('target_country') or '—'}\n"
            f"ICP campaña — idioma: {campaign.get('target_language') or '—'}\n"
            f"ICP campaña — perfil: {campaign.get('target_role') or '—'}\n"
            f"ICP campaña — intereses: {campaign.get('target_interests') or '—'}\n"
        )
    else:
        icp_block = (
            f"Modo: B2B\n"
            f"ICP campaña — tamaño empresa: {campaign.get('target_company_size') or '—'}\n"
            f"ICP campaña — industria: {campaign.get('target_industry') or '—'}\n"
            f"ICP campaña — país: {campaign.get('target_country') or '—'}\n"
            f"ICP campaña — idioma: {campaign.get('target_language') or '—'}\n"
            f"ICP campaña — rol objetivo: {campaign.get('target_role') or '—'}\n"
        )
    icp_ai = (campaign.get("icp_ai_digest") or "").strip()
    if icp_ai:
        icp_block += f"Resumen / notas ICP (IA, truncado):\n{icp_ai[:1800]}\n"

    pname = prospect.get("name") or ""
    pname_first = pname.split()[0] if pname else "Hola"
    cal = (campaign.get("calendar_link") or "").strip()
    inject_cal, cal_mandatory = should_inject_calendar_link(
        policy,
        calendar_url=cal,
        inbound_text=inbound_raw,
        timing_soft=prospect_timing_soft,
        booking_priority=prospect_booking_priority,
        interest_level=interest_level,
        prospect_wants_meeting=prospect_wants_meeting,
        explicit_meeting_commitment=explicit_meeting_commitment,
        substantive_questions=substantive,
    )
    if inject_cal and cal_mandatory:
        cal_line = (
            f"LINK DE AGENDA (OBLIGATORIO en el cuerpo del mail, tal cual, en una sola línea o frase corta): {cal}\n\n"
        )
    elif inject_cal:
        cal_line = (
            f"Link agenda (opcional: UNA frase corta al final si ya respondiste con valor): {cal}\n\n"
        )
    elif cal and inbound_raw and not prospect_timing_soft:
        cal_line = (
            "PROHIBIDO incluir link de calendario, horarios o CTA de reunión en este mensaje. "
            "Respondé primero con valor; la agenda queda para cuando el prospecto lo pida.\n\n"
        )
    else:
        cal_line = ""
    user_prompt = (
        f"Campaña: {campaign.get('name')}\n"
        f"Tono pedido: {tone_use}\n"
        f"Canales permitidos (contexto): {campaign.get('preferred_channel_hint', '')}\n"
        f"{cal_line}"
        f"{icp_block}\n"
        "Producto (mención mínima, sin datasheet):\n"
        f"- nombre: {product.get('name')}\n"
        f"- propuesta de valor (no copiar literal): {(product.get('value_proposition') or '')[:420]}\n"
        f"- contexto léxico opcional: {(product.get('description') or '')[:320]}\n\n"
        "Prospecto destinatario:\n"
        f"- nombre completo: {pname}\n"
        f"- primer nombre: {pname_first}\n"
        f"- empresa: {prospect.get('company_name') or '—'}\n"
        f"- rol: {prospect.get('role') or '—'}\n"
        f"- industria: {prospect.get('industry') or '—'}\n"
        f"- país: {prospect.get('country') or '—'}\n"
        f"- email (no lo cites en el cuerpo): {prospect.get('email') or '—'}\n\n"
        f"{_mvp_prospect_context_block(prospect)}"
        + _thread_summary_block(prospect)
        + "Historial previo en Nexus (respetá el hilo; no repitas el mismo pitch ni el mismo CTA textual):\n"
        f"{hist}"
        f"{inbound_block}"
    )
    raw = _raw_chat(system, user_prompt, temperature=temp, max_output_tokens=max_tokens)
    text = _strip_json_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail="OpenAI no devolvió JSON válido para el borrador Gmail. Reintentá en unos segundos.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Respuesta OpenAI inválida para el borrador (no es objeto).")
    subject = str(data.get("subject") or "").strip()
    body = str(data.get("body") or "").strip()
    if len(subject) < 2 or len(body) < 20:
        raise HTTPException(status_code=502, detail="OpenAI devolvió asunto o cuerpo demasiado vacíos.")
    if inbound_raw and not prospect_timing_soft:
        body = _maybe_scrub_inbound_evasive(body, system, inbound_snippet=inbound_raw)
    # Cinturón: si preguntó precio y no hay ficha, nunca dejar un invento del LLM.
    if (
        inbound_raw
        and price_asked(inbound_raw)
        and not pricing_known
        and looks_like_invented_pricing(body)
    ):
        team_hint = None
        m_team = re.search(r"(\d+)\s*[-–]?\s*(\d+)?\s*sdrs?", inbound_raw, re.I)
        if m_team:
            a, b = m_team.group(1), m_team.group(2)
            team_hint = f"{a}–{b} SDRs" if b else f"{a} SDRs"
        body = build_price_unknown_email_reply(
            insist=inbound_insists_price_before_meeting(inbound_raw),
            team_hint=team_hint,
        )
    subject = _normalize_gmail_subject_human(
        subject,
        first_name=pname_first,
        full_name=pname,
        company=str(prospect.get("company_name") or ""),
        product_name=str(product.get("name") or ""),
    )
    if len(subject) > 998:
        subject = subject[:995] + "…"
    from app.services.outbound_text_normalize import normalize_outbound_email_body

    return subject, normalize_outbound_email_body(body)


def generate_simulated_inbound_turn(
    *,
    prospect: dict,
    campaign: dict,
    product: dict,
    status_label: str,
    education: str,
) -> str:
    """Mensaje inbound simulado (prospecto ficticio) coherente con el estado elegido por el randomizador."""
    system = resolve_voice_global(campaign) + _education_block(education)
    user_prompt = (
        "Escribí un único mensaje INBOUND breve, como si lo mandara el prospecto real "
        f"por un chat tipo {campaign.get('preferred_channel_hint','')}.\n\n"
        f"Estado / intención objetivo (no lo nombres textual): {status_label}\n"
        f"Nombre: {prospect.get('name')} | empresa: {prospect.get('company_name')}\n"
        f"Tono empresa campaña cliente: {campaign.get('tone')}\n\n"
        "Recordá tono persona real: puede tener typos muy leves o informalidad sólo si suena creíble, "
        "pero mantenelo legible profesional liviano.\n"
        "No reveles estado internamente. Sin markdown ni listas."
    )
    return _chat(system, user_prompt, temperature=random.uniform(0.74, 0.92), max_output_tokens=160)


def generate_followup_message(
    *,
    prospect: dict,
    previous_messages: Sequence[dict[str, str]],
    campaign: dict,
    product: dict,
    education: str,
    objection_type: str | None = None,
    interest_level: str | None = None,
    outbound_seq_index: int = 0,
    allow_soft_meeting_hint: bool = False,
    is_final_goodbye: bool = False,
) -> str:
    system = resolve_voice_global(campaign) + _education_block(education)
    obj = objection_type or "none"
    intr = (interest_level or "low").lower()
    cal = (campaign.get("calendar_link") or "").strip()
    meeting_line = ""
    if allow_soft_meeting_hint and cal and intr == "high" and not is_final_goodbye:
        meeting_line = (
            f"Si encaja, cerrá invitando a una llamada corta; si hace falta el link de agenda en una sola frase: {cal}. "
            "No ofrezcas mandar material/resumen por chat como alternativa a la llamada."
        )
    if is_final_goodbye:
        goodbye_prompt = (
            "ÚLTIMO FOLLOW-UP OPCIONAL (post-secuencia) — último intento con aire de despedida sutil.\n"
            "FORMATO: 3–6 líneas cortas. Español neutro-argentino. Sin markdown.\n\n"
            "TONO OBLIGATORIO:\n"
            "- Se tiene que sentir como cierre / último intento, no como otro chase más.\n"
            "- Está BIEN decir algo sutil tipo: «si no es buen momento, dejo de insistir», "
            "«cierro por acá para no molestar», «si más adelante querés retomar, avisame».\n"
            "- Puerta abierta cálida, sin culpa y sin presión de venta.\n"
            "- CTA suave opcional (una frase): si quiere charlar 5–10 min, que avise; si no, cerrás.\n"
            "- PROHIBIDO: ¿pudiste leer/revisar mi mensaje?, re-pitch de producto, features, cerrar venta.\n\n"
            f"Empresa que representás (firma): {campaign.get('brand_name') or campaign.get('company_name') or '—'}\n"
            f"Remitente: {campaign.get('sender_name') or '—'}\n"
            f"Prospecto: {prospect.get('name')} | empresa: {prospect.get('company_name')} "
            f"| rol: {prospect.get('role')}\n"
            f"Producto (solo contexto, no lo re-expliques): {product.get('name')}\n"
            f"Objeción previa: {obj} | Interés: {intr} | Outbounds previos ~{outbound_seq_index}\n\n"
            "Historial (coherencia; no repetir pitch):\n"
            f"{_conversation_digest(previous_messages)}\n\n"
            "Devolvé UN solo mensaje final de despedida sutil / último intento."
        )
        return _chat(
            system,
            goodbye_prompt,
            temperature=random.uniform(0.7, 0.9),
            max_output_tokens=160,
            scrub_followup_cliche=True,
        )
    user_prompt = (
        "Seguimiento (2°+ touch outbound) — el prospecto ya recibió mensajes tuyos. "
        "FORMATO OBLIGATORIO: como máximo 1-2 líneas totales (muy cortas). "
        "Objetivo: retomar el hilo o empujar hacia llamada breve, NUNCA 'te mando info' ni re-explicar producto.\n"
        f"Empresa que representás (firma): {campaign.get('brand_name') or campaign.get('company_name') or '—'}\n"
        f"Remitente: {campaign.get('sender_name') or '—'}\n"
        f"Canal típico: {campaign.get('preferred_channel_hint','')}.\n\n"
        f"Prospecto: {prospect.get('name')} | empresa: {prospect.get('company_name')} "
        f"| rol: {prospect.get('role')} | industria: {prospect.get('industry')}\n"
        f"Tono campaña: {campaign.get('tone')} | campaña interna: {campaign.get('name')}\n"
        f"ICP — industria: {campaign.get('target_industry') or '—'} | país: {campaign.get('target_country') or '—'} "
        f"| rol objetivo: {campaign.get('target_role') or '—'}\n"
    )
    icp_ai = (campaign.get("icp_ai_digest") or "").strip()
    if icp_ai:
        user_prompt += f"Notas ICP (IA, truncado): {icp_ai[:900]}\n"
    user_prompt += (
        f"Objeción previa conocida (si no es none, respetala): {obj}\n"
        f"Interés previo modelado (guideline, no lo cites): {intr}\n"
        f"Número aproximado de outbound ya enviados: {outbound_seq_index}\n"
    )
    timing_guard = ""
    if obj == "timing":
        timing_guard = (
            "\nIMPORTANTE: objeción timing / pidió espacio — NO empujes reunión ni agenda en este mensaje; "
            "solo retomá muy suave o dejá puerta abierta sin CTA duro.\n\n"
        )
    user_prompt += timing_guard + (
        "CONTEXTO / MEMORIA (fundamental):\n"
        "- Leé el historial COMPLETO abajo. Tomá nota mental de qué ya explicaste del producto, "
        "problema, propuesta o CTA.\n"
        "- Este mensaje debe APALANCARSE en lo ya dicho: como si retomás una charla, no como un mail frío.\n"
        "- PROHIBIDO volver a explicar el producto desde cero, repetir pitch largo, o repetir la misma "
        "promesa de valor con otras palabras.\n"
        "- Evitá re-usar de forma llamativa palabras ya muy presentes en tus outbounds previos "
        "(ej.: optimizar, gestión, ayudar, alinear, potenciar) — buscá un registro distinto.\n"
        "- Variá formato: muchas veces 1–3 líneas; a veces MUY corto (una sola línea + pregunta); "
        "a veces un toque más largo pero sin datasheet.\n"
        "- Referencias naturales al hilo previo (puente corto). "
        "PROHIBIDO: ¿pudiste leer/revisar mi mensaje?, 'no sé si viste el mensaje', "
        "'solo quería hacer seguimiento', 'circling back', 'siguiendo mi mensaje'.\n"
        "- No inventes datos del prospecto. Si falta dato, quedate general sin inventar.\n"
        f"{meeting_line}\n\n"
        "Nombre del producto (referencia mínima si hace falta cerrar una idea nueva, NO repetir lo ya dicho):\n"
        f"{product.get('name')}\n\n"
        "Historial conversación (orden cronológico; usá sólo para coherencia):\n"
        f"{_conversation_digest(previous_messages)}\n\n"
        "Tus outbounds previos (obligatorio leer para NO repetir):\n"
        f"{_past_outbounds(previous_messages)}\n\n"
        "Devolvé UN solo mensaje final, en español, sin markdown ni listas."
    )
    return _chat(
        system,
        user_prompt,
        temperature=random.uniform(0.82, 0.97),
        max_output_tokens=90,
        scrub_followup_cliche=True,
    )


def generate_inbound_response(
    *,
    prospect: dict,
    inbound_message: str,
    conversation_history: Sequence[dict[str, str]],
    campaign: dict,
    product: dict,
    education: str,
    objection_type: str | None = None,
    interest_level: str | None = None,
    allow_soft_meeting_close: bool = False,
    inbound_turn_index: int = 1,
    prospect_timing_soft: bool = False,
    prospect_booking_priority: bool = False,
    prospect_substantive_questions: bool = False,
    ai_policy: AiBehaviorPolicy | None = None,
    prospect_wants_meeting: bool = False,
    explicit_meeting_commitment: bool = False,
    reply_objective: str | None = None,
    response_class: str | None = None,
) -> str:
    from app.services.conversation_intelligence import (
        build_professional_closure_reply,
        product_explanation_deferred_to_meeting,
        resolve_closure_kind,
    )

    policy = ai_policy or DEFAULT_POLICY
    objective = (reply_objective or "").strip().lower()
    closure_kind = resolve_closure_kind(
        text=inbound_message,
        response_class=response_class,
        reply_objective=objective,
    )
    if closure_kind:
        return build_professional_closure_reply(
            prospect_name=prospect.get("name"),
            closure_kind=closure_kind,
            inbound_text=inbound_message or "",
        )

    defer_product_to_call = product_explanation_deferred_to_meeting(inbound_message)
    scheduling_stage = objective == "agendar" or (
        prospect_booking_priority
        and defer_product_to_call
    )

    if scheduling_stage:
        system = resolve_voice_inbound_schedule(campaign) + behavior_prompt_section(policy) + _education_block(education)
    else:
        system = resolve_voice_inbound_consultative(campaign) + behavior_prompt_section(policy) + _education_block(education)

    vp = product.get("value_proposition") or ""
    vp_full = (vp[:520] + ("…" if len(vp or "") > 520 else "")) if vp else ""
    obj = objection_type or "none"
    intr = (interest_level or "low").lower()
    cal = (campaign.get("calendar_link") or "").strip()
    substantive = (
        not scheduling_stage
        and (
            prospect_substantive_questions
            or inbound_text_needs_substantive_answer(inbound_message)
        )
    )
    if prospect_timing_soft:
        meeting_rules = (
            "El prospecto pidió tiempo o posponer: respondé con 2–3 frases humanas. "
            "Reconocé el timing sin dramatizar. PROHIBIDO proponer reunión ahora o link de calendario."
        )
        max_lines = "3"
    elif scheduling_stage:
        meeting_rules = (
            "ETAPA: AGENDAR. El prospecto pidió reunión/llamada/demo. "
            "Cerrá con día/horario o link de agenda. Sin pitch de producto."
        )
        if defer_product_to_call:
            meeting_rules += (
                " Preguntó cómo funciona: UNA frase ('te lo mostramos en la llamada') y coordiná horario."
            )
        if cal:
            meeting_rules += f" Link agenda disponible: {cal}"
        else:
            meeting_rules += " Preguntá qué día de la próxima semana (u otra ventana) le queda cómodo."
        max_lines = "4"
    elif prospect_booking_priority:
        if substantive:
            meeting_rules = (
                "El prospecto quiere agendar Y probablemente preguntó algo: "
                "PRIMERO respondé la pregunta con 3–5 líneas de valor; DESPUÉS link de agenda."
            )
        else:
            meeting_rules = (
                "Quiere agendar YA: tono positivo, cerrá con link de agenda en una línea al final."
            )
        if cal:
            meeting_rules += f" Link: {cal}"
        max_lines = "6" if substantive else "4"
    else:
        meeting_rules = (
            "PRIORIDAD #1: respondé útilmente lo que preguntó o comentó (usá contexto de producto). "
            "PRIORIDAD #2: solo al final, sin presión, invitá a charla breve si encaja."
        )
        inject_cal, cal_mandatory = should_inject_calendar_link(
            policy,
            calendar_url=cal,
            inbound_text=inbound_message,
            timing_soft=prospect_timing_soft,
            booking_priority=prospect_booking_priority,
            interest_level=intr,
            prospect_wants_meeting=prospect_wants_meeting,
            explicit_meeting_commitment=explicit_meeting_commitment,
            substantive_questions=substantive,
        )
        if inject_cal and cal_mandatory:
            meeting_rules += f" Incluí el link de agenda: {cal}"
        elif inject_cal:
            meeting_rules += f" Podés cerrar suave con el link solo al final: {cal}"
        elif cal:
            meeting_rules += " No incluyas link de calendario en este turno."
        elif cal and allow_soft_meeting_close and policy.calendar_link == "soft_suggestion":
            meeting_rules += f" Podés cerrar suave con: {cal}"
        max_lines = "6" if substantive else "4"
        if policy.response_length == "short":
            max_lines = "4" if substantive else "3"
        elif policy.response_length == "detailed":
            max_lines = "8" if substantive else "6"
    stage_line = f"Etapa comercial / objetivo: {objective or 'seguimiento'}\n" if objective else ""
    user_prompt = (
        "Contestá como vendedor consultivo ante un mensaje inbound "
        f"(canal: {campaign.get('preferred_channel_hint','')}).\n\n"
        f"{stage_line}"
        f"Prospecto: {prospect.get('name')} | {prospect.get('company_name')} | {prospect.get('role')}\n"
        f"Campaña: {campaign.get('name')} | Tono: {campaign.get('tone')}\n"
        f"Producto: {product.get('name')}\n"
        + (
            f"Propuesta de valor (referencia breve si hace falta, NO copiar entera): {vp_full}\n\n"
            if not scheduling_stage
            else "\n"
        )
        + f"Objeción: {obj} | Interés: {intr} | Turno: {inbound_turn_index}\n"
        f"{meeting_rules}\n\n"
        + _thread_summary_block(prospect)
        + (
            "PROHIBIDO: 'te lo cuento en la reunión' sin explicar antes. "
            "PROHIBIDO: solo empujar agenda sin aportar valor.\n"
            if not scheduling_stage
            else "PROHIBIDO: volver a explicar el producto como primer cold outreach.\n"
        )
        + "Si send_info: respondé con bullets mentales en prosa (qué hace, para quién, beneficio) — no solo 'mandamos PDF'.\n"
        f"Historial:\n{_conversation_digest(conversation_history)}\n\n"
        f"MENSAJE DEL PROSPECTO:\n{inbound_message}\n\n"
        f"Máximo {max_lines} líneas cortas en total. Texto plano."
    )
    out = _chat(
        system,
        user_prompt,
        temperature=random.uniform(0.55, 0.68) if scheduling_stage else random.uniform(0.62, 0.78),
        max_output_tokens=120 if scheduling_stage else (220 if substantive else 150),
        model=MODEL_INBOUND,
    )
    if substantive and inbound_message and not scheduling_stage:
        out = _maybe_scrub_inbound_evasive(out, system, inbound_snippet=inbound_message)
    return out


def generate_linkedin_inbound_reply(
    *,
    prospect: dict,
    inbound_message: str,
    conversation_history: Sequence[dict[str, str]],
    campaign: dict,
    product: dict,
    education: str,
    interest_level: str | None = None,
    allow_soft_meeting_close: bool = True,
    allow_opening_greeting: bool = True,
    max_words: int | None = None,
) -> str:
    """
    Réplica LinkedIn/WhatsApp inbound: personalizada, breve, anclada al mensaje.
    `max_words` (p. ej. 65 en WA producto-QA) afloja el techo sin soltar plantillas.
    """
    inbound_raw = (inbound_message or "").strip()
    if not inbound_raw:
        raise ValueError("Mensaje inbound vacío")

    substantive = inbound_text_needs_substantive_answer(inbound_raw)
    # LI ~45–70; WA pasa max_words (~35–45). No superar 70 en DM.
    word_cap = int(max_words) if max_words and int(max_words) > 0 else (65 if substantive else 45)
    word_cap = max(30, min(word_cap, 70))
    vp = (product.get("value_proposition") or product.get("description") or "")[:280]
    cal = (campaign.get("calendar_link") or "").strip()
    meeting_hint = ""
    if allow_soft_meeting_close and cal:
        meeting_hint = f" Podés cerrar con invitación suave a charla breve; link disponible: {cal}"
    elif allow_soft_meeting_close:
        meeting_hint = " Podés cerrar con invitación suave a charla breve de 15 min (sin presión)."

    product_rule = (
        "El prospecto PREGUNTÓ por el producto: respondé como experto del producto "
        f"en 1-3 frases concretas (hechos, no marketing vacío): {vp}. "
        "Si falta un dato exacto, decí lo que sí sabés y ofrecé cerrarlo en la call. No inventes."
        if substantive
        else (
            "El prospecto NO pidió explicación del producto. "
            "PROHIBIDO pitch/beneficios/%. Solo acknowledge + CTA suave a reunión."
        )
    )
    greeting_rule = (
        "SALUDO: abrí con 'Hola [primer nombre],' y seguí directo a la respuesta. "
        "PROHIBIDO latiguillo ('Buena pregunta', 'Perfecto', 'Genial', etc.)."
        if allow_opening_greeting
        else (
            "SALUDO: PROHIBIDO 'Hola' / 'Buen día' / 'Hey'. "
            "Este hilo ya tuvo saludo. "
            "PROHIBIDO latiguillos ('Buena pregunta', 'Perfecto', 'Genial', 'Listo', "
            "'Dale', 'Claro'). Arrancá con la respuesta misma."
        )
    )

    system = VOICE_LINKEDIN_INBOUND_DM + _education_block(education)
    from app.services.outreach_language import language_from_campaign_dict, language_prompt_block

    system = system + language_prompt_block(
        language_from_campaign_dict(campaign),
        country=(campaign or {}).get("seller_country") or (campaign or {}).get("style_country"),
    )
    channel_label = "WhatsApp" if "whatsapp" in str(campaign.get("name") or "").lower() else "LinkedIn"
    user_prompt = (
        f"Escribí UN solo mensaje de {channel_label} como réplica al prospecto.\n\n"
        f"Prospecto: {prospect.get('name')} | {prospect.get('company_name')} | {prospect.get('role')}\n"
        f"Campaña: {campaign.get('name')} | Tono: {campaign.get('tone')}\n"
        f"Producto (contexto, no lo pitches salvo que pregunten): {product.get('name')}\n"
        f"{product_rule}\n"
        f"{greeting_rule}\n"
        f"Interés estimado: {(interest_level or 'medium').lower()}\n"
        f"{meeting_hint}\n\n"
        + _thread_summary_block(prospect)
        + f"Historial reciente:\n{_conversation_digest(conversation_history)}\n\n"
        f"MENSAJE DEL PROSPECTO (respondé esto):\n{inbound_raw}\n\n"
        f"Máximo {word_cap} palabras. Sin firma. Sin plantillas genéricas. "
        "Terminá la idea completa (prohibido cortar a medias). "
        "Si preguntan integración/conexión con una tool, respondé eso — no armes un vs. "
        "Si hablás del producto: mismos hechos de ficha, redacción un poco distinta a veces "
        "(no hace falta cambiar de ángulo siempre; evitá sonar calcado)."
    )
    out = _chat(
        system,
        user_prompt,
        temperature=random.uniform(0.62, 0.76),
        max_output_tokens=140 if substantive or word_cap >= 60 else 70,
        model=MODEL_INBOUND,
    )
    if substantive and inbound_raw:
        out = _maybe_scrub_inbound_evasive(out, system, inbound_snippet=inbound_raw)
    from app.services.outbound_text_normalize import apply_opening_greeting_policy
    from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

    text = apply_opening_greeting_policy(
        _trim_words(out, word_cap + 3),
        allow_greeting=allow_opening_greeting,
    )
    return enforce_one_meeting_cta_per_message(text)


def _trim_words(text: str, max_words: int) -> str:
    """Misma política que LinkedIn/WA: oraciones completas, sin '…' a mitad de frase."""
    from app.services.linkedin_reply_compose import _trim_words as _trim_chat

    return _trim_chat(text, max_words=max_words)


_SEQUENCE_DAY_HINTS: dict[int, str] = {
    1: "Día 1: email inicial, personalización media-alta, objetivo presentar Nexus sin ser agresivo.",
    4: "Día 4: solicitud de conexión LinkedIn (o WhatsApp apalancando el mail 1; si no hay teléfono, email).",
    7: "Día 7: WhatsApp o email con hilo previo y un beneficio concreto o caso corto.",
    10: "Día 10: toque LinkedIn: sugerí una interacción social (post, like, comentario breve) sin DM largo.",
    14: "Día 14: WhatsApp o email más directo: foco ahorro de tiempo/costos y eficiencia.",
    18: "Día 18: último mensaje LinkedIn DM (baja presión), listo para que el SDR lo envíe.",
    21: "Día 21: break-up email elegante, puerta abierta, sin culpas.",
}


def generate_sequence_touch_message(
    *,
    day: int,
    channel: str,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    tone: str,
    education_blob: str,
) -> str:
    """Un solo mensaje para el hito `day` del embudo 21d (simulación multicanal)."""
    hint = _SEQUENCE_DAY_HINTS.get(day, "Seguimiento de secuencia multicanal Nexus.")
    system = (
        resolve_voice_global(campaign)
        + _education_block(education_blob)
        + "\n\nSos el motor comercial Nexus. Escribí UN solo mensaje outbound listo para enviar.\n"
        f"Tono campaña: {tone}. Canal previsto: {channel}. {hint}\n"
        "No menciones APIs internas ni 'simulación'. Español neutro-argentino. Máximo ~120 palabras."
    )
    user = (
        f"Prospecto: {prospect.get('name')} @ {prospect.get('company_name')} ({prospect.get('role') or 'rol —'})\n"
        f"Campaña: {campaign.get('name')} · ICP: {campaign.get('target_role')} / {campaign.get('target_industry')}\n"
        f"Producto: {product.get('name')} — {product.get('value_proposition') or product.get('description', '')[:400]}\n"
        f"Link calendario (solo si encaja naturalmente): {campaign.get('calendar_link') or '—'}\n"
    )
    return _chat(system, user, temperature=0.55, max_output_tokens=520)


def generate_reactivation_ping(
    *,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    tone: str,
    education_blob: str,
) -> str:
    """Día 42 / follow-up opcional: último intento con aire de despedida sutil."""
    system = (
        resolve_voice_global(campaign)
        + _education_block(education_blob)
        + "\n\nÚLTIMO INTENTO OPCIONAL (reactivación tardía): mensaje corto de cierre sutil. "
        "Tiene que sentirse como despedida / último intento, no como otro chase. "
        "Está BIEN decir algo tipo «si no es buen momento, dejo de insistir» o "
        "«cierro por acá para no molestar; si más adelante querés retomar, avisame». "
        "Puerta abierta cálida. Sin presión de venta. Sin ¿pudiste leer mi mensaje?. "
        "Sin re-pitch. Español neutro-argentino. Máximo 90 palabras. "
        f"Tono: {tone}."
    )
    user = (
        f"Prospecto: {prospect.get('name')} en {prospect.get('company_name')}.\n"
        f"Producto referencia (no lo re-expliques): {product.get('name')}.\n"
        f"Campaña: {campaign.get('name')}.\n"
        f"Remitente: {campaign.get('sender_name') or '—'}.\n"
        "Escribí el mensaje final de despedida sutil / último intento."
    )
    return _chat(system, user, temperature=0.62, max_output_tokens=380)


def interpret_product_document(raw_text: str) -> dict[str, str]:
    """
    Extrae campos de producto desde texto largo (pegar documento / descripción).
    Si el brief ya viene etiquetado (Nombre / Descripción / …), se preserva entero
    con parse determinista; la IA solo rellena huecos.
    """
    from app.services.product_brief_parse import (
        merge_interpret_dicts,
        parse_structured_product_brief,
    )

    text = (raw_text or "").strip()
    if len(text) < 40:
        raise HTTPException(
            status_code=400,
            detail="Pegá al menos unas líneas de descripción o documento (mín. ~40 caracteres).",
        )

    structured = parse_structured_product_brief(text)
    structured_dict = structured.as_interpret_dict()

    # Brief rico etiquetado: no hace falta IA (más fiel + más rápido).
    if structured.is_rich and (
        structured_dict.get("description") or structured_dict.get("value_proposition")
    ):
        if structured_dict.get("suggested_name") or structured_dict.get("description"):
            return structured_dict

    system = (
        "Sos analista de producto B2B. Respondé únicamente con un objeto JSON (sin markdown ni texto fuera del JSON) "
        "con estas claves string en español: "
        "suggested_name, description, value_proposition, target_audience, pain_points, main_benefits, "
        "recommended_tone, use_cases, common_objections. "
        "REGLA CRÍTICA: si el texto ya trae secciones etiquetadas (Nombre, Descripción, Propuesta de valor, "
        "Público objetivo, Casos de uso, Pain points, Beneficios, Objeciones, Tono), COPIÁ el contenido "
        "COMPLETO de cada sección (NO resumas, NO acortes). "
        "Preservá números, canales (email/LinkedIn/WhatsApp) y diferenciadores. "
        "No hay máximo de letras: si es largo, igual devolvés el texto íntegro en el JSON."
    )
    # Sin recorte artificial de entrada: el brief completo va al modelo.
    user_prompt = f"Contenido a interpretar:\n\n{text}"
    try:
        raw = _raw_chat(system, user_prompt, temperature=0.2, max_output_tokens=8_000)
    except Exception:
        if structured.is_rich:
            return structured_dict
        raise
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        if structured.is_rich:
            return structured_dict
        raise HTTPException(
            status_code=502,
            detail="La IA no devolvió JSON válido. Probá acortar o reformatear el texto.",
        ) from exc
    if not isinstance(data, dict):
        if structured.is_rich:
            return structured_dict
        raise HTTPException(status_code=502, detail="Respuesta de IA inesperada.")
    out: dict[str, str] = {}
    for k in (
        "suggested_name",
        "name",
        "description",
        "value_proposition",
        "target_audience",
        "pain_points",
        "main_benefits",
        "recommended_tone",
        "use_cases",
        "common_objections",
    ):
        v = data.get(k, "")
        out[k] = str(v).strip() if v is not None else ""
    if not out.get("suggested_name") and out.get("name"):
        out["suggested_name"] = out["name"]
    # El parse etiquetado gana en campos que ya trajo (no dejar que la IA los achique).
    return merge_interpret_dicts(structured_dict, out)


def analyze_campaign_icp(
    *,
    campaign_name: str,
    product_name: str,
    product_description: str,
    target_company_size: str | None,
    target_industry: str | None,
    target_country: str | None,
    target_language: str | None,
    target_role: str | None,
    tone: str,
    allowed_channels: list[str],
    prospect_count: int,
    target_interests: str | None = None,
    outreach_mode: str | None = None,
) -> dict[str, str | int | list[str]]:
    mode = (outreach_mode or "b2b").strip().lower()
    is_b2c = mode == "b2c"
    strategist = "estratega GTM B2C (consumidor final)" if is_b2c else "estratega GTM B2B"
    system = (
        f"Sos {strategist}. Respondé sólo JSON (sin markdown) con claves: "
        "icp_quality (breve), icp_scope (muy amplio|amplio|ajustado|estrecho), recommendations (texto multilinea corto), "
        "suggested_channels (array de strings: linkedin, email, whatsapp), message_style (breve), "
        "low_response_risk (bajo|medio|alto), suggested_initial_prospect_count (entero entre 20 y 500), "
        "notes (opcional, breve)."
    )
    if is_b2c:
        icp_lines = (
            f"Modo campaña: B2C (personas, no empresas)\n"
            f"ICP — país/región: {target_country or '—'}\n"
            f"ICP — idioma: {target_language or '—'}\n"
            f"ICP — perfil / rol: {target_role or '—'}\n"
            f"ICP — intereses / keywords: {target_interests or '—'}\n"
        )
    else:
        icp_lines = (
            f"Modo campaña: B2B\n"
            f"ICP — tamaño empresa: {target_company_size or '—'}\n"
            f"ICP — industria: {target_industry or '—'}\n"
            f"ICP — país: {target_country or '—'}\n"
            f"ICP — idioma: {target_language or '—'}\n"
            f"ICP — rol: {target_role or '—'}\n"
        )
    user = (
        f"Campaña: {campaign_name}\n"
        f"Producto: {product_name}\n"
        f"Descripción producto (truncada): {(product_description or '')[:1200]}\n"
        f"{icp_lines}"
        f"Tono campaña: {tone}\n"
        f"Canales permitidos (orden): {', '.join(allowed_channels)}\n"
        f"Cantidad objetivo prospectos: {prospect_count}\n"
    )
    raw = _raw_chat(system, user, temperature=0.2, max_output_tokens=1200)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail="La IA no devolvió JSON válido para el análisis de ICP.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Respuesta de IA inesperada.")
    ch = data.get("suggested_channels") or allowed_channels
    if isinstance(ch, str):
        ch = [x.strip() for x in ch.split(",") if x.strip()]
    if not isinstance(ch, list):
        ch = list(allowed_channels)
    ch = [str(x).lower().strip() for x in ch if str(x).strip()]
    try:
        n = int(data.get("suggested_initial_prospect_count", prospect_count))
    except (TypeError, ValueError):
        n = prospect_count
    n = max(20, min(500, n))
    return {
        "icp_quality": str(data.get("icp_quality") or "").strip(),
        "icp_scope": str(data.get("icp_scope") or "").strip(),
        "recommendations": str(data.get("recommendations") or "").strip(),
        "suggested_channels": ch[:5],
        "message_style": str(data.get("message_style") or "").strip(),
        "low_response_risk": str(data.get("low_response_risk") or "medio").strip().lower(),
        "suggested_initial_prospect_count": n,
        "notes": str(data.get("notes") or "").strip(),
    }


def _mvp_prospect_context_block(prospect: dict) -> str:
    """Bloque de contexto real de prospección para personalizar borradores MVP."""
    lines: list[str] = []
    ctx = (prospect.get("prospecting_context") or "").strip()
    if ctx:
        lines.append(ctx)
    for key, label in (
        ("website", "Web empresa"),
        ("domain", "Dominio"),
        ("linkedin_url", "LinkedIn"),
        ("icp_score", "Score ICP"),
        ("company_size", "Tamaño empresa"),
        ("enrichment_source", "Fuente datos"),
        ("industry", "Industria"),
    ):
        val = (prospect.get(key) or "").strip()
        if val:
            lines.append(f"{label}: {val}")
    if not lines:
        return ""
    return (
        "CONTEXTO REAL DE PROSPECCIÓN (obligatorio: personalizá el mensaje con estos datos; "
        "no uses plantillas genéricas ni inventes hechos):\n"
        + "\n".join(lines)
        + "\n\n"
    )


def generate_linkedin_sdr_draft(
    *,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    education: str,
    is_reply: bool,
    last_prospect_message: str,
) -> str:
    """Borrador corto para que el SDR copie y pegue en LinkedIn (no envía)."""
    if is_reply and (last_prospect_message or "").strip():
        system = resolve_voice_inbound_consultative(campaign) + _education_block(education)
        user = (
            "Generá UN mensaje outbound para LinkedIn (texto plano) como réplica del SDR.\n"
            "PRIORIDAD #1: respondé concretamente lo que preguntó el prospecto.\n"
            "PRIORIDAD #2: cierre suave con llamada de 15 min si encaja.\n"
            "Sin markdown. Máximo 5-6 líneas cortas. No uses nombres de empresa de prueba.\n\n"
            f"Prospecto: {prospect.get('name')} | {prospect.get('company_name')} | rol {prospect.get('role')}\n"
            f"Campaña: {campaign.get('name')} | tono {campaign.get('tone')}\n"
            f"Producto: {product.get('name')} — {product.get('value_proposition', '')[:200]}\n\n"
            f"Último mensaje del prospecto:\n{last_prospect_message[:1200]}\n"
        )
        return _chat(system, user, temperature=random.uniform(0.62, 0.78), max_output_tokens=220)

    system = resolve_voice_global(campaign) + _education_block(education)
    user = (
        "Generá UN primer mensaje outbound para LinkedIn (texto plano), "
        "como si lo fuera a pegar un SDR humano. Sin markdown.\n"
        "Formato OBLIGATORIO: párrafos cortos separados por línea en blanco "
        "(saludo · valor · CTA). PROHIBIDO un solo muro de texto.\n"
        "Longitud: corto pero más desarrollado que WhatsApp (~280-480 caracteres, máx ~550).\n"
        "PROHIBIDO mensaje genérico: mencioná algo concreto de la empresa o el rol según el contexto.\n"
        "La empresa del prospecto se nombra UNA sola vez. El producto se nombra UNA sola vez.\n"
        "NO copies el headline de LinkedIn (cargo + empresa pegados). Rol corto.\n"
        "Presentación: «Soy [SDR] de [marca].» No «Te contacto desde [marca]. Soy [SDR].»\n"
        "Si retomas un mail, no vuelvas a nombrar el producto en esa frase.\n\n"
        f"Prospecto: {prospect.get('name')} | {prospect.get('company_name')} | rol {prospect.get('role')}\n"
        f"Campaña: {campaign.get('name')} | tono {campaign.get('tone')}\n"
        f"Producto: {product.get('name')}\n"
        f"Propuesta de valor (no copiar literal): {(product.get('value_proposition') or '')[:320]}\n\n"
        f"{_mvp_prospect_context_block(prospect)}"
    )
    from app.services.linkedin_copy_quality import polish_linkedin_outbound

    draft = _chat(system, user, temperature=random.uniform(0.72, 0.88), max_output_tokens=220)
    return polish_linkedin_outbound(
        draft,
        company=(prospect.get("company_name") or ""),
        product=(product.get("name") or ""),
    )


def generate_whatsapp_sdr_draft(
    *,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    education: str,
) -> str:
    """Borrador inicial WhatsApp — personalizado, no plantilla del email."""
    system = resolve_voice_global(campaign) + _education_block(education)
    pname = (prospect.get("name") or "").split()[0] or "Hola"
    user = (
        "Generá UN primer mensaje outbound para WhatsApp (texto plano). "
        "Tono informal, chill, según el país de Mi estilo. Sin markdown.\n"
        "MÁS CORTO que LinkedIn: ideal 20-35 palabras (máx ~45 / ~260 caracteres).\n"
        "Formato OBLIGATORIO: 2–3 micro-párrafos con línea en blanco "
        "(saludo; 1 idea de valor; CTA). PROHIBIDO párrafo muro y email formal.\n"
        "Mencioná la empresa/rol con un gancho breve del contexto.\n\n"
        f"Primer nombre: {pname}\n"
        f"Prospecto: {prospect.get('name')} | {prospect.get('company_name')} | rol {prospect.get('role')}\n"
        f"Campaña: {campaign.get('name')} | tono {campaign.get('tone')}\n"
        f"Producto: {product.get('name')}\n"
        f"Propuesta de valor (referencia, no copiar): {(product.get('value_proposition') or '')[:320]}\n\n"
        f"{_mvp_prospect_context_block(prospect)}"
    )
    return _chat(system, user, temperature=random.uniform(0.7, 0.86), max_output_tokens=140)
