"""
Banco determinístico de mensajes cold / follow-up (B2B + B2C).

Sin research web: slots CRM (nombre, empresa, rol) + {valor} de producto.
Respeta Mi estilo del SDR si hay plantilla. Email ≠ LinkedIn ≠ WhatsApp.
Rotación estable por prospecto/campaña/canal/toque.

Reglas humanas (por nº de mensaje en la secuencia del prospecto):
1) Siempre enganchar a la persona/empresa ANTES del producto.
2) Al menos 1 vez por secuencia: preguntar cómo está.
3) Toque 1: explicar un poco más qué es / qué hace el producto (cualquier canal).
4) Toque 2+: apalancar el mensaje/canal anterior; desde el 3er toque, más fuerte
   («Retomo lo que te decía por …»).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from app.schemas.mvp_outreach import SdrReasoningRead

Channel = Literal["email", "linkedin", "whatsapp"]
Market = Literal["b2b", "b2c"]

# ---------------------------------------------------------------------------
# Subjects (email cold)
# ---------------------------------------------------------------------------

_EMAIL_SUBJECTS_B2B = (
    "{valor_asunto}",
    "{valor_asunto} para {empresa}",
    "Propuesta breve: {valor_asunto}",
    "Idea: {valor_asunto}",
    "{valor_asunto} · {empresa}",
)
_EMAIL_SUBJECTS_B2C = (
    "{valor_asunto}",
    "Propuesta breve: {valor_asunto}",
    "Idea: {valor_asunto}",
    "{valor_asunto} para vos",
    "Un espacio: {valor_asunto}",
)

# Estructura Nexus default (solo cambia el CTA). Mi estilo del SDR pisa esto.
_NEXUS_CTAS_EMAIL = (
    "¿Coordinamos una reunión breve?",
    "¿Te queda bien una llamada breve esta semana?",
    "¿Tenés 15 minutos esta semana para verlo?",
    "¿Te parece una videollamada breve?",
    "¿Agendamos una llamada corta?",
    "¿Hablamos un rato esta semana?",
    "¿Te sirve una reunión breve para ver si aplica?",
    "¿Podemos coordinar 15 minutos?",
)
_NEXUS_CTAS_LI = (
    "¿Coordinamos una charla corta esta semana?",
    "¿Te queda bien una videollamada breve?",
    "¿Tiene sentido una reunión corta esta semana?",
    "¿Podemos coordinar 10 o 15 minutos?",
    "¿Te parece si agendamos 15 minutos?",
    "¿Lo vemos en una llamada breve?",
    "¿Te sirve una charla corta para ver si aplica?",
    "¿Hablamos un rato esta semana?",
)
_NEXUS_CTAS_WA = (
    "¿Tenés un rato esta semana para una llamada corta?",
    "¿Agendamos una charla breve?",
    "¿Te sirve una videollamada corta?",
    "¿Hablamos un rato?",
    "¿Coordinamos una llamada corta?",
    "¿Te parece si agendamos 15 minutos?",
    "¿Te queda bien una llamada breve?",
    "¿Lo vemos en una llamada corta?",
)


def _nexus_email_cold() -> tuple[str, ...]:
    return tuple(
        f"""Hola {{nombre}},

Soy {{sdr}} de {{marca}}. {{vi_que_sos}}

{{valor}}

{cta}

Saludos,
{{sdr}}"""
        for cta in _NEXUS_CTAS_EMAIL
    )


def _nexus_li_cold() -> tuple[str, ...]:
    return tuple(
        f"""Hola {{nombre}},

Soy {{sdr}} de {{marca}}. {{vi_que_sos}}

{{valor}}

{cta}"""
        for cta in _NEXUS_CTAS_LI
    )


def _nexus_wa_cold() -> tuple[str, ...]:
    return tuple(
        f"""Hola {{nombre}},

Soy {{sdr}} de {{marca}}.

{{vi_que_sos}} {{valor}}

{cta}"""
        for cta in _NEXUS_CTAS_WA
    )


# ---------------------------------------------------------------------------
# Templates — B2B email cold
# ---------------------------------------------------------------------------

_B2B_EMAIL_COLD = _nexus_email_cold()

_B2B_EMAIL_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Sigo interesado en ver si {producto} puede ayudar a {empresa}.

Si te sirve, ¿podemos agendar una reunión breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido una call corta para retomarlo?

Quedo atento a lo que te quede mejor.""",
    """Hola {nombre},

{retomo}
No quiero saturar. Si en algún momento querés explorar {producto}, coordinamos un espacio.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para retomar esa conversación en una reunión breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
La idea sigue siendo una charla corta sobre {producto}, sin presión.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Si ahora no es el mejor momento para lo de {producto}, entiendo. Cuando quieras retomarlo, avísame y lo vemos.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

¿Hoy vale la pena agendar 15 minutos para ver si ayuda a {empresa}?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Sigue teniendo sentido explorar si puede ayudar a {empresa}, o preferís dejarlo para más adelante?

Quedo atento.""",
)

_B2B_LI_COLD = _nexus_li_cold()

_B2B_LI_FU = (
    """Hola {nombre},

{retomo}
{como_estas}La idea sigue siendo ver si {producto} suma para {empresa}.

¿Te viene bien 15 minutos esta semana?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido retomar esa conversación en una llamada corta?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión: si ahora no es momento, lo dejamos. Si sí, ¿coordinamos 10 o 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para una charla breve sobre {producto}?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Quería dejarlo arriba por si el mensaje anterior no llegó en buen momento.

¿Agendamos 10 minutos para ver si encaja?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Vale la pena 15 minutos para {empresa} con {producto}, o preferís retomarlo más adelante?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo solo esto: {valor}

¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve, avisame y armamos un espacio corto.

Quedo atento.""",
)

_B2B_WA_COLD = _nexus_wa_cold()

_B2B_WA_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve una llamada corta sobre {producto}, avisame.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor hoy o otro día para 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión. Si más adelante querés ver {producto}, escribime.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
¿Hacemos una charla corta esta semana?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

Si no es momento, lo retomamos después.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Esta semana o la próxima para una llamada breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene bien una llamada breve esta semana?

Quedo atento.""",
)

# ---------------------------------------------------------------------------
# Templates — B2C
# ---------------------------------------------------------------------------

_B2C_EMAIL_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque me interesa ver si podemos ayudarte con {producto}.

{valor}

Si te parece útil, ¿coordinamos una charla breve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

¿Te interesaría una call corta esta semana para verlo juntos?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {valor}

¿Tiene sentido una reunión breve para ver si te sirve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te contacto para ver si {producto} puede facilitarte algo concreto.

{valor}

¿Podemos agendar un espacio en tu semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Una idea que capaz te suma.

{valor}

Si te resuena, ¿te parece una videollamada esta semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Pensé en escribirte porque capaz {producto} te ayuda de verdad.

{valor}

¿Te queda bien una videollamada corta?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Quiero ver si {producto} te suma, sin forzar nada.

{valor}

¿Coordinamos un rato para repasarlo?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque {valor_clause}

¿Te gustaría una reunión breve para explorarlo?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te dejo un mensaje breve sobre cómo podemos ayudarte.

{valor}

¿Tenés 15 minutos esta semana para ver si te sirve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo con ganas de entender tu situación y ver si encajamos.

{valor}

¿Agendamos una reunión breve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

¿Te parece una call de 15 minutos para ver si te sirve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque creo que esto te puede servir.

{valor}

¿Te interesa un meet corto para ver cómo te aplicaría?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

Si esto puede ayudarte, me gustaría contártelo con tiempo.

{valor}

¿Hablamos un rato?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. No busco cerrar nada por mail. Quiero ver si {producto} tiene sentido para vos y en qué te ayudaría.

{valor}

¿Te queda un espacio esta semana?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo con interés genuino en ver si podemos facilitarte algo con {producto}.

{valor}

¿Coordinamos una videollamada?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

Si querés, lo vemos en una call esta semana, sin compromiso.

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Pensé en vos porque {valor_clause}

¿Te parece útil una charla corta?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te propongo mostrarte {producto} y ver juntos si te aplica.

{valor}

¿Qué día de esta semana te queda mejor para una reunión?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. Te escribo porque me interesa entender tu día a día y ver si desde {producto} podemos facilitarte algo concreto.

{valor}

Si te parece, ¿podemos charlar un rato para ver en qué te ayudaría de verdad?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}. {vi_que_sos}

{valor}

¿Te queda bien una llamada breve?

Saludos,
{sdr}""",
    """Hola {nombre},

Soy {sdr} de {marca}.

{valor}

Me pareció que esto te podría servir mucho.

Si estoy en lo correcto, me encantaría agendar un espacio para ver si te podemos ayudar. ¿Te parece?

Saludos,
{sdr}""",
)

_B2C_EMAIL_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Sigo interesado en ver si {producto} puede ayudarte.

Si te sirve, ¿podemos agendar una reunión breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido una call corta para retomarlo?

Quedo atento a lo que te quede mejor.""",
    """Hola {nombre},

{retomo}
No quiero saturar. Si en algún momento querés explorar {producto}, coordinamos un espacio.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para retomar esa conversación?

Quedo atento.""",
    """Hola {nombre},

{retomo}
La idea sigue siendo una charla corta sobre {producto}, sin presión.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Si ahora no es el mejor momento para lo de {producto}, entiendo. Cuando quieras retomarlo, avísame.

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

¿Hoy vale la pena agendar 15 minutos para ver si te ayuda?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Sigue teniendo sentido explorarlo, o preferís dejarlo para más adelante?

Quedo atento.""",
)

_B2C_LI_COLD = (
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te parece si coordinamos una charla corta esta semana?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {como_estas}{hook}

{valor}

¿Te queda bien una videollamada breve?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Tiene sentido una reunión corta esta semana?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Podemos coordinar 10 o 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

¿Te parece si agendamos 15 minutos para charlarlo?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

Si te resuena, ¿lo vemos en una llamada breve?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {como_estas}{hook}

{valor}

¿Te sirve una charla corta para explorarlo?""",
    """Hola {nombre},

Te escribo desde {marca}, soy {sdr}. {como_estas}{hook}

{valor}

¿Te queda bien una videollamada corta?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Coordinamos un meet rápido esta semana?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {como_estas}{hook}

{valor}

¿Te parece si hablamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

¿Agendamos una reunión breve para verlo con calma?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te sirve una llamada de 15 minutos?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

¿Te parece útil una charla corta?""",
    """Hola {nombre},

Soy {sdr}. {como_estas}{hook}

{valor}

Si querés, ¿lo vemos en un meet corto esta semana?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {como_estas}{hook}

{valor}

¿Hablamos un rato para ver si te aplica?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {como_estas}{hook}

{valor}

Me pareció que esto te podría servir mucho.

Si estoy en lo correcto, me encantaría agendar un espacio para ver si te podemos ayudar. ¿Te parece?""",
)

_B2C_LI_FU = (
    """Hola {nombre},

{retomo}
{como_estas}La idea sigue siendo ver si {producto} te suma.

¿Te viene bien 15 minutos esta semana?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Tiene sentido retomar esa conversación en una llamada corta?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión: si ahora no es momento, lo dejamos. Si sí, ¿coordinamos 10 o 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor esta semana o la próxima para una charla breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Quería dejarlo arriba por si el mensaje anterior no llegó en buen momento.

¿Agendamos 10 minutos para ver si encaja?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Vale la pena 15 minutos con {producto}, o preferís retomarlo más adelante?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo solo esto: {valor}

¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve, avisame y armamos un espacio corto.

Quedo atento.""",
)

_B2C_WA_COLD = (
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

¿Tenés un rato esta semana para una llamada corta?""",
    """Hola {nombre},

Soy {sdr}, de {marca}.

{motivo}

¿Agendamos una charla breve?""",
    """Hola {nombre},

Te escribe {sdr}, de {marca}. {motivo}

¿Te sirve una videollamada corta?""",
    """Hola {nombre},

Te escribe {sdr}, de {marca}. {motivo}

¿Hablamos un rato?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

Si te interesa, ¿coordinamos una llamada corta?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {motivo}

¿Te parece si agendamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr} de {marca}. {motivo}

¿Te queda bien una llamada breve?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

¿Hacemos una charla corta para ver si te sirve?""",
    """Hola {nombre},

Te escribe {sdr}, de {marca}.

{motivo}

¿Tiene sentido una charla breve?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

¿Coordinamos 15 minutos para charlarlo?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

¿Hablamos un rato para verlo?""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

¿Agendamos 15 minutos?""",
    """Hola {nombre},

Soy {sdr}, de {marca}.

{motivo}

Si querés, lo vemos en una llamada corta.""",
    """Hola {nombre},

Soy {sdr}, de {marca}. {motivo}

¿Te parece una videollamada breve esta semana?""",
    """Hola {nombre},

Te escribe {sdr}, de {marca}. {motivo}

¿Te sirve que lo charlemos 15 minutos?""",
    """Hola {nombre},

Te escribe {sdr}, de {marca}. {motivo}

¿Lo vemos en una llamada corta?""",
)

_B2C_WA_FU = (
    """Hola {nombre},

{retomo}
{como_estas}Si te sirve una llamada corta sobre {producto}, avisame.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene mejor hoy o otro día para 15 minutos?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sin presión. Si más adelante querés ver {producto}, escribime.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Hacemos una charla corta o lo dejamos por ahora?

Quedo atento.""",
    """Hola {nombre},

{retomo}
¿Hacemos una charla corta esta semana?

Quedo atento.""",
    """Hola {nombre},

{retomo}
Sumo esto: {valor}

Si no es momento, lo retomamos después.

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Esta semana o la próxima para una llamada breve?

Quedo atento.""",
    """Hola {nombre},

{retomo}
{como_estas}¿Te viene bien una llamada breve esta semana?

Quedo atento.""",
)

_BANKS: dict[tuple[Market, Channel, str], tuple[str, ...]] = {
    ("b2b", "email", "cold"): _B2B_EMAIL_COLD,
    ("b2b", "email", "fu"): _B2B_EMAIL_FU,
    ("b2b", "linkedin", "cold"): _B2B_LI_COLD,
    ("b2b", "linkedin", "fu"): _B2B_LI_FU,
    ("b2b", "whatsapp", "cold"): _B2B_WA_COLD,
    ("b2b", "whatsapp", "fu"): _B2B_WA_FU,
    ("b2c", "email", "cold"): _B2C_EMAIL_COLD,
    ("b2c", "email", "fu"): _B2C_EMAIL_FU,
    ("b2c", "linkedin", "cold"): _B2C_LI_COLD,
    ("b2c", "linkedin", "fu"): _B2C_LI_FU,
    ("b2c", "whatsapp", "cold"): _B2C_WA_COLD,
    ("b2c", "whatsapp", "fu"): _B2C_WA_FU,
}


@dataclass(frozen=True)
class ColdBankRender:
    subject: str | None
    body: str
    template_id: str
    market: Market
    channel: Channel
    kind: str  # cold | fu
    index: int
    leverage_used: bool
    reasoning: SdrReasoningRead


def _stable_index(*, seed: str, n: int) -> int:
    if n <= 0:
        return 0
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % n


def _norm_channel(channel: str) -> Channel:
    ch = (channel or "email").strip().lower()
    if ch in ("linkedin", "li"):
        return "linkedin"
    if ch in ("whatsapp", "wa"):
        return "whatsapp"
    return "email"


def _norm_market(campaign: dict[str, Any] | None, *, explicit: str | None = None) -> Market:
    raw = (explicit or "").strip().lower()
    if not raw and campaign:
        raw = str(
            campaign.get("outreach_mode")
            or campaign.get("market")
            or campaign.get("mode")
            or ""
        ).strip().lower()
    return "b2c" if raw == "b2c" else "b2b"


def _first_name(prospect: dict[str, Any]) -> str:
    from app.services.outreach_display_names import prospect_greeting_name

    return prospect_greeting_name(prospect) or "hola"


def _sender(campaign: dict[str, Any]) -> str:
    from app.services.outreach_display_names import sender_first_name

    return sender_first_name(
        campaign_sender=campaign.get("sender_name"),
        fallback="el equipo",
    )


def _brand(campaign: dict[str, Any]) -> str:
    from app.services.outreach_display_names import outreach_company_display

    for key in ("brand_name", "company_name", "seller_company_name"):
        brand = outreach_company_display(campaign.get(key)) or ""
        if brand:
            return brand
    return "nuestro equipo"


def _product_name(product: dict[str, Any] | None, *, brand: str) -> str:
    from app.services.outreach_display_names import product_outreach_display_name

    name = product_outreach_display_name(((product or {}).get("name") or "").strip())
    return name or brand or "nuestra solución"


_VALUE_OPENERS = (
    "Con {p} {v}",
    "Desde {p} {v}",
)


def _as_nosotros_valor(blurb: str) -> str:
    t = (blurb or "").strip()
    if not t:
        return t
    if re.match(r"(?i)^nosotros\b", t):
        return t
    if t[:1].isupper():
        t = t[0].lower() + t[1:]
    return f"Nosotros {t}"


_FILLER_WORDS = (
    r"en\s+pocas\s+palabras|b[aá]sicamente|en\s+resumen|resumiendo|en\s+s[ií]ntesis|"
    r"b[aá]sicamente\s+hablando|dicho\s+simple|para\s+que\s+te\s+des\s+una\s+idea|"
    r"la\s+verdad\s+es\s+que|simplemente|concretamente"
)
_FILLER_MID_RE = re.compile(rf",\s*(?:{_FILLER_WORDS})\s*,", re.I)
_FILLER_START_RE = re.compile(rf"(^|(?<=[.!?]\s)|(?<=\n))(?:{_FILLER_WORDS})\s*,?\s+", re.I)


def strip_filler_phrases(text: str | None) -> str:
    """Muletillas que alargan sin decir nada («, en pocas palabras,», «Básicamente, …»)."""
    t = _FILLER_MID_RE.sub("", text or "")

    def _start(m: re.Match[str]) -> str:
        return m.group(1)

    stripped = _FILLER_START_RE.sub(_start, t)
    if stripped != t:
        stripped = re.sub(
            r"(^|(?<=[.!?]\s)|(?<=\n))([a-záéíóúñ])", lambda m: m.group(1) + m.group(2).upper(), stripped
        )
    return re.sub(r"[ \t]{2,}", " ", stripped).strip()


def _valor_one_liner(
    product: dict[str, Any] | None,
    *,
    product_name: str,
    market: Market,
    channel: str = "email",
    explain_more: bool = False,
    seed: str = "",
) -> str:
    """
    Valor reescrito desde la ficha (voz SDR), nunca copy-paste de VP/descripción.
    explain_more solo alarga un poco el tope de canal (excepto WhatsApp: siempre corto).
    """
    from app.services.message_structure_variants import (
        _conversational_value_blurb,
        _vary_value_opener,
    )

    ch = _norm_channel(channel)
    # WA siempre corto; LI/email pueden alargar un poco en el 1.er toque de secuencia.
    blurb_channel = "email" if (explain_more and ch != "whatsapp") else ch
    blurb = (
        _conversational_value_blurb(
            product,
            product_name=product_name,
            channel=blurb_channel,
            seed=seed,
            explain_more=explain_more and ch == "email",
        )
        or ""
    ).strip()
    blurb = strip_filler_phrases(blurb)
    if blurb and len(blurb) >= 12:
        # LinkedIn ya viene con apertura variada; email/WA la aplicamos acá.
        # Sin «Nosotros con/desde…»: la presentación ya es «Soy X de Y.».
        if ch != "linkedin":
            blurb = _vary_value_opener(blurb, product_name=product_name, seed=seed)
        return blurb if blurb.endswith((".", "!", "?")) else f"{blurb}."
    if market == "b2c":
        return f"Con {product_name} podemos ayudarte de forma concreta."
    return f"Con {product_name} podemos ayudar de forma concreta."


def _lowercase_first(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    return t[0].lower() + t[1:] if t[0].isupper() else t


def _valor_clause(valor: str) -> str:
    """Para unir tras 'porque' / similar — sin dos puntos."""
    t = (valor or "").strip()
    if not t:
        return "podemos aportar valor de forma concreta."
    t = re.sub(r"^Básicamente,\s+", "", t)
    t = re.sub(r",\s+en pocas palabras,", "", t, count=1)
    t = _lowercase_first(t)
    return t if t.endswith((".", "!", "?")) else f"{t}."


_HOW_ARE_YOU_RE = re.compile(
    r"\b(c[oó]mo est[aá]s|c[oó]mo va|qu[eé] tal)\b",
    re.I,
)


def _prior_asked_how_are_you(prior_touches: list[dict[str, Any]] | None) -> bool:
    for t in prior_touches or []:
        body = str(t.get("body") or t.get("message_body") or "")
        if _HOW_ARE_YOU_RE.search(body):
            return True
    return False


def _channel_label(channel: Channel) -> str:
    if channel == "linkedin":
        return "LinkedIn"
    if channel == "whatsapp":
        return "WhatsApp"
    return "mail"


def _hook_line(
    *,
    market: Market,
    empresa: str,
    rol: str,
    seed: str,
    avoid: tuple[str, ...] = (),
    channel: str = "email",
) -> str:
    """Enganche humano ANTES del producto (nunca pitch de entrada)."""
    from app.services.outreach_human_anchor import (
        pick_outreach_context_anchor,
        pick_outreach_context_anchor_short,
    )

    if market == "b2c":
        opts = (
            "Te escribo porque me interesa entender un poco tu contexto.",
            "Quería saludarte y ver si tiene sentido charlar un momento.",
            "Te contacto con una consulta corta.",
        )
        idx = _stable_index(seed=seed + "|hook", n=len(opts))
        return opts[idx]

    ch = (channel or "email").strip().lower()
    picker = (
        pick_outreach_context_anchor_short
        if ch in ("linkedin", "whatsapp", "li", "wa")
        else pick_outreach_context_anchor
    )
    return picker(
        empresa=empresa or None,
        rol=rol if rol and rol != "tu rol" else None,
        seed=f"{seed}|hook",
        channel=ch,
        avoid=avoid,
    )


def _retomo_line(
    *,
    prior_touches: list[dict[str, Any]] | None,
    current_channel: Channel,
    producto: str,
    strength: Literal["soft", "strong"] = "soft",
) -> str:
    """Apalanca mensajes/canales previos. strong = toques 3+ de la secuencia.

    Regla 10: el retomo NO nombra el producto (suena a IA si se martilla).
    """
    del producto  # no repetir nombre de producto en el puente
    prior = prior_touches or []
    if not prior:
        return ""
    last = prior[-1]
    last_ch = _norm_channel(str(last.get("channel") or "email"))
    label = _channel_label(last_ch)
    if strength == "strong":
        if last_ch != current_channel:
            return f"Retomo lo que te decía por {label}."
        return "Retomo lo que te comentaba."
    if last_ch != current_channel:
        return f"Te había escrito por {label}."
    if last_ch == "email":
        return "Retomo el mail anterior."
    return "Vuelvo sobre el mensaje anterior."


def _slots(
    *,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
    market: Market,
    channel: str = "email",
    prior_touches: list[dict[str, Any]] | None = None,
    explain_more: bool = False,
    ask_how_are_you: bool = False,
    retomo_strength: Literal["soft", "strong"] = "soft",
    seed: str = "",
    avoid_openers: tuple[str, ...] = (),
) -> dict[str, str]:
    nombre = _first_name(prospect)
    if nombre.lower() == "hola":
        nombre = (prospect.get("name") or "").strip().split()[0] if (prospect.get("name") or "").strip() else "allí"
    if nombre and nombre != "allí" and nombre.islower():
        nombre = nombre[:1].upper() + nombre[1:]
    sdr = _sender(campaign)
    brand = _brand(campaign)
    from app.services.outreach_display_names import (
        company_label_for_outreach,
        prospect_company_for_outreach,
        prospect_role_for_outreach,
    )

    empresa = prospect_company_for_outreach(
        company_name=prospect.get("company_name"),
        email=prospect.get("email"),
        website=prospect.get("company_website"),
        source_provider=prospect.get("source_provider"),
    )
    empresa = company_label_for_outreach(empresa) or empresa
    rol = prospect_role_for_outreach(
        prospect.get("role") or prospect.get("selling_to_role"),
        prospect.get("name"),
        company_name=empresa,
    )
    producto = _product_name(product, brand=brand)
    valor = _valor_one_liner(
        product,
        product_name=producto,
        market=market,
        channel=channel,
        explain_more=explain_more,
        seed=seed or str(prospect.get("id") or nombre or ""),
    )

    anchor_seed = seed or str(prospect.get("id") or nombre or "")
    from app.services.outreach_human_anchor import (
        pick_outreach_context_anchor,
        pick_outreach_context_anchor_short,
        pick_outreach_reason,
    )

    te_escribo_por = pick_outreach_context_anchor(
        empresa=empresa or None,
        rol=rol if rol and rol != "tu rol" else None,
        seed=f"{anchor_seed}|full",
        channel=channel,
        avoid=avoid_openers,
    )
    te_escribo_por_corto = pick_outreach_context_anchor_short(
        empresa=empresa or None,
        rol=rol if rol and rol != "tu rol" else None,
        seed=f"{anchor_seed}|short",
        channel=channel,
        avoid=avoid_openers,
    )
    if rol and empresa:
        como_rol_en_empresa = f"Como {rol} en {empresa}"
        rol_en = f"como {rol} en {empresa}"
        vi_que_sos = f"Vi que sos {rol} de {empresa}."
    elif rol:
        como_rol_en_empresa = f"Como {rol}"
        rol_en = f"como {rol}"
        vi_que_sos = f"Vi que sos {rol}."
    elif empresa:
        como_rol_en_empresa = f"En {empresa}"
        rol_en = f"en {empresa}"
        vi_que_sos = f"Vi que estás en {empresa}."
    else:
        como_rol_en_empresa = "En tu equipo"
        rol_en = "en tu equipo"
        vi_que_sos = ""

    valor_full = valor
    # A veces conectar ancla → valor (oración con sentido); si no, dejar slots separados.
    if vi_que_sos and valor:
        from app.services.message_structure_variants import connect_prospect_anchor_to_value

        mid = connect_prospect_anchor_to_value(
            vi_que_sos,
            valor,
            seed=f"{anchor_seed}|bridge",
            separate_paragraphs=_norm_channel(channel) != "whatsapp",
        )
        if "\n\n" in mid:
            vi_que_sos, valor = mid.split("\n\n", 1)
        else:
            # Un solo bloque (puente o WA en el mismo párrafo).
            vi_que_sos, valor = mid, ""

    hook = _hook_line(
        market=market,
        empresa=empresa,
        rol=rol or "tu rol",
        seed=seed or producto,
        avoid=avoid_openers,
        channel=channel,
    )
    # Va en el renglón del saludo («Hola X, ¿cómo estás?»): lo pone _how_are_you_in_greeting.
    como_estas = ""
    retomo = _retomo_line(
        prior_touches=prior_touches,
        current_channel=_norm_channel(channel),
        producto=producto,
        strength=retomo_strength,
    )
    if retomo and not retomo.endswith((".", "!", "?")):
        retomo = f"{retomo}."
    if retomo:
        retomo = f"{retomo} "

    from app.services.sequence_email_subject_guard import short_value_subject_hook

    valor_asunto = short_value_subject_hook(product)

    return {
        "nombre": nombre,
        "empresa": empresa or "tu equipo",
        "rol": rol or "tu rol",
        "rol_en": rol_en,
        "sdr": sdr,
        "marca": brand,
        "producto": producto,
        "valor": valor,
        "valor_asunto": valor_asunto,
        "valor_clause": _valor_clause(valor_full),
        "te_escribo_por": te_escribo_por,
        "te_escribo_por_corto": te_escribo_por_corto,
        "motivo": pick_outreach_reason(
            empresa=empresa or None,
            rol=rol if rol and rol != "tu rol" else None,
            reason=_valor_clause(valor_full),
            seed=anchor_seed,
            avoid=avoid_openers,
        ),
        "como_rol_en_empresa": como_rol_en_empresa,
        "vi_que_sos": vi_que_sos,
        "hook": hook,
        "como_estas": como_estas,
        "retomo": retomo,
    }


_SELLER_PLACEHOLDER_RE = re.compile(r"[\[{]\s*([A-Za-zÁÉÍÓÚáéíóúÑñ _]{2,30})\s*[\]}]")

_SELLER_PLACEHOLDER_SLOTS: dict[str, str] = {
    "nombre": "nombre",
    "nombreprospecto": "nombre",
    "empresa": "empresa",
    "suempresa": "empresa",
    "empresaprospecto": "empresa",
    "rol": "rol",
    "cargo": "rol",
    "puesto": "rol",
    "sdr": "sdr",
    "minombre": "sdr",
    "tunombre": "sdr",
    "vendedor": "sdr",
    "tuempresa": "marca",
    "miempresa": "marca",
    "marca": "marca",
    "producto": "producto",
    "valor": "valor",
    "propuesta": "valor",
    "propuestadevalor": "valor",
}


def _placeholder_key(raw: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", raw or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[\s_]+", "", t).lower()


def seller_template_has_placeholders(text: str | None) -> bool:
    """Ejemplo de «Mi estilo» escrito como estructura ([Nombre], [Empresa], …)."""
    return any(
        _placeholder_key(m.group(1)) in _SELLER_PLACEHOLDER_SLOTS
        for m in _SELLER_PLACEHOLDER_RE.finditer(text or "")
    )


def _fill_seller_template(template: str, slots: dict[str, str]) -> str:
    def _sub(m: re.Match[str]) -> str:
        key = _SELLER_PLACEHOLDER_SLOTS.get(_placeholder_key(m.group(1)))
        if not key:
            return m.group(0)
        return slots.get(key) or ""

    out = _SELLER_PLACEHOLDER_RE.sub(_sub, template or "")
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip()


def _missing_prospect_slots(slots: dict[str, str]) -> set[str]:
    missing: set[str] = set()
    if (slots.get("nombre") or "").strip().lower() in ("", "allí", "alli"):
        missing.add("nombre")
    if slots.get("empresa") in ("", "tu equipo"):
        missing.add("empresa")
    if slots.get("rol") in ("", "tu rol"):
        missing.add("rol")
    return missing


def _seller_text_without_missing(text: str | None, missing: set[str]) -> str:
    """Marcadores del SDR ([Empresa], {cargo}…) sin dato del prospecto: se reescribe la frase."""
    from app.services.seller_style_templates import MARKERS, omit_missing_markers

    def _canon(m: re.Match[str]) -> str:
        key = _SELLER_PLACEHOLDER_SLOTS.get(_placeholder_key(m.group(1)))
        return MARKERS.get(key or "", m.group(0))

    return omit_missing_markers(_SELLER_PLACEHOLDER_RE.sub(_canon, text or ""), missing)


def _fill(template: str, slots: dict[str, str]) -> str:
    out = template
    for key, val in slots.items():
        out = out.replace("{" + key + "}", val)
    # Limpieza suave de dobles espacios / rol vacío residual.
    out = re.sub(r"como tu rol en ", "en ", out, flags=re.I)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"  +", " ", out)
    return out.strip()


def _leverage_line(
    *,
    prior_touches: list[dict[str, Any]] | None,
    current_channel: Channel,
    producto: str,
    seed: str,
    force: bool = False,
    strength: Literal["soft", "strong"] = "soft",
) -> str:
    prior = prior_touches or []
    others = [
        t
        for t in prior
        if _norm_channel(str(t.get("channel") or "")) != current_channel
    ]
    # Si no hay otro canal, igual podemos apalancar el mismo canal en FU.
    if not others and not (force and prior):
        return ""
    if not force and _stable_index(seed=seed + "|lev", n=100) >= 35:
        return ""
    return _retomo_line(
        prior_touches=prior,
        current_channel=current_channel,
        producto=producto,
        strength=strength,
    ).rstrip()


def _insert_leverage(body: str, *, channel: Channel, line: str) -> str:
    if not line:
        return body
    # Evitar duplicar si la plantilla ya trae {retomo}.
    if line.rstrip(".")[:24].lower() in body.lower():
        return body
    if channel == "email" or "\n\n" in body:
        parts = body.split("\n\n", 2)
        if len(parts) >= 3:
            return f"{parts[0]}\n\n{parts[1]}\n\n{line}\n\n{parts[2]}"
        if len(parts) == 2:
            return f"{parts[0]}\n\n{line}\n\n{parts[1]}"
        return f"{body}\n\n{line}"
    m = re.search(r"([.!?])\s+", body)
    if not m:
        return f"{body} {line}"
    i = m.end()
    return f"{body[:i]}{line} {body[i:]}"


_HOW_ARE_YOU_SENTENCE_RE = re.compile(
    r"¿\s*(?:c[oó]mo\s+est[aá]s|c[oó]mo\s+va|qu[eé]\s+tal)\s*\?[ \t]*",
    re.I,
)
_GREETING_LINE_RE = re.compile(r"^(Hola\s+[^,\n?¿!.]+?|Hola)\s*[,.!]?\s*$", re.I)


def _how_are_you_in_greeting(body: str, *, ask: bool) -> str:
    """«¿Cómo estás?» solo en el renglón del saludo, tras una coma: «Hola Jorge, ¿cómo estás?»."""
    lines = (body or "").split("\n")
    if not lines:
        return body
    first, rest = lines[0], "\n".join(lines[1:])
    had = bool(_HOW_ARE_YOU_SENTENCE_RE.search(rest))
    in_greeting = bool(_HOW_ARE_YOU_SENTENCE_RE.search(first))
    if not (ask or had):
        return body
    m = _GREETING_LINE_RE.match(first.strip())
    if not in_greeting and not m:
        return body
    rest = _HOW_ARE_YOU_SENTENCE_RE.sub("", rest)
    rest = re.sub(r"[ \t]+\n", "\n", rest)
    rest = re.sub(r"\n[ \t]+", "\n", rest)
    rest = re.sub(r"\n{3,}", "\n\n", rest)
    if not in_greeting and m:
        first = f"{m.group(1).strip()}, ¿cómo estás?"
    return f"{first}\n{rest}".rstrip()


def first_touch_on_channel(
    prior_touches: list[dict[str, Any]] | None,
    channel: str,
) -> bool:
    """True si aún no hubo outbound en este canal (puede haber otros canales).

    NO usar para elegir banco cold vs FU (regla 11): cold es solo el 1º toque
    de toda la secuencia. Preferí `is_sequence_first_touch`.
    """
    ch = _norm_channel(channel)
    for t in prior_touches or []:
        if _norm_channel(str(t.get("channel") or "")) == ch:
            return False
    return True


def is_sequence_first_touch(prior_touches: list[dict[str, Any]] | None) -> bool:
    """True solo si no hay ningún toque previo en la secuencia (cualquier canal)."""
    return not (prior_touches or [])


def resolve_bank_kind(
    *,
    prior_touches: list[dict[str, Any]] | None,
    first_touch: bool | None = None,
) -> str:
    """
    Regla 11 — cold únicamente en el 1º mensaje de la secuencia.
    Si ya hubo mail/WA/LI, el banco es siempre FU (nunca linkedin-cold otra vez).
    """
    seq_first = is_sequence_first_touch(prior_touches)
    if first_touch is None:
        return "cold" if seq_first else "fu"
    # Caller no puede forzar cold si ya hay historial.
    if first_touch and not seq_first:
        return "fu"
    return "cold" if first_touch else "fu"


_ANCHOR_OPENER_PHRASES = (
    "te contacto",
    "te escribo",
    "pensé en",
    "quería conectar",
    "quería comentarte",
    "llegué a vos",
    "me pareció",
)


def _template_openers(template: str) -> tuple[str, ...]:
    """Frases de enganche que la plantilla ya trae fija (sin contar los slots)."""
    fixed = re.sub(r"\{\w+\}", " ", template or "").lower()
    found = tuple(p for p in _ANCHOR_OPENER_PHRASES if p in fixed)
    if "te escribe" in fixed and "te escribo" not in found:
        found += ("te escribo",)
    return found


def pick_bank_index(
    *,
    market: Market,
    channel: Channel,
    kind: str,
    prospect_id: int | str | None,
    campaign_id: int | str | None,
    step_day: int = 0,
    avoid: Iterable[int] = (),
) -> int:
    bank = _BANKS[(market, channel, kind)]
    extra = "cold" if kind == "cold" else f"fu|{int(step_day or 0)}"
    seed = f"{prospect_id or 0}|{campaign_id or 0}|{channel}|{market}|{extra}"
    try:
        pid_num = int(prospect_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        pid_num = None
    if pid_num is not None and pid_num > 0:
        # Prospectos de una campaña tienen ids consecutivos: rotar evita repetir plantilla
        # entre los primeros len(bank) de la tanda (el hash puro colisiona seguido).
        offset = _stable_index(seed=f"{campaign_id or 0}|{channel}|{market}|{extra}|rot", n=len(bank))
        idx = (offset + pid_num) % len(bank)
    else:
        idx = _stable_index(seed=seed, n=len(bank))
    from app.services.message_learning import learned_bank_index

    return learned_bank_index(
        market=market,
        channel=channel,
        kind=kind,
        campaign_id=campaign_id,
        stable_index=idx,
        bank_size=len(bank),
        seed=seed,
        avoid=avoid,
    )


def used_bank_indices(prior_touches: list[dict[str, Any]] | None, *, market: str, channel: str, kind: str) -> set[int]:
    """Plantillas de este banco que el prospecto ya recibió (según `bank_template_id` de toques previos)."""
    prefix = f"{market}-{channel}-{kind}-"
    used: set[int] = set()
    for t in prior_touches or []:
        tid = str(t.get("bank_template_id") or "")
        if tid.startswith(prefix):
            try:
                used.add(int(tid[len(prefix):]) - 1)
            except ValueError:
                continue
    return used


def render_cold_bank_touch(
    *,
    channel: str,
    prospect: dict[str, Any],
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
    prior_touches: list[dict[str, Any]] | None = None,
    first_touch: bool | None = None,
    step_day: int = 0,
    outreach_mode: str | None = None,
    seller_template: str | None = None,
    seller_variant: bool = False,
) -> ColdBankRender:
    """
    `seller_template`: estructura propia del SDR con marcadores ([Nombre], [Empresa]…).
    Solo en el 1º toque de la SECUENCIA (regla 11) reemplaza a la plantilla cold.
    """
    ch = _norm_channel(channel)
    market = _norm_market(campaign, explicit=outreach_mode)
    prior = list(prior_touches or [])
    # Nº de mensaje en la secuencia (1 = primero del prospecto, sin importar canal).
    touch_n = len(prior) + 1
    kind = resolve_bank_kind(prior_touches=prior, first_touch=first_touch)
    first_touch = kind == "cold"
    bank = _BANKS[(market, ch, kind)]
    pid = prospect.get("id")
    cid = campaign.get("id") or campaign.get("campaign_id")
    idx = pick_bank_index(
        market=market,
        channel=ch,
        kind=kind,
        prospect_id=pid,
        campaign_id=cid,
        step_day=step_day,
        avoid=used_bank_indices(prior, market=market, channel=ch, kind=kind),
    )
    seed = f"{pid or 0}|{cid or 0}|{ch}|{market}|{kind}|{idx}|n{touch_n}"
    explain_more = touch_n == 1
    # Mínimo 1 vez por secuencia: en el 1.er toque; si no quedó, en el 2.º.
    # LinkedIn cold: siempre preguntar cómo está (estructura acordada).
    ask_how = (not _prior_asked_how_are_you(prior)) and touch_n <= 2
    if ch == "linkedin" and kind == "cold":
        ask_how = True
    retomo_strength: Literal["soft", "strong"] = "strong" if touch_n >= 3 else "soft"

    slots = _slots(
        prospect=prospect,
        campaign=campaign,
        product=product,
        market=market,
        channel=ch,
        prior_touches=prior,
        explain_more=explain_more,
        ask_how_are_you=ask_how,
        retomo_strength=retomo_strength,
        seed=seed,
        avoid_openers=_template_openers(bank[idx]),
    )
    seller_tpl = None
    if seller_template:
        from app.services.seller_style_templates import prepare_seller_template

        seller_tpl = prepare_seller_template(
            seller_template,
            sender=slots["sdr"],
            brand=slots["marca"],
            product=slots["producto"],
        )
    missing = _missing_prospect_slots(slots)
    tpl_body = _seller_text_without_missing(seller_tpl.body, missing) if seller_tpl is not None else ""
    if seller_tpl is not None and tpl_body.strip():
        if seller_variant:
            from app.services.seller_style_templates import vary_seller_template

            tpl_body = vary_seller_template(tpl_body, seed)
        body = _fill_seller_template(tpl_body, slots)
        subject = None
        tpl_subject = _seller_text_without_missing(seller_tpl.subject, missing) if seller_tpl.subject else ""
        if ch == "email" and tpl_subject.strip():
            subject = _fill_seller_template(tpl_subject, slots)
        elif ch == "email" and kind == "cold":
            subjects = _EMAIL_SUBJECTS_B2C if market == "b2c" else _EMAIL_SUBJECTS_B2B
            subject = _fill(subjects[_stable_index(seed=seed + "|subj", n=len(subjects))], slots)
        elif ch == "email":
            subject = f"Re: {slots['producto']}"
        tid = f"seller-{ch}-{kind}" + ("-v" if seller_variant else "")
        if ch == "linkedin":
            from app.services.linkedin_copy_quality import polish_linkedin_outbound

            body = polish_linkedin_outbound(
                body,
                company=slots.get("empresa") or "",
                product=slots.get("producto") or "",
            )
        return ColdBankRender(
            subject=subject,
            body=body,
            template_id=tid,
            market=market,
            channel=ch,
            kind=kind,
            index=idx,
            leverage_used=False,
            reasoning=SdrReasoningRead(
                probable_problem=slots["valor"][:180],
                why_it_matters=f"Estructura propia del SDR (Mi estilo) · toque #{touch_n}",
                hypothesis=slots["producto"],
                response_question="coordinar reunión / call / espacio",
                selling_to_role=(prospect.get("role") or "") if market == "b2b" else "",
            ),
        )

    template = bank[idx]
    if kind == "cold" and slots["producto"].strip().lower() == slots["marca"].strip().lower():
        template = _producto_as_brand(template)
    body = _fill(template, slots)

    lev = ""
    # Toque 2+: siempre apalancar (cross-canal o mismo canal). Cold cross-canal también.
    if touch_n >= 2 or (kind == "cold" and prior):
        # Las plantillas FU ya traen {retomo}; no duplicar si ya está.
        if "{retomo}" not in bank[idx] and "retomo" not in bank[idx].lower():
            lev = _leverage_line(
                prior_touches=prior,
                current_channel=ch,
                producto=slots["producto"],
                seed=seed,
                force=touch_n >= 2,
                strength=retomo_strength,
            )
            if lev:
                body = _insert_leverage(body, channel=ch, line=lev)
    elif kind == "cold":
        lev = _leverage_line(
            prior_touches=prior,
            current_channel=ch,
            producto=slots["producto"],
            seed=seed,
            force=False,
            strength="soft",
        )
        if lev:
            body = _insert_leverage(body, channel=ch, line=lev)

    # Guardrail: nunca empezar el pitch de producto justo después del saludo corto.
    body = _ensure_hook_before_product(body, slots=slots, channel=ch)
    if kind == "cold":
        body = _ensure_role_and_company(body, slots=slots)
    body = _how_are_you_in_greeting(body, ask=ask_how)
    if ch == "linkedin":
        from app.services.linkedin_copy_quality import polish_linkedin_outbound

        body = polish_linkedin_outbound(
            body,
            company=slots.get("empresa") or "",
            product=slots.get("producto") or "",
        )

    subject: str | None = None
    if ch == "email" and kind == "cold":
        subjects = _EMAIL_SUBJECTS_B2C if market == "b2c" else _EMAIL_SUBJECTS_B2B
        s_idx = _stable_index(seed=seed + "|subj", n=len(subjects))
        subject = _fill(subjects[s_idx], slots)
    elif ch == "email" and kind == "fu":
        subject = f"Re: {slots['producto']}"

    tid = f"{market}-{ch}-{kind}-{idx + 1:02d}"
    reasoning = SdrReasoningRead(
        probable_problem=slots["valor"][:180],
        why_it_matters=(
            f"Plantilla {tid} · toque #{touch_n} · "
            f"{'explica producto' if explain_more else 'apalanca previo'}"
        ),
        hypothesis=slots["producto"],
        response_question="coordinar reunión / call / espacio",
        selling_to_role=(prospect.get("role") or "") if market == "b2b" else "",
    )
    return ColdBankRender(
        subject=subject,
        body=body,
        template_id=tid,
        market=market,
        channel=ch,
        kind=kind,
        index=idx,
        leverage_used=bool(lev) or bool(slots.get("retomo", "").strip()),
        reasoning=reasoning,
    )


def _producto_as_brand(template: str) -> str:
    """Marca = producto: la presentación ya lo nombra («Soy X de ButtereX»); no repetirlo en cada frase."""
    out = template
    for pat, rep in (
        (r"desde \{producto\} podemos", "podemos"),
        (r"lo que ofrece \{producto\}", "lo que ofrecemos"),
        (r"presentarte \{producto\}", "presentarte lo que hacemos"),
    ):
        out = re.sub(pat, rep, out)
    return out.replace("{producto}", "lo que hacemos")


_PLACEHOLDER_FILLERS = ("tu equipo", "tu rol")


def _ensure_role_and_company(body: str, *, slots: dict[str, str]) -> str:
    """Regla: el primer mensaje nombra rol y empresa del prospecto cuando los tenemos."""
    text = (body or "").strip()
    emp = (slots.get("empresa") or "").strip()
    rol = (slots.get("rol") or "").strip()
    emp = "" if emp.lower() in _PLACEHOLDER_FILLERS else emp
    rol = "" if rol.lower() in _PLACEHOLDER_FILLERS else rol
    marca = re.escape((slots.get("marca") or "").strip()) or r"\w+"
    soy = re.search(rf"(?:Soy [^.\n]+|Te escribo desde [^.\n]+|[^.\n]*\({marca}\))\.[ \t]*", text)
    low = (text[: soy.start()] + text[soy.end():]).lower() if soy else text.lower()
    # La pregunta de cierre se puede reemplazar después (horarios del Calendar): no cuenta.
    low = re.sub(r"¿[^¿?]*\?", " ", low)
    paras = [p for p in low.split("\n\n") if p.strip()]
    closing = re.compile(r"^\s*(saludos|un saludo|abrazo|gracias|slds|atte)")
    while paras and (closing.match(paras[-1]) or len(paras[-1].split()) <= 2):
        paras.pop()
    if paras and re.search(r"\b(call|llamada|reuni[oó]n|charla|videollamada|minutos)\b", paras[-1]):
        low = low.replace(paras[-1], " ")

    def _mentions(term: str) -> bool:
        return bool(re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)", low))

    missing_emp = bool(emp) and not _mentions(emp)
    missing_rol = bool(rol) and not _mentions(rol)
    if not text or not (missing_emp or missing_rol):
        return text
    if "vi que sos" in text.lower() or "vi que estás en" in text.lower() or "vi que estas en" in text.lower():
        return text
    if rol and emp:
        full = f"Vi que sos {rol} de {emp}."
    elif rol:
        full = f"Vi que sos {rol}."
    else:
        full = f"Vi que estás en {emp}."
    if missing_emp and missing_rol:
        line = full
    elif missing_rol:
        line = f"Vi que sos {rol}."
    else:
        line = f"Vi que estás en {emp}."

    if soy:
        head = text[: soy.end()].rstrip(" \t")
        tail = text[soy.end():]
        nxt = re.match(r"[^.!?\n]*[.!?][ \t]*", tail)
        if nxt:
            nxt_low = nxt.group(0).lower()
            if (
                any(f in nxt_low for f in _PLACEHOLDER_FILLERS)
                or (emp and emp.lower() in nxt_low)
                or (rol and rol.lower() in nxt_low)
                or nxt_low.lstrip().startswith("te escribo")
            ):
                tail = tail[nxt.end():]
                line = full
        if line.lower().startswith("te escribo") and "te escribo" in (head + tail).lower():
            line = re.sub(r"^Te escribo por tu lugar como", "Vi que sos", line)
            line = re.sub(r"^Te escribo por tu trabajo en", "Vi que estás en", line)
        if tail and not tail.startswith("\n"):
            tail = " " + tail.lstrip(" \t")
        return f"{head} {line}{tail}".rstrip()
    first, sep, rest = text.partition("\n")
    if not sep:
        return f"{first} {line}"
    return f"{first}\n\n{line} {rest.lstrip()}".rstrip()


def _ensure_hook_before_product(
    body: str, *, slots: dict[str, str], channel: Channel
) -> str:
    """Si el cuerpo va saludo → pitch de producto, inserta el hook."""
    text = (body or "").strip()
    if not text:
        return text
    hook = (slots.get("hook") or "").strip()
    if not hook:
        return text
    # Ya hay enganche típico.
    low = text.lower()
    if any(
        x in low
        for x in (
            "te escribo por",
            "te escribo porque",
            "te contacto",
            "pensé en",
            "creo que esto te puede",
            "creo que te puede",
            "vi lo que hacen",
            "vi tu",
            "vi ",
            "quería conectar",
            "quería comentarte",
            "llegué a vos",
            "me crucé",
            "me pareció",
            "estuve viendo",
            "estuve revisando",
            "por tu perfil",
            "por tu lugar",
            "por tu rol",
            "por tu trabajo",
            "entender",
            "contexto",
            "sin ir directo",
        )
    ):
        return text
    producto = (slots.get("producto") or "").strip()
    valor = (slots.get("valor") or "").strip()
    # Detectar "Hola X, soy Y. Con Producto…"
    m = re.match(
        r"^(Hola\s+[^.,!?]+[.,!]?\s*(?:Soy\s+[^.]+\.\s*)?)(Con\s+)",
        text,
        flags=re.I,
    )
    if m and producto and producto.lower() in text.lower()[:160]:
        return f"{m.group(1)}{hook} {m.group(2)}{text[m.end():]}"
    # LI/WA: valor pegado demasiado pronto.
    if channel in ("linkedin", "whatsapp") and valor and valor[:40].lower() in low[:160]:
        m2 = re.match(r"^(Hola\s+[^.]+\.\s*(?:Soy\s+[^.]+\.\s*)?)", text, flags=re.I)
        if m2 and hook.lower() not in low:
            return f"{m2.group(1)}{hook} {text[m2.end():]}"
    return text

def bank_counts() -> dict[str, int]:
    return {f"{m}-{c}-{k}": len(v) for (m, c, k), v in _BANKS.items()}
