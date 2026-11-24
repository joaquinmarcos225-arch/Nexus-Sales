"""Generación playbook SDR outbound — personalización + problema/beneficio + CTA a reunión."""

from __future__ import annotations

import json
import random
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import HTTPException

from app.schemas.mvp_outreach import SdrReasoningRead
from app.services import openai_service as oai

Channel = Literal["email", "linkedin", "whatsapp"]


class SdrDraftValidationError(Exception):
    """Borrador generado pero rechazado por reglas de validación SDR."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        super().__init__(report.get("summary") or "Borrador rechazado por validación")


class SdrResponseParseError(Exception):
    """Fallo al parsear la respuesta OpenAI (JSON inválido, tipo incorrecto o body vacío)."""

    def __init__(
        self,
        *,
        message: str,
        debug: dict[str, Any],
        salvage_body: str | None = None,
    ):
        self.message = message
        self.debug = debug
        self.salvage_body = salvage_body
        super().__init__(message)


@dataclass
class _ValidationAccum:
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    banned_matches: list[dict[str, str]] = field(default_factory=list)

    def extend(self, other: "_ValidationAccum") -> None:
        self.issues.extend(other.issues)
        self.warnings.extend(other.warnings)
        self.banned_matches.extend(other.banned_matches)


_EMAIL_DAY1_WORDS_MIN = 55
_EMAIL_DAY1_WORDS_MAX = 120
_EMAIL_DAY1_WORDS_IDEAL_MIN = 70
_EMAIL_DAY1_WORDS_IDEAL_MAX = 110

# Frases globales prohibidas (cualquier bloque).
_GLOBAL_BANNED = re.compile(r"(espero que est[eé]s bien)", re.I)

# Pitch de producto — NO confundir con "Soy Juan de Nexus" en presentación.
_PRODUCT_PITCH = re.compile(
    r"(\bnexus\s+(?:centraliza|automatiza|integra|permite|ofrece|es una)\b|"
    r"\b(?:nuestra|nuestro)\s+(?:plataforma|software|soluci[oó]n|herramienta)\b|"
    r"funcionalidades?(?:\s+(?:del|de la|de nuestro))?\s+(?:producto|plataforma|software|m[oó]dulo)|"
    r"demostraci[oó]n de la plataforma|soluci[oó]n integral|suite de)",
    re.I,
)

# Culpa / fricción directa hacia el prospecto — prohibido (sí se permite frame sectorial del producto).
_PAIN_ASSUMPTION_BANNED = re.compile(
    r"(seguramente\s+(?:te|le)\s+(?:pasa|ocurre|enfrent|tien)|"
    r"probablemente\s+(?:te|le)\s+(?:pasa|ocurre|enfrent|teng)|"
    r"¿(?:te|le)\s+(?:pasa|sucede|identific|reconoc)|"
    r"detect(?:ás|as)\s+esta\s+dificultad)",
    re.I,
)

# Follow-ups: nunca reprochar falta de lectura del mensaje anterior.
_GUILT_FOLLOWUP_BANNED = re.compile(
    r"(pudiste\s+(?:leer|revisar|ver)|"
    r"viste\s+(?:mi|el)\s+mensaje|"
    r"le[ií]ste\s+(?:mi|el)\s+(?:mensaje|correo|mail)|"
    r"revisaste\s+(?:mi|el|lo\s+que)|"
    r"si\s+(?:pudiste|viste|revisaste)\s+(?:leer|revisar|ver|lo))",
    re.I,
)

# Outbound: no medir interés residual (regla 6).
_INTEREST_CHECK_BANNED = re.compile(
    r"(segu[ií]s?\s+(?:con\s+ganas|interesad)|"
    r"sigue(?:s)?\s+(?:con\s+ganas|interesad)|"
    r"(?:todav[ií]a|a[uú]n)\s+(?:te\s+)?interesa|"
    r"te\s+sigue\s+interesando|"
    r"still\s+interested|"
    r"seguir\s+explorando)",
    re.I,
)

# Frame de problema/beneficio sectorial permitido (anclado a producto o research).
_SECTORAL_VALUE = re.compile(
    r"(habitualmente|en tu sector|en tu (?:rol|industria|empresa)|"
    r"(?:los|las)\s+(?:directores|equipos|l[ií]deres|gerentes)|"
    r"empresas (?:como|de tu)|"
    r"ayudamos a|pierden|recortar|reduc(?:ir|e)|aceler|"
    r"cuellos?\s+de\s+botella|tiempo\s+(?:manual|perdido)|"
    r"olvid[eé]\s+(?:mencionarte|comentarte))",
    re.I,
)

# Porcentajes / métricas inventadas (salvo que el producto las traiga explícitas).
_INVENTED_METRIC = re.compile(
    r"\b\d{1,3}\s*%|\b\d+\s*(?:x|veces)\b|aceler(?:ar|amos)\s+un\s+\d+",
    re.I,
)

# Ganchos que afirman hechos (crecimiento, inversión, noticias, hiring) — requieren evidencia del brief.
_FACT_HOOK_NEEDS_BRIEF = re.compile(
    r"(vi en linkedin|vi que .{0,40}crec|sigue creciendo|crecimiento de|"
    r"en estos d[ií]as|este trimestre|le[ií] que|me enter[eé] que|"
    r"ronda de inversi|serie [abc]\b|cerr[óo] (?:una )?inversi|levantaron capital|"
    r"contrat(?:aron|a)|buscan perfil|headcount|duplicar (?:el )?equipo|"
    r"noticia reciente|abri[óo] oficina)",
    re.I,
)

_BRIEF_EVIDENCE_STOP = frozenset(
    {
        "dato",
        "confirmado",
        "empresa",
        "prospecto",
        "producto",
        "contacto",
        "linkedin",
        "gancho",
        "sugerido",
        "persona",
        "contexto",
        "hallazgos",
        "evidencia",
        "explícita",
        "explícito",
        "personaliz",
        "personalizá",
        "inventar",
        "métricas",
        "metricas",
        "anclar",
        "sectorial",
        "beneficio",
        "prohibido",
        "usar",
        "solo",
        "claro",
        "modo",
        "industria",
        "contacto",
        "valor",
        "https",
    }
)


def _brief_evidence_tokens(*, brief: str, prospect_company: str) -> set[str]:
    company_tokens = {
        w for w in re.findall(r"[a-záéíóúñ]{3,}", (prospect_company or "").lower())
    }
    return {
        w
        for w in re.findall(r"[a-záéíóúñ]{5,}", (brief or "").lower())
        if w not in _BRIEF_EVIDENCE_STOP and w not in company_tokens
    }

# Puente contextual de follow-up (sin culpa) — incluye modo despedida suave.
_CONTEXT_BRIDGE = re.compile(
    r"(dejar esto arriba|paso r[aá]pido|olvid[eé]\s+(?:mencionarte|comentarte)|"
    r"te escribo brevemente|retomo|te escrib[ií]|te hab[ií]a escrito|"
    r"hace unos d[ií]as|por ac[aá] para|te escribo por aqu[ií]|seguimiento|"
    r"para ver si (?:logramos|podemos) coincidir|"
    r"[uú]ltima vez|dejo una idea|cuando te venga bien|"
    r"armamos una charla|agendamos unos minutos|te calza una charla|"
    r"quedo atento)",
    re.I,
)

# Lenguaje corporativo genérico / buzzwords sin mecanismo concreto del producto.
_GENERIC_CORPORATE = re.compile(
    r"(consolid(?:ar|a)\s+informaci[oó]n|centraliz(?:ar|a)\s+informaci[oó]n|"
    r"mejora(?:r)?\s+(?:la\s+)?eficiencia|optimiza(?:r)?\s+procesos|"
    r"datos relevantes|decisiones [áa]giles|potencia(?:r)?\s+productividad|"
    r"informaci[oó]n dispersa|visibilidad(?:\s+unificada)?|procesos m[aá]s eficientes|"
    r"alinear equipos|sinergias|transformaci[oó]n digital|best practices|"
    r"operaciones m[aá]s [áa]giles|toma de decisiones|datos confiables|"
    r"(?:menos|reduc(?:ir|e|imos)|baja(?:r)?)\s+fricci[oó]n(?:\s+operativa)?|"
    r"fricci[oó]n\s+operativa|sin\s+fricci[oó]n|"
    r"agiliza(?:r)?\s+(?:el\s+)?d[ií]a\s+a\s+d[ií]a|"
    r"mejorar\s+(?:la\s+)?operaci[oó]n|simplifica(?:r)?\s+procesos)",
    re.I,
)

_PRESENTATION_OK = re.compile(
    r"(soy\s+\S+|mi nombre es|te escribo desde|te hablo desde|escribo desde|desde\s+\S+)",
    re.I,
)

_GENERIC_CTA_BANNED = re.compile(
    r"(¿detectas esta dificultad|¿te sucede esto|¿qu[eé] opinas|¿te resuena|¿lo ves as[ií]|"
    r"¿est[aá] en el radar|¿identificas|¿reconoces|¿te pasa|¿es algo que hoy|"
    r"¿est[aá] entre tus prioridades|¿te resulta familiar|¿coincide con)",
    re.I,
)

_CONVERSATION_CTA = re.compile(
    r"(conversar|charla breve|coordinar una charla|explorarlo juntos|explorar juntos|"
    r"\d+\s*minutos|hablar brevemente|vale la pena|tendr[ií]a sentido|"
    r"coordinar.*(?:charla|conversaci[oó]n|reuni[oó]n|call)|¿te parecer[ií]a [úu]til|"
    r"conversaci[oó]n breve|quedar en contacto|hablar unos minutos|"
    r"reuni[oó]n breve|reuni[oó]n corta|coordinar una reuni[oó]n|mostrarles c[oó]mo)",
    re.I,
)

_GREETING_OK = re.compile(r"^(hola|buen d[ií]a|buenas)\b", re.I | re.M)

# Resultado/beneficio concreto — lenguaje simple (no solo jerga de ventas).
_CONCRETE_OUTCOME_MARKERS = re.compile(
    r"(?:"
    r"ayudamos?\s+a|logran?|obtienen?|consiguen?|genera(?:r|n)?(?:\s+m[aá]s)?|permite(?:n)?|"
    r"resultado|impacto|beneficio|"
    r"reduc(?:ir|e|en|imos)?|aument(?:ar|a|an)?|mejor(?:ar|a|an)?|aceler(?:ar|a|an)?|automatiz(?:ar|a|an)?|"
    r"centraliz(?:ar|a|amos|ación)?|"
    r"contactar(?:\s+m[aá]s)?|m[aá]s\s+prospectos|menos\s+tiempo|"
    r"tiempo\s+manual|dedicar\s+m[aá]s|conversaciones?\s+reales|"
    r"prospecci[oó]n|outreach|"
    r"oportunidades?\s+comerciales|m[aá]s\s+oportunidades|"
    r"carga\s+manual|sin\s+aumentar|"
    r"esto\s+les\s+permite|les\s+permite"
    r")",
    re.I,
)

_BENEFITS_MARKERS = _CONCRETE_OUTCOME_MARKERS

_FIRST_TOUCH_SECTION_KEYS = ("greeting", "presentation", "problem", "solution", "benefits", "cta")

_SDR_PLAYBOOK_SYSTEM = """
Sos un SDR outbound senior (español). Escribís mensajes personalizados, claros y breves.

OBJETIVO ÚNICO: agendar una reunión/llamada. NUNCA intentar cerrar la venta.
En el CTA NO digas minutos (ni 10 ni 15): pedí una llamada/reunión breve; el calendario fija la duración.

PERSONALIZACIÓN (lo que más vende hoy):
- Usá SIEMPRE la INVESTIGACIÓN PREVIA y/o datos CRM (empresa, rol, señales públicas).
- B2B: gancho sobre la EMPRESA o el equipo del prospecto.
- B2C: gancho sobre la PERSONA (perfil, rol, trayectoria).
- Si no hay dato confirmado: gancho suave con empresa/rol CRM. PROHIBIDO inventar hechos.
- PROHIBIDO inventar cómo «manejan» / «gestionan» documentos, procesos o el día a día
  («me llamó la atención cómo…») sin evidencia en el brief.

PRODUCTO DE CAMPAÑA (OBLIGATORIO):
Leé PRODUCTO SELECCIONADO. Problema + solución deben anclarse a ESE producto (dolor sectorial o beneficio real), no a features de relleno.

PRIMER TOQUE — estructura Nexus (default; NO la cambies salvo plantilla propia del SDR en Mi estilo):
1) Saludo: Hola [Nombre],
2) Presentación + ancla: Soy [SDR] de [marca]. Vi que sos [rol] de [empresa].
3) Producto: UNA explicación clara en voz «Nosotros…» (qué hace el producto). Sin rodeos.
4) CTA: una pregunta para agendar reunión/llamada (sin minutos; tono profesional, no «te viene bien»)
5) Firma SOLO en email: Saludos, [SDR]
WhatsApp: SIN firma. Nunca cierres con «Saludos,» ni el nombre al final.
PROHIBIDO: «No te escribo solo», «sin forzar un encaje», «conocer un poco el contexto», «me pareció que esto les podría servir». El producto se explica.

PROHIBIDO:
- Culpa directa ("seguramente te pasa", "¿te sucede esto?")
- Pitch de features / cerrar venta
- CTAs genéricos ("¿Qué opinas?", "¿Detectas esta dificultad?")
- Mensajes mucho más largos que los ejemplos del playbook
- CTA informal tipo «¿Te viene bien…?» (preferí «¿Te queda bien…?» / «¿Podemos coordinar…?»)

Texto plano. Sin markdown.
"""

_SDR_PLAYBOOK_FOLLOW_UP_SYSTEM = """
Sos un SDR outbound senior (español). Escribís follow-ups humanos, breves y con valor NUEVO.

REGLA DE ORO: NUNCA digas "¿pudiste leer/revisar mi mensaje anterior?" ni reproches falta de respuesta.
Eso genera culpa y fricción. El mensaje anterior es solo hilo conductor.

ESTRUCTURA DE SEGUIMIENTO (todos los canales):
1) OBLIGATORIO apalancarse del hilo previo («te había escrito por LinkedIn/mail/WhatsApp», «retomo…»). PROHIBIDO mensaje suelto.
2) Nuevo ángulo de valor: dato/beneficio distinto — NO repetir el pitch del Día 1
3) UN solo CTA de reunión (2 opciones blandas si no hay Calendar). PROHIBIDO dos CTAs.

REGLAS POR DÍA:
- Días 4/7/10/13: puente + valor nuevo + CTA reunión
- Día 16/19: break-up / cierre suave sin culpa; puerta abierta

PROHIBIDO:
- Reescribir el pitch completo del Día 1
- "¿Pudiste leer/revisar…?", "sin respuesta", "viste mi mensaje"
- Listar features o cerrar venta

Texto plano. Sin markdown. Más corto es mejor.
"""

_PRIOR_TOUCH_REFERENCE = re.compile(
    r"(te hab[ií]a escrito|te escrib[ií]|te envi[eé]|retomo|mis mensajes|mensaje anterior|"
    r"mensajes anteriores|seguimiento|hace unos d[ií]as|como coment[eé]|en mi (?:email|mensaje|correo)|"
    r"email que|correo que|sin respuesta|sin novedades|sin tu respuesta|que te envi[eé])",
    re.I,
)

_COORDINATE_CALL_CTA = re.compile(
    r"(coordinar|llamada breve|agenda|agendar|demo|esta semana|"
    r"otra persona|alguien m[aá]s del equipo|persona indicada|con qui[eé]n hablar)",
    re.I,
)

_HUMAN_FOLLOWUP_CTA = re.compile(
    r"(persona indicada|alguien m[aá]s del equipo|con qui[eé]n|"
    r"coordinar|llamada breve|agenda|agendar|demo|esta semana|"
    r"tiene sentido seguir|prefer[ií]s que|dej(?:emos|ar)lo para|lo dejamos para|dejamos para|"
    r"no est[aá] en agenda|evaluando para este a[nñ]o|"
    r"seguir conversando|seguimos conversando|dejar para m[aá]s adelante|"
    r"prioridad|prioridades|en el radar|seguir o|dejarlo para|revis)",
    re.I,
)

_EMAIL_FOLLOWUP_MARKERS = re.compile(
    r"(correo|email|escrib[ií].*d[ií]as|automatiz|prospecci[oó]n|cargando datos|"
    r"tareas manuales)",
    re.I,
)

_FUTURE_RECONTACT = re.compile(
    r"(trimestre|pr[oó]ximo\s+(?:trimestre|año|semestre)|m[aá]s adelante|"
    r"prioridades cambian|volver[eé] a escribir|última vez|última nota|no ocupar)",
    re.I,
)

_GENERIC_EMAIL_SUBJECTS = re.compile(
    r"^(seguimiento|consulta|ventas|una idea|operaciones|automatizaci[oó]n)$",
    re.I,
)

_VALUE_ADD_MARKERS = re.compile(
    r"(caso|cliente|empresa|dato|%|\d+\s*%|aprendizaje|insight|"
    r"estudio|tendencia|observamos|vimos que|logr[oó]|redujo|aument[oó]|"
    r"tiempo.*(?:operativ|prospecci[oó]n)|tareas operativas|conversaciones reales)",
    re.I,
)

_BREAKUP_MARKERS = re.compile(
    r"(cierro|no seguir ocupando|no ocupar|última vez|última nota|quedo a disposici[oó]n|"
    r"quedo atento|puerta abierta|no tuve respuesta|no tuvimos respuesta|"
    r"dejo esta conversaci[oó]n|saludos|m[aá]s adelante tiene sentido|otras prioridades)",
    re.I,
)

_RE_PITCH_STRUCTURE = re.compile(
    r"lo hacemos mediante|esto les permite|nuestro enfoque|"
    r"mediante nuestra|mediante nuestro|demostraci[oó]n de",
    re.I,
)

_WHY_WRITE_MARKERS = re.compile(
    r"(te escribo porque|escribo porque|te contacto porque|contacto porque|"
    r"ayudamos a|trabajamos con|nos dedicamos a|apoyamos a)",
    re.I,
)

_RESULT_MARKERS = _CONCRETE_OUTCOME_MARKERS

_WHAT_WE_DO_MARKERS = re.compile(
    r"(?:"
    r"lo hacemos|lo logramos|mediante|"
    r"nuestro (?:enfoque|proceso|m[eé]todo|producto|plataforma|software|herramienta)|"
    r"as[ií] (?:funciona|trabajamos|lo|es)|"
    r"utilizamos|automatizamos|centralizamos|conectamos|integramos|"
    r"ayudamos|ofrecemos|implementamos|"
    r"integra(?:mos|ci[oó]n)|con\s+(?:nuestra|nuestro)"
    r")",
    re.I,
)

# Funcionamiento concreto (herramienta, automatización, canales) — válido aunque no use la frase exacta "lo hacemos".
_HOW_IT_WORKS_MARKERS = re.compile(
    r"(?:"
    r"automatiz|consolid|centraliz|integr|conect|"
    r"plataforma|herramienta|software|producto|"
    r"mail|whatsapp|linkedin|email|"
    r"en un solo lugar|un solo (?:lugar|sitio)|"
    r"prospectos|campa[nñ]as|reporting|contacto|outreach"
    r")",
    re.I,
)


def _value_depth_for_touch(
    *,
    prior_touches: list[dict[str, Any]],
    step_day: int = 0,
) -> str:
    """Dosificación: full (1º) → chill (casi todos los FU) → minimal (último/breakup).

    Los FU intermedios DEBEN traer un ángulo nuevo (no solo retomo+CTA).
    Minimal solo en el cierre (día ≥16).
    """
    n = len(prior_touches or [])
    if n <= 0:
        return "full"
    if int(step_day or 0) >= 16:
        return "minimal"
    return "chill"


def _product_for_outreach_copy(product: dict[str, str] | None) -> dict[str, str]:
    """Copia del producto con nombre comercial corto para mensajes (no el título con |)."""
    from app.services.outreach_display_names import product_outreach_display_name

    raw = dict(product or {})
    full = str(raw.get("name") or "").strip()
    short = product_outreach_display_name(full) or full
    if short:
        raw["name"] = short
    if full and short and full != short:
        raw["full_name"] = full
    return raw


def _product_context_block(
    product: dict[str, str],
    *,
    value_depth: str = "full",
) -> str:
    if not product or not (
        product.get("name")
        or product.get("description")
        or product.get("original_description")
    ):
        return "PRODUCTO: no configurado — usá solo contexto de campaña.\n\n"
    from app.services.outreach_display_names import product_outreach_display_name

    full_name = str(product.get("full_name") or product.get("name") or "").strip()
    short_name = product_outreach_display_name(product.get("name")) or full_name or "—"
    orig = (product.get("original_description") or product.get("description") or "—")[:2400]
    summary = (product.get("interpreted_summary") or product.get("value_proposition") or "—")[:1200]
    problems = (product.get("extracted_problems") or product.get("pain_points") or "—")[:1200]
    benefits = (product.get("extracted_benefits") or product.get("benefits") or "—")[:1200]
    lines = [
        "PRODUCTO SELECCIONADO EN CAMPAÑA (referencia interna — fuente de verdad):",
        f"Nombre comercial (usar en el mensaje, máx. 1 vez): {short_name}",
    ]
    if full_name and full_name != short_name:
        lines.append(f"Título completo en ficha (NO pegar en el mensaje): {full_name}")
    lines.extend(
        [
            f"Descripción original:\n{orig}",
            f"Resumen interpretado (propuesta de valor):\n{summary}",
            f"Problemas que resuelve (extraídos):\n{problems}",
            f"Beneficios que aporta (extraídos):\n{benefits}",
            "PROHIBIDO vender otro producto (p. ej. Nexus Sales / outbound genérico) "
            "si no está en ESTA ficha. Solo hechos de este producto.",
        ]
    )
    depth = (value_depth or "full").strip().lower()
    if depth == "minimal":
        lines.append(
            "MODO FOLLOW-UP TARDÍO (casi no explicar producto):\n"
            "- El pitch ya se dio. PROHIBIDO re-explicar qué hace el producto.\n"
            "- PROHIBIDO pegar de nuevo la propuesta de valor / description.\n"
            "- Retomá el hilo de toques previos + CTA. Como mucho media idea NUEVA "
            "(nunca repetida antes con esta persona) si aporta; si no, cero explicación.\n"
            "- NUNCA inventar hechos fuera de la ficha. NUNCA reproches falta de lectura."
        )
    elif depth == "chill":
        lines.append(
            "MODO FOLLOW-UP (con valor nuevo, chill):\n"
            "- El 1º toque ya explicó el producto: NO re-pegues el pitch completo.\n"
            "- OBLIGATORIO: UNA idea corta / ángulo NUEVO de la ficha "
            "(beneficio o dato distinto al de toques previos), en una oración. "
            "PROHIBIDO FU vacío tipo solo «te escribí por X · ¿agendamos?».\n"
            "- Cada dato de valor = una sola vez. No inventar hechos fuera de la ficha.\n"
            "- PROHIBIDO copiar value_proposition o description casi literal.\n"
            "- NUNCA reproches falta de lectura."
        )
    else:
        lines.append(
            "MODO PRIMER TOQUE (explicar bien; no escasear):\n"
            "Usá esta info para: (1) un gancho personalizado con investigación/CRM, "
            "(2) el problema/necesidad sectorial o de rol que el producto resuelve, "
            "(3) la solución/beneficio concreto. PROHIBIDO inventar hechos no respaldados. "
            "PROHIBIDO cerrar la venta — solo pedir reunión corta.\n"
            "VALOR OBLIGATORIO: en solution/benefits (o el bloque único de valor) REESCRIBÍ "
            "en tus palabras (voz nosotros / conversacional) los hechos concretos de la ficha "
            "(%, canales, resultado medible). "
            "PROHIBIDO copiar value_proposition o description casi literal. "
            "PROHIBIDO «Con {nombre} incrementa/automatiza…» — conjugá «incrementamos/automatizamos». "
            "PROHIBIDO buzzwords vacíos («menos fricción operativa», «mejorar eficiencia», "
            "«optimizar procesos») si no sostienen el valor real de la ficha."
        )
    return "\n".join(lines) + "\n\n"


def _role_context_block(prospect: dict[str, str], campaign: dict[str, str]) -> str:
    from app.schemas.mvp_outreach import RoleAlignmentRead
    from app.services.lead_sourcing.role_alignment import assess_role_alignment, role_block_for_prompt

    icp = prospect.get("icp_target_role") or campaign.get("target_role") or ""
    actual = prospect.get("prospect_actual_role") or prospect.get("role") or ""
    selling = prospect.get("selling_to_role") or ""
    warning = prospect.get("role_warning") or ""
    if selling or warning:
        alignment = RoleAlignmentRead(
            icp_target_role=icp,
            prospect_actual_role=actual,
            selling_to_role=selling,
            selling_rationale="",
            warning=warning or None,
            alignment_level=prospect.get("role_alignment_level") or "unknown",  # type: ignore[arg-type]
            match_score=0,
            aligned=prospect.get("role_alignment_level") == "match",
        )
    else:
        alignment = assess_role_alignment(icp, actual)
    return role_block_for_prompt(alignment)


def _collect_pattern_matches(
    text: str,
    field: str,
    pattern: re.Pattern[str],
    rule: str,
) -> _ValidationAccum:
    acc = _ValidationAccum()
    if not text:
        return acc
    for m in pattern.finditer(text):
        phrase = m.group(0)
        acc.banned_matches.append({"field": field, "rule": rule, "phrase": phrase})
        acc.issues.append(f'{field}: {rule} — "{phrase}"')
    return acc


_INTERNAL_BLOCK_KEYS = (
    "probable_problem",
    "why_it_matters",
    "hypothesis",
    "response_question",
    "selling_to_role",
)


def _first_touch_outcome_context(
    *,
    internal: dict[str, Any] | None,
    sections: dict[str, Any] | None,
    body: str,
) -> str:
    """Texto combinado donde puede aparecer el resultado (para no fallar 3 veces por el mismo gap)."""
    parts: list[str] = []
    if internal and isinstance(internal, dict):
        parts.append(str(internal.get("probable_problem") or ""))
    if sections and isinstance(sections, dict):
        parts.extend(
            str(sections.get(k) or "")
            for k in ("problem", "solution", "benefits")
        )
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    if len(paragraphs) >= 2:
        parts.append(paragraphs[1])
    if len(paragraphs) >= 4:
        parts.append(paragraphs[3])
    return " ".join(p for p in parts if p).strip()


def _mentions_concrete_outcome(text: str, *, outcome_context: str = "") -> bool:
    blob = f"{text} {outcome_context}".strip()
    return bool(blob and _CONCRETE_OUTCOME_MARKERS.search(blob))


def _first_touch_how_context(
    *,
    internal: dict[str, Any] | None,
    sections: dict[str, Any] | None,
    body: str,
) -> str:
    parts: list[str] = []
    if sections and isinstance(sections, dict):
        parts.append(str(sections.get("solution") or ""))
    if internal and isinstance(internal, dict):
        parts.append(str(internal.get("hypothesis") or ""))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    if len(paragraphs) >= 3:
        parts.append(paragraphs[2])
    return " ".join(p for p in parts if p).strip()


def _mentions_how_we_do_it(text: str, *, how_context: str = "") -> bool:
    blob = f"{text} {how_context}".strip()
    if not blob:
        return False
    if _WHAT_WE_DO_MARKERS.search(blob):
        return True
    return bool(_HOW_IT_WORKS_MARKERS.search(blob) and len(blob) >= 35)


def debug_how_we_do_validation(
    text: str,
    *,
    field: str = "sections.solution",
    how_context: str = "",
) -> dict[str, Any]:
    """Traza PASS/FAIL del validador «cómo lo hacemos» (sin llamar a la IA)."""
    blob = f"{text} {how_context}".strip()
    what_hits = [m.group(0) for m in _WHAT_WE_DO_MARKERS.finditer(blob)]
    how_hits = [m.group(0) for m in _HOW_IT_WORKS_MARKERS.finditer(blob)]
    corp_hits = [m.group(0) for m in _GENERIC_CORPORATE.finditer(text)]
    mentions = _mentions_how_we_do_it(text, how_context=how_context)
    accum = _validate_solution_text(text, field, how_context=how_context)
    checks: list[dict[str, Any]] = [
        {
            "name": "_GLOBAL_BANNED / _PAIN_ASSUMPTION",
            "ok": not any(
                p.search(text)
                for p in (_GLOBAL_BANNED, _PAIN_ASSUMPTION_BANNED)
            ),
        },
        {
            "name": "_GENERIC_CORPORATE",
            "ok": not corp_hits,
            "matches": corp_hits,
        },
        {
            "name": "_WHAT_WE_DO_MARKERS",
            "ok": bool(what_hits),
            "matches": what_hits,
            "pattern": _WHAT_WE_DO_MARKERS.pattern,
        },
        {
            "name": "_HOW_IT_WORKS_MARKERS (fallback, len>=35)",
            "ok": bool(how_hits) and len(blob) >= 35,
            "matches": how_hits,
            "pattern": _HOW_IT_WORKS_MARKERS.pattern,
        },
        {
            "name": "_mentions_how_we_do_it()",
            "ok": mentions,
            "rule": "PASS si WHAT_WE_DO matchea blob, o HOW_IT_WORKS matchea y len(blob)>=35",
        },
        {
            "name": "_validate_solution_text()",
            "ok": not accum.issues,
            "issues": list(accum.issues),
        },
    ]
    failed = [c for c in checks if not c["ok"]]
    return {
        "field": field,
        "text": text,
        "text_len": len(text),
        "how_context_len": len(how_context),
        "result": "PASS" if not failed and not accum.issues else "FAIL",
        "fail_reason": failed[0]["name"] if failed else (accum.issues[0] if accum.issues else None),
        "checks": checks,
        "functions": [
            "_validate_solution_text() -> sections.solution / body.qué_hacemos",
            "_validate_internal() -> internal.hypothesis",
            "_build_first_touch_block_checklist() -> checkmark UI (atribuye issues por regex)",
        ],
    }


_FIRST_TOUCH_BLOCK_DEFS: tuple[tuple[str, str], ...] = (
    ("greeting", "saludo"),
    ("presentation", "presentación"),
    ("problem", "por qué escribo"),
    ("solution", "cómo lo hacemos"),
    ("benefits", "resultado / beneficio"),
    ("cta", "CTA"),
)

_BLOCK_ISSUE_PATTERNS: dict[str, re.Pattern[str]] = {
    "greeting": re.compile(r"greeting|saludo", re.I),
    "presentation": re.compile(r"presentation|presentaci[oó]n", re.I),
    "problem": re.compile(r"sections\.problem|por_qu[eé]_escribo|why_it_matters|why_write", re.I),
    "solution": re.compile(r"sections\.solution|qu[eé]_hacemos|internal\.hypothesis|hypothesis", re.I),
    "benefits": re.compile(
        r"sections\.benefits|body\.resultado|internal\.probable_problem|probable_problem|beneficio",
        re.I,
    ),
    "cta": re.compile(
        r"sections\.cta|body\.cta|internal\.response_question|response_question|"
        r"primer toque:.*(?:CTA|reuni[oó]n|conversaci[oó]n)|CTA debe",
        re.I,
    ),
}


def _first_touch_block_value(
    key: str,
    *,
    sections: dict[str, Any],
    internal: dict[str, Any],
) -> str:
    if key == "greeting":
        return str(sections.get("greeting") or "").strip()
    if key == "presentation":
        return str(sections.get("presentation") or "").strip()
    if key == "problem":
        return str(sections.get("problem") or internal.get("why_it_matters") or "").strip()
    if key == "solution":
        return str(sections.get("solution") or internal.get("hypothesis") or "").strip()
    if key == "benefits":
        return str(sections.get("benefits") or internal.get("probable_problem") or "").strip()
    if key == "cta":
        return str(sections.get("cta") or internal.get("response_question") or "").strip()
    return ""


def _issue_targets_block(issue: str, block_key: str) -> bool:
    pattern = _BLOCK_ISSUE_PATTERNS.get(block_key)
    return bool(pattern and pattern.search(issue))


def _build_first_touch_block_checklist(
    *,
    sections: dict[str, Any] | None,
    internal: dict[str, Any] | None,
    issues: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    sections = sections if isinstance(sections, dict) else {}
    internal = internal if isinstance(internal, dict) else {}
    checklist: list[dict[str, Any]] = []
    missing: list[str] = []
    for key, label in _FIRST_TOUCH_BLOCK_DEFS:
        value = _first_touch_block_value(key, sections=sections, internal=internal)
        block_issues = [i for i in issues if _issue_targets_block(i, key)]
        if key == "solution":
            solution_section = str(sections.get("solution") or "").strip()
            if solution_section and _mentions_how_we_do_it(solution_section):
                # sections.solution válido: no penalizar por internal.hypothesis ni body.qué_hacemos
                # (el checklist muestra sections.solution pero body puede partir párrafos distinto)
                block_issues = [
                    i
                    for i in block_issues
                    if not re.search(r"internal\.hypothesis|body\.qu[eé]_hacemos", i, re.I)
                ]
        has_content = len(value) >= 8
        if key == "solution" and not has_content:
            has_content = _mentions_how_we_do_it(value, how_context=value)
        ok = has_content and not block_issues
        issue_text = block_issues[0] if block_issues else None
        if not ok:
            if not has_content:
                missing.append(f"falta {label}")
            elif issue_text:
                missing.append(issue_text)
            else:
                missing.append(f"bloque «{label}» incumple validación")
        checklist.append(
            {
                "key": key,
                "label": label,
                "ok": ok,
                "value": value,
                "issue": issue_text,
            }
        )
    return checklist, missing


def _validation_report(
    *,
    channel: Channel,
    step_day: int,
    body: str,
    subject: str | None,
    sections: dict[str, Any] | None,
    internal: dict[str, Any] | None = None,
    accum: _ValidationAccum,
    attempts: int,
    generation_debug: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sections_out: dict[str, str] | None = None
    if sections and isinstance(sections, dict):
        sections_out = {
            k: str(sections.get(k) or "").strip()
            for k in _FIRST_TOUCH_SECTION_KEYS
        }
    internal_out: dict[str, str] | None = None
    if internal and isinstance(internal, dict):
        internal_out = {
            k: str(internal.get(k) or "").strip()
            for k in _INTERNAL_BLOCK_KEYS
        }
    issues = list(accum.issues)
    warnings = list(accum.warnings)
    block_checklist: list[dict[str, Any]] = []
    missing_blocks: list[str] = []
    how_we_do_trace: dict[str, Any] | None = None
    if step_day == 1:
        block_checklist, missing_blocks = _build_first_touch_block_checklist(
            sections=sections if isinstance(sections, dict) else None,
            internal=internal if isinstance(internal, dict) else None,
            issues=issues,
        )
        solution_text = ""
        if sections and isinstance(sections, dict):
            solution_text = str(sections.get("solution") or "").strip()
        if not solution_text and internal and isinstance(internal, dict):
            solution_text = str(internal.get("hypothesis") or "").strip()
        if solution_text:
            how_we_do_trace = debug_how_we_do_validation(
                solution_text,
                field="sections.solution",
                how_context=_first_touch_how_context(
                    internal=internal if isinstance(internal, dict) else None,
                    sections=sections if isinstance(sections, dict) else None,
                    body=body,
                ),
            )
            sol_item = next((b for b in block_checklist if b.get("key") == "solution"), None)
            if sol_item:
                how_we_do_trace["checklist_ok"] = sol_item.get("ok")
                how_we_do_trace["checklist_issue"] = sol_item.get("issue")
                how_we_do_trace["checklist_value_source"] = (
                    "sections.solution"
                    if sections and str(sections.get("solution") or "").strip()
                    else "internal.hypothesis"
                )
                solution_block_issues = [
                    i for i in issues if _issue_targets_block(i, "solution")
                ]
                how_we_do_trace["issues_attributed_to_solution_block"] = solution_block_issues
    return {
        "valid": False,
        "summary": "; ".join(issues) if issues else "Borrador rechazado por validación",
        "issues": issues,
        "warnings": warnings,
        "word_count": _word_count(body) if channel == "email" else None,
        "char_count": len(body),
        "rejected_subject": subject,
        "rejected_body": body,
        "rejected_sections": sections_out,
        "rejected_internal": internal_out,
        "block_checklist": block_checklist,
        "missing_blocks": missing_blocks,
        "how_we_do_trace": how_we_do_trace,
        "banned_matches": accum.banned_matches,
        "channel": channel,
        "step_day": step_day,
        "attempts": attempts,
        "generation_debug": generation_debug,
    }


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or "", flags=re.UNICODE))


def _validate_base_text(text: str, field: str) -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_collect_pattern_matches(text, field, _GLOBAL_BANNED, "frase prohibida"))
    acc.extend(
        _collect_pattern_matches(
            text, field, _PAIN_ASSUMPTION_BANNED, "culpa/fricción directa hacia el prospecto"
        )
    )
    acc.extend(
        _collect_pattern_matches(
            text, field, _GUILT_FOLLOWUP_BANNED, "reproche de lectura/respuesta prohibido"
        )
    )
    return acc


def _validate_pitch_text(text: str, field: str) -> _ValidationAccum:
    acc = _validate_base_text(text, field)
    acc.extend(_collect_pattern_matches(text, field, _PRODUCT_PITCH, "pitch de producto"))
    return acc


def _validate_why_write_text(
    text: str,
    field: str,
    prospect: dict[str, str],
    campaign: dict[str, str],
) -> _ValidationAccum:
    del prospect, campaign
    acc = _ValidationAccum()
    acc.extend(_validate_base_text(text, field))
    acc.extend(_collect_pattern_matches(text, field, _GENERIC_CORPORATE, "lenguaje corporativo genérico"))
    if text and not (
        _WHY_WRITE_MARKERS.search(text)
        or _SECTORAL_VALUE.search(text)
        or _RESULT_MARKERS.search(text)
    ):
        acc.issues.append(
            f"{field}: debe plantear el problema/necesidad o el beneficio que resolvés "
            f'(ej. "Habitualmente los [rol]…" / "Ayudamos a…")'
        )
    return acc


def _validate_solution_text(text: str, field: str, *, how_context: str = "") -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_validate_base_text(text, field))
    acc.extend(_collect_pattern_matches(text, field, _GENERIC_CORPORATE, "explicación genérica/corporativa"))
    if text and not (
        _mentions_how_we_do_it(text, how_context=how_context)
        or _RESULT_MARKERS.search(text)
        or _SECTORAL_VALUE.search(text)
    ):
        acc.issues.append(
            f"{field}: debe explicar la solución/beneficio concreto del producto "
            f'(ej. "Ayudamos a… a recortar…", "Automatizamos…")'
        )
    return acc


def _validate_banned_text(text: str, field: str) -> _ValidationAccum:
    return _validate_pitch_text(text, field)


def _is_first_touch(prior_touches: list[dict[str, Any]]) -> bool:
    return not prior_touches


def _has_followup_bridge(body: str) -> bool:
    return bool(_PRIOR_TOUCH_REFERENCE.search(body) or _CONTEXT_BRIDGE.search(body))


def _prior_touches_block(prior: list[dict[str, Any]]) -> str:
    if not prior:
        return "HISTORIAL: primer contacto — no hay toques anteriores.\n\n"

    lines = [
        "HISTORIAL COMPLETO DE TOQUES SIN RESPUESTA.",
        "La secuencia EVOLUCIONA: el pitch completo del producto ya fue en el primer toque.",
        "Usá un puente contextual corto + ángulo NUEVO de valor. NUNCA reproches falta de lectura.",
        "NUNCA actúes como si el contacto fuera completamente nuevo.\n",
    ]
    for t in prior:
        if not isinstance(t, dict):
            continue
        body = (t.get("body") or "").strip()
        if not body:
            continue
        day = t.get("day") or "?"
        ch = t.get("channel") or "?"
        subj = (t.get("subject") or "").strip()
        header = f"--- Día {day} · {ch}"
        if subj:
            header += f" · Asunto: {subj}"
        header += " ---"
        lines.append(f"{header}\n{body[:1000]}")
    return "\n\n".join(lines) + "\n\n"


def _first_touch_structure_block(
    *,
    channel: Channel,
    sender_name: str,
    brand_name: str,
    prospect_first_name: str,
    prospect_role: str = "",
    prospect_industry: str = "",
    prospect_company: str = "",
    prospect_id: int | str | None = None,
    campaign_id: int | str | None = None,
) -> str:
    from app.services.message_structure_variants import (
        first_touch_structure_prompt,
        pick_first_touch_variant,
    )
    from app.services.nexus_outreach_playbook_approved import approved_playbook_reference
    from app.services.outreach_display_names import first_real_name_token, is_placeholder_token

    sender = first_real_name_token(sender_name, fallback="") or "[nombre SDR]"
    if is_placeholder_token(sender):
        sender = "[nombre SDR]"
    brand = brand_name.strip() or "[empresa/producto]"
    first = (prospect_first_name or "").strip()
    if not first or is_placeholder_token(first):
        first = "[Nombre]"
    role = prospect_role.strip() or prospect_industry.strip() or ""
    approved = approved_playbook_reference(step_day=1, channel=channel)
    variant = pick_first_touch_variant(
        channel=channel,
        prospect_id=prospect_id,
        campaign_id=campaign_id,
    )
    shape = first_touch_structure_prompt(
        channel=channel,
        variant=variant,
        sender_name=sender,
        brand_name=brand,
        prospect_first_name=first,
        prospect_role=role,
        prospect_company=prospect_company,
    )
    return (
        f"{approved}\n{shape}\n"
        "Completá sections JSON. body ensamblado = greeting + presentation + "
        "bloques de valor (problem/solution/benefits según variante) + cta (+ firma email).\n"
        "internal.probable_problem = el ángulo de valor del mensaje (anclado al producto).\n"
    )


def _first_touch_value_paragraph(sections: dict[str, Any]) -> str:
    """Párrafos de valor separados (problema / cómo / resultado)."""
    blocks: list[str] = []
    for key in ("problem", "solution", "benefits"):
        val = str(sections.get(key) or "").strip()
        if not val:
            continue
        blocks.append(val if val.endswith((".", "?", "!")) else f"{val}.")
    return "\n\n".join(blocks)


def _assemble_first_touch_body(sections: dict[str, Any]) -> str:
    from app.services.outbound_text_normalize import normalize_outbound_email_body

    opening: list[str] = []
    for key in ("greeting", "presentation"):
        val = str(sections.get(key) or "").strip()
        if val:
            opening.append(val)
    parts: list[str] = []
    if opening:
        # Hola X,\nSoy Y de Z.  (saludo + presentación juntos)
        parts.append("\n".join(opening))
    value = _first_touch_value_paragraph(sections)
    if value:
        parts.append(value)
    cta = str(sections.get("cta") or "").strip()
    if cta:
        parts.append(cta)
    return normalize_outbound_email_body("\n\n".join(parts))


def _follow_up_structure_block(
    *,
    step_day: int,
    channel: Channel,
    prospect_id: int | str | None = None,
    campaign_id: int | str | None = None,
    value_depth: str = "chill",
) -> str:
    from app.services.message_structure_variants import (
        follow_up_structure_prompt,
        pick_follow_up_variant,
    )
    from app.services.nexus_outreach_playbook_approved import approved_playbook_reference

    approved = approved_playbook_reference(step_day=step_day, channel=channel)
    variant = pick_follow_up_variant(
        channel=channel,
        prospect_id=prospect_id,
        campaign_id=campaign_id,
        step_day=step_day,
    )
    shape = follow_up_structure_prompt(
        channel=channel,
        variant=variant,
        step_day=step_day,
        value_depth=value_depth,
    )
    return f"{approved}\n{shape}\n"


def _channel_rules(
    channel: Channel,
    *,
    first_touch: bool,
    step_day: int = 1,
    value_depth: str = "full",
) -> str:
    if first_touch or value_depth == "full":
        if channel == "email":
            return (
                "CANAL Email primer toque: body ideal 70-110 palabras. "
                "Subject con ángulo corto de VALOR del producto "
                "(ej. «Automatizá tu outbound» / «Propuesta breve: {gancho}»). "
                "PROHIBIDO asunto genérico tipo solo «Propuesta breve para {empresa}». "
                "Explicá bien el valor en el body (no escasear). "
                "Bloques grandes editables (sin plantilla de audiencia fija) + CTA reunión.\n"
            )
        if channel == "linkedin":
            return (
                "CANAL LinkedIn primer toque: largo INTERMEDIO (~260-420 chars, máx ~480). "
                "Sin subject. OBLIGATORIO: «Hola Ana, ¿cómo estás?» en el mismo renglón; "
                "nombrar rol + empresa del prospecto; "
                "1–2 oraciones con lo más impactante del producto (explicar bien); CTA reunión. "
                "Dividí en párrafos cortos (línea en blanco). "
                "PROHIBIDO un solo bloque enorme. PROHIBIDO pegar ficha de producto.\n"
            )
        return (
            "CANAL WhatsApp primer toque: MÁS CORTO que LinkedIn. "
            "Ideal 20-35 palabras (máx ~45 / ~260 caracteres). "
            "Informal, chill, según el país de Mi estilo. Dividí en 2–3 micro-párrafos "
            "(saludo; idea de valor corta pero clara; CTA). PROHIBIDO párrafo muro. CTA reunión corta.\n"
        )
    if value_depth == "minimal":
        if channel == "email":
            return (
                "CANAL Email follow-up TARDÍO: casi sin explicar producto. "
                "Retomá hilo + CTA. PROHIBIDO re-pitch. Preferí Re:. 40-70 palabras.\n"
            )
        if channel == "linkedin":
            return (
                "CANAL LinkedIn follow-up TARDÍO: casi sin explicar producto. "
                "Retomá hilo + CTA. PROHIBIDO re-pitch. Sin subject.\n"
            )
        return (
            "CANAL WhatsApp follow-up TARDÍO: ultra corto, casi sin producto. "
            "Retomá hilo + CTA. PROHIBIDO re-pitch. 2 micro-párrafos máx.\n"
        )
    if channel == "email":
        return (
            "CANAL Email follow-up TEMPRANO: sin re-pitch completo; "
            "como mucho UNA idea de valor corta/chill + CTA. Preferí Re:. 50-90 palabras.\n"
        )
    if channel == "linkedin":
        return (
            "CANAL LinkedIn follow-up TEMPRANO: corto (2 párrafos breves), "
            "sin re-pitch completo; como mucho UNA idea chill + CTA. Sin subject.\n"
        )
    return (
        "CANAL WhatsApp follow-up TEMPRANO: ultra corto, chill, 2 micro-párrafos máx; "
        "sin re-pitch completo; como mucho media idea de valor + CTA.\n"
    )


def _structure_instructions(
    *,
    channel: Channel,
    step_day: int,
    prior_touches: list[dict[str, Any]],
    campaign: dict[str, str],
    prospect: dict[str, str],
) -> str:
    prospect_id = prospect.get("id")
    campaign_id = campaign.get("id") or campaign.get("campaign_id")
    if _is_first_touch(prior_touches):
        from app.services.outreach_display_names import prospect_greeting_name, sender_first_name

        first = prospect_greeting_name(prospect)
        return _first_touch_structure_block(
            channel=channel,
            sender_name=sender_first_name(
                campaign_sender=str(campaign.get("sender_name") or ""),
                fallback="",
            ),
            brand_name=str(campaign.get("brand_name") or ""),
            prospect_first_name=first,
            prospect_role=str(prospect.get("role") or ""),
            prospect_industry=str(prospect.get("industry") or ""),
            prospect_company=str(prospect.get("company_name") or ""),
            prospect_id=prospect_id,
            campaign_id=campaign_id,
        )
    return _follow_up_structure_block(
        step_day=step_day,
        channel=channel,
        prospect_id=prospect_id,
        campaign_id=campaign_id,
        value_depth=_value_depth_for_touch(
            prior_touches=prior_touches, step_day=step_day
        ),
    )


def _build_user_prompt(
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    education: str,
    step_day: int,
    step_objective: str,
    prior_touches: list[dict[str, Any]],
    tone: str,
) -> str:
    pname = (prospect.get("name") or "").strip()
    from app.services.outreach_display_names import outreach_company_display, prospect_greeting_name, sender_first_name

    first = prospect_greeting_name(prospect) or "ahí"
    first_touch = _is_first_touch(prior_touches)
    value_depth = _value_depth_for_touch(
        prior_touches=prior_touches, step_day=step_day
    )
    sender = sender_first_name(
        campaign_sender=str(campaign.get("sender_name") or ""),
        fallback="equipo",
    )
    brand = outreach_company_display(
        campaign.get("brand_name") or campaign.get("seller_company_name")
    ) or "nuestro equipo"
    structure = _structure_instructions(
        channel=channel,
        step_day=step_day,
        prior_touches=prior_touches,
        campaign=campaign,
        prospect=prospect,
    )
    product_block = _product_context_block(product, value_depth=value_depth)
    role_block = _role_context_block(prospect, campaign)
    edu_block = f"{education[:1400]}\n\n" if (education or "").strip() else ""
    research = (prospect.get("research_brief") or "").strip()
    # Evitar duplicar si education ya trae el brief.
    research_block = ""
    if research and research[:80] not in (education or ""):
        research_block = f"{research[:1200]}\n\n"
    from app.services.sequence_cta_guard import prior_ctas_for_prompt
    from app.services.sequence_close_guard import close_rules_for_prompt
    from app.services.sequence_interest_check_guard import interest_check_rules_for_prompt
    from app.services.sequence_value_once_guard import value_once_rules_for_prompt
    from app.services.sequence_email_subject_guard import email_subject_rules_for_prompt
    from app.services.sequence_product_name_guard import product_name_rules_for_prompt
    from app.services.sequence_cold_bank_guard import cold_bank_rules_for_prompt
    from app.services.sequence_anchor_bridge_guard import anchor_bridge_rules_for_prompt
    from app.services.sequence_followup_leverage_guard import (
        followup_leverage_rules_for_prompt,
    )
    from app.services.sequence_structure_order_guard import (
        structure_order_rules_for_prompt,
    )

    cta_rule_block = prior_ctas_for_prompt(prior_touches)
    close_rule_block = close_rules_for_prompt(prior_touches)
    rules_extra = interest_check_rules_for_prompt()
    rules_extra += cold_bank_rules_for_prompt(sequence_first_touch=first_touch)
    rules_extra += anchor_bridge_rules_for_prompt(sequence_first_touch=first_touch)
    rules_extra += followup_leverage_rules_for_prompt(sequence_first_touch=first_touch)
    rules_extra += structure_order_rules_for_prompt()
    rules_extra += (
        "REGLA DURA CTA: UN solo CTA de reunión por mensaje. "
        "PROHIBIDO dos pedidos de reunión seguidos.\n"
    )
    if channel == "email":
        rules_extra += email_subject_rules_for_prompt(product)
    rules_extra += product_name_rules_for_prompt(
        product_name=str((product or {}).get("name") or ""),
        prior_touches=prior_touches,
        sequence_first_touch=first_touch,
    )
    if not first_touch:
        rules_extra += value_once_rules_for_prompt(prior_touches)
    if cta_rule_block:
        rules_extra += cta_rule_block.rstrip() + "\n"
    if close_rule_block:
        rules_extra += close_rule_block.rstrip() + "\n"
    return (
        f"Playbook Día {step_day}. Objetivo: {step_objective}\n"
        f"DOSIFICACIÓN DE VALOR: {value_depth} "
        f"(full=explicar bien en 1º; chill=FU temprano una idea; minimal=FU tardío casi sin explicar).\n"
        + (
            ""
            if first_touch
            else (
                "REGLA DURA FOLLOW-UP: PROHIBIDO «Vi que sos…», «Vi tu lugar como…» "
                "u anclas de rol/empresa. Apalancate del hilo anterior.\n"
            )
        )
        + rules_extra
        + f"{_channel_rules(channel, first_touch=first_touch, step_day=step_day, value_depth=value_depth)}\n"
        f"{structure}\n"
        f"Remitente SDR: {sender} · Empresa/producto: {brand}\n"
        f"Tono: {tone or campaign.get('tone') or 'profesional cercano'}\n\n"
        f"{role_block}"
        f"{product_block}"
        f"{edu_block}"
        f"{research_block}"
        f"Prospecto: {pname} ({first}) | {prospect.get('role')} @ {prospect.get('company_name')}\n"
        f"Industria: {prospect.get('industry') or '—'} | Web: {prospect.get('website') or '—'}\n\n"
        f"{oai._mvp_prospect_context_block(prospect)}"
        f"ICP campaña: {campaign.get('target_industry')} | rol ICP: {campaign.get('target_role')}\n"
        f"Cargo real contacto: {prospect.get('prospect_actual_role') or prospect.get('role') or '—'}\n"
        f"Rol al que vendés: {prospect.get('selling_to_role') or prospect.get('role') or '—'}\n\n"
        f"{_prior_touches_block(prior_touches)}"
        + (
            "CAMPOS JSON — primer toque:\n"
            "internal (razonamiento, no va al mensaje):\n"
            "  probable_problem = problema/necesidad sectorial o beneficio que resolvés (anclado al producto)\n"
            "  why_it_matters = por qué es relevante para su rol/empresa (con research si hay)\n"
            "  hypothesis = cómo lo resolvés / beneficio concreto (breve)\n"
            "  response_question = CTA de reunión corta\n"
            "sections (van al mensaje):\n"
            "  greeting = Hola [Nombre],\n"
            "  presentation = Soy [SDR]. + gancho investigado (empresa B2B / persona B2C)\n"
            "  problem = dolor/necesidad sectorial o de rol (1–2 frases)\n"
            "  solution = solución/beneficio del producto (1–2 frases, sin features)\n"
            "  benefits = opcional / vacío\n"
            "  cta = reunión/llamada breve con ¿...? (SIN minutos; tono profesional)\n"
            "OBLIGATORIO: saludo + presentación con gancho (solo rol/empresa o brief) "
            "+ problema/solución + CTA reunión. Nunca cerrar venta.\n\n"
            'JSON: {"internal":{"probable_problem":"","why_it_matters":"","hypothesis":"","response_question":"","selling_to_role":""},'
            '"sections":{"greeting":"","presentation":"","problem":"","solution":"","benefits":"","cta":""},'
            '"subject":"...","body":"..."}\n'
            if first_touch
            else (
                f"CAMPOS JSON — follow-up Día {step_day}:\n"
                "internal (razonamiento, no va al mensaje):\n"
                "  why_it_matters = objetivo de ESTE toque en la secuencia (routing, valor, timing, cierre…)\n"
                "  response_question = pregunta/CTA humana de este toque\n"
                "  selling_to_role = rol al que apunta el mensaje\n"
                "NO rellenes probable_problem/hypothesis con un re-pitch del producto.\n"
                + (
                    "Día 7 WhatsApp: body 1-3 líneas; retomá mensajes previos; CTA con ¿seguir conversando o dejarlo?\n"
                    f'Ejemplo: {{"internal":{{"why_it_matters":"Seguimiento Día 7","response_question":"¿Tiene sentido seguir conversando o preferís que lo deje para más adelante?","selling_to_role":"{prospect.get("role") or "Decisor"}"}},'
                    f'"body":"Hola {first}.\\nRetomo mis mensajes anteriores para no insistir sin sentido.\\n¿Tiene sentido seguir conversando sobre este tema o preferís que lo deje para más adelante?\\n{sender}"}}\n'
                    if step_day == 7 and channel == "whatsapp"
                    else ""
                )
                + (
                    "Día 16 WhatsApp: último intento humano; preguntá si seguimos o lo dejamos.\n"
                    if step_day == 16 and channel == "whatsapp"
                    else ""
                )
                + 'JSON: {"internal":{"probable_problem":"","why_it_matters":"","hypothesis":"","response_question":"","selling_to_role":""},'
                '"subject":null|"..." ,"body":"..."}\n'
            )
        )
    )


def _expected_json_schema(first_touch: bool) -> str:
    if first_touch:
        return (
            '{"internal":{"probable_problem":"","why_it_matters":"","hypothesis":"",'
            '"response_question":"","selling_to_role":""},'
            '"sections":{"greeting":"","presentation":"","problem":"","solution":"","benefits":"","cta":""},'
            '"subject":"...","body":"..."}'
        )
    return (
        '{"internal":{"probable_problem":"","why_it_matters":"","hypothesis":"",'
        '"response_question":"","selling_to_role":""},'
        '"subject":null|"..." ,"body":"..."}'
    )


def _salvage_body_from_raw(raw: str, stripped: str) -> str | None:
    for source in (stripped, raw):
        if not source or not source.strip():
            continue
        text = source.strip()
        if not text.lstrip().startswith("{") and len(text) >= 20:
            return text
        for pattern in (
            r'"body"\s*:\s*"((?:\\.|[^"\\])*)"',
            r'"body"\s*:\s*\'([^\']*)\'',
            r'body["\']?\s*[:=]\s*["\'](.+?)["\'](?:\s*[,}])',
        ):
            match = re.search(pattern, text, re.I | re.S)
            if not match:
                continue
            body = (
                match.group(1)
                .replace("\\n", "\n")
                .replace("\\r", "\r")
                .replace('\\"', '"')
                .replace("\\'", "'")
                .strip()
            )
            if len(body) >= 20:
                return body
    return None


def _recover_touch_data_after_parse_error(
    exc: SdrResponseParseError,
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    step_day: int,
    step_objective: str,
    prior_touches: list[dict[str, Any]],
    first_touch: bool,
) -> tuple[dict[str, Any], bool] | None:
    """Recupera un borrador usable cuando OpenAI no devolvió JSON válido."""
    from app.services.openai_fallback import build_sdr_playbook_fallback_json, is_openai_fallback_enabled

    salvage = (exc.salvage_body or "").strip()
    if salvage and len(salvage) >= 20 and not first_touch:
        from app.services.openai_fallback import normalize_follow_up_internal

        internal = normalize_follow_up_internal(
            {
                "probable_problem": "",
                "why_it_matters": f"Seguimiento Día {step_day}: {step_objective or 'retomar conversación'}",
                "hypothesis": "",
                "response_question": "",
                "selling_to_role": "",
            },
            body=salvage,
            prospect=prospect,
            step_day=step_day,
            step_objective=step_objective,
        )
        return (
            {
                "internal": internal,
                "body": salvage,
            },
            False,
        )

    if first_touch and not is_openai_fallback_enabled():
        return None

    raw = build_sdr_playbook_fallback_json(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        step_day=step_day,
        step_objective=step_objective,
        prior_touches=prior_touches,
    )
    return json.loads(raw), True


def _generation_debug_base(
    *,
    channel: Channel,
    step_day: int,
    first_touch: bool,
    system_prompt: str,
    user_prompt: str,
    raw_response: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    max_output_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "step_day": step_day,
        "model": model,
        "prompt_system": system_prompt,
        "prompt_user": user_prompt,
        "raw_response": raw_response,
        "expected_json_schema": _expected_json_schema(first_touch),
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "parse_error": None,
        "stacktrace": None,
        "stripped_response": None,
    }


def _parse_json_response_debug(raw: str, *, debug_base: dict[str, Any]) -> dict[str, Any]:
    stripped = oai._strip_json_fence(raw)
    debug = {**debug_base, "stripped_response": stripped}
    parse_error: str | None = None
    stacktrace: str | None = None

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as initial_error:
        parse_error = (
            f"json.JSONDecodeError: {initial_error.msg} "
            f"(pos {initial_error.pos}, línea {initial_error.lineno}, col {initial_error.colno})"
        )
        stacktrace = traceback.format_exc()
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise SdrResponseParseError(
                message="OpenAI no devolvió JSON válido para el borrador SDR.",
                debug={**debug, "parse_error": parse_error, "stacktrace": stacktrace},
                salvage_body=_salvage_body_from_raw(raw, stripped),
            ) from initial_error
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as slice_error:
            parse_error = f"json.JSONDecodeError inicial: {initial_error}; slice: {slice_error}"
            stacktrace = traceback.format_exc()
            raise SdrResponseParseError(
                message="OpenAI no devolvió JSON válido para el borrador SDR.",
                debug={**debug, "parse_error": parse_error, "stacktrace": stacktrace},
                salvage_body=_salvage_body_from_raw(raw, stripped),
            ) from slice_error

    if not isinstance(data, dict):
        parse_error = f"Tipo inesperado tras json.loads: {type(data).__name__}"
        stacktrace = traceback.format_exc()
        raise SdrResponseParseError(
            message="Respuesta OpenAI inválida (no es un objeto JSON).",
            debug={**debug, "parse_error": parse_error, "stacktrace": stacktrace},
            salvage_body=_salvage_body_from_raw(raw, stripped),
        )
    return data


def _validate_internal(
    internal: dict[str, Any],
    prospect: dict[str, str],
    campaign: dict[str, str],
    *,
    follow_up: bool = False,
    step_day: int = 1,
    step_objective: str = "",
    outcome_context: str = "",
    how_context: str = "",
    body: str = "",
) -> _ValidationAccum:
    acc = _ValidationAccum()
    if follow_up:
        if body.strip():
            from app.services.openai_fallback import normalize_follow_up_internal

            repaired = normalize_follow_up_internal(
                internal,
                body=body,
                prospect=prospect,
                step_day=step_day,
                step_objective=step_objective,
            )
            internal.clear()
            internal.update(repaired)
        for key in ("why_it_matters", "response_question"):
            if len(str(internal.get(key) or "").strip()) < 8:
                acc.issues.append(f"internal.{key} incompleto — debe reflejar el objetivo del Día {step_day}")
        selling = str(internal.get("selling_to_role") or "").strip()
        if len(selling) < 3:
            acc.issues.append("internal.selling_to_role incompleto")
        return acc

    how_explained_in_message = _mentions_how_we_do_it(how_context)
    for key in ("probable_problem", "why_it_matters", "hypothesis", "response_question"):
        if len(str(internal.get(key) or "").strip()) < 12:
            if key == "hypothesis" and how_explained_in_message:
                continue
            acc.issues.append(f"internal.{key} incompleto")
    if len(str(internal.get("selling_to_role") or "").strip()) < 3:
        acc.issues.append("internal.selling_to_role incompleto — debe indicar a qué rol vendés")
    expected_role = (prospect.get("selling_to_role") or prospect.get("role") or "").strip()
    selling = str(internal.get("selling_to_role") or "").strip()
    if expected_role and selling:
        exp_low = expected_role.lower()
        sell_low = selling.lower()
        if exp_low not in sell_low and sell_low not in exp_low:
            exp_token = expected_role.split()[0].lower()
            if exp_token not in sell_low:
                acc.issues.append(
                    f"internal.selling_to_role debe ser el rol acordado ({expected_role}), no mezclar perfiles"
                )
    for key in ("probable_problem", "hypothesis"):
        val = str(internal.get(key) or "").strip()
        if val:
            acc.extend(
                _collect_pattern_matches(val, f"internal.{key}", _GENERIC_CORPORATE, "lenguaje corporativo genérico")
            )
            acc.extend(
                _collect_pattern_matches(
                    val,
                    f"internal.{key}",
                    _PAIN_ASSUMPTION_BANNED,
                    "culpa/fricción directa hacia el prospecto",
                )
            )
    probable = str(internal.get("probable_problem") or "").strip()
    if probable and not (
        _mentions_concrete_outcome(probable, outcome_context=outcome_context)
        or _SECTORAL_VALUE.search(probable)
        or _RESULT_MARKERS.search(probable)
    ):
        acc.issues.append(
            "internal.probable_problem: debe describir el problema/beneficio que resolvés (anclado al producto)"
        )
    hypothesis = str(internal.get("hypothesis") or "").strip()
    if hypothesis and not (
        _mentions_how_we_do_it(hypothesis, how_context=how_context)
        or _RESULT_MARKERS.search(hypothesis)
        or _SECTORAL_VALUE.search(hypothesis)
    ):
        acc.issues.append("internal.hypothesis: debe explicar brevemente la solución/beneficio")
    return acc


def _pitch_block_count(body: str) -> int:
    """Cuenta bloques típicos del pitch Día 1 repetidos en follow-ups."""
    count = 0
    if _WHY_WRITE_MARKERS.search(body):
        count += 1
    if _RE_PITCH_STRUCTURE.search(body):
        count += 1
    if re.search(r"esto les permite", body, re.I):
        count += 1
    if re.search(r"^soy\s+", body.strip(), re.I | re.M):
        count += 1
    return count


def _validate_follow_up_body(
    body: str,
    *,
    step_day: int,
    channel: Channel,
) -> _ValidationAccum:
    acc = _ValidationAccum()
    if channel == "whatsapp":
        text = body.strip()
        if len(text) < 10:
            acc.issues.append("body vacío o demasiado corto")
        elif len(text) > 280:
            acc.issues.append(f"Día {step_day}: WhatsApp demasiado largo ({len(text)} caracteres)")
        acc.extend(
            _collect_pattern_matches(
                text, "body", _GUILT_FOLLOWUP_BANNED, "reproche de lectura/respuesta prohibido"
            )
        )
        acc.extend(
            _collect_pattern_matches(
                text, "body", _INTEREST_CHECK_BANNED, "chequeo de interés residual prohibido"
            )
        )
        return acc

    acc.extend(_validate_base_text(body, "body"))
    acc.extend(_collect_pattern_matches(body, "body", _GENERIC_CORPORATE, "lenguaje corporativo genérico"))
    acc.extend(_collect_pattern_matches(body, "body", _PRODUCT_PITCH, "re-pitch de producto prohibido en follow-up"))
    acc.extend(
        _collect_pattern_matches(
            body, "body", _INTEREST_CHECK_BANNED, "chequeo de interés residual prohibido"
        )
    )

    if step_day in (4, 7, 10, 13) and not _has_followup_bridge(body):
        acc.issues.append(
            f"Día {step_day}: debe tener puente contextual corto (sin reprochar falta de lectura)"
        )
    if step_day in (16, 19) and not (
        _has_followup_bridge(body) or _BREAKUP_MARKERS.search(body)
    ):
        acc.issues.append(
            f"Día {step_day}: debe cerrar el hilo con CTA de reunión o despedida breve "
            "(sin insistir en «Quedo atento» si ya se usó 2 veces)"
        )

    pitch_blocks = _pitch_block_count(body)
    if pitch_blocks >= 2:
        acc.issues.append(
            f"Día {step_day}: no repetir estructura del pitch del primer toque — mensaje más humano y breve"
        )
    if _RE_PITCH_STRUCTURE.search(body):
        acc.issues.append(
            f"Día {step_day}: no re-explicar cómo funciona el producto en follow-up"
        )

    # Modo despedida suave: CTA liviano permitido; largo por canal (no por día rígido).
    if channel == "email":
        wc = _word_count(body)
        if wc < 12 or wc > 120:
            acc.issues.append(
                f"Día {step_day}: email follow-up ~20-90 palabras (tiene {wc})"
            )
    elif channel == "linkedin":
        n = len(body)
        if n < 40 or n > 520:
            acc.issues.append(
                f"Día {step_day}: LinkedIn follow-up ~80-450 caracteres (tiene {n})"
            )
    else:
        if len(body) > 280:
            acc.issues.append(
                f"Día {step_day}: WhatsApp follow-up demasiado largo ({len(body)} caracteres)"
            )

    if not (
        _COORDINATE_CALL_CTA.search(body)
        or _CONVERSATION_CTA.search(body)
        or _HUMAN_FOLLOWUP_CTA.search(body)
        or ("?" in body or "¿" in body)
        or _BREAKUP_MARKERS.search(body)
    ):
        acc.issues.append(
            f"Día {step_day}: debe ofrecer reunión/llamada/charla (CTA outbound)"
        )

    return acc


def _first_touch_retry_hint() -> str:
    return (
        " Primer toque: usá la VARIANTE automática indicada (bloques grandes editables). "
        "PROHIBIDO plantillas rígidas tipo «ayudamos a equipos comerciales a …». "
        "Hola [Nombre] + presentación + valor anclado al PRODUCTO/ICP + CTA reunión. "
        "Email: subject breve. LinkedIn: párrafos cortos (más desarrollado que WA). "
        "WhatsApp: más corto, informal/chill, micro-párrafos."
    )


def _follow_up_retry_hint(step_day: int) -> str:
    return (
        " Follow-up: bloque corto editable (sin re-pitch del Día 1), "
        "CTA de reunión (nosotros ofrecemos). "
        "«Quedo atento» máx. 2 veces en toda la secuencia. "
        "PROHIBIDO: ¿seguís interesad@?, ¿seguís con ganas…?, "
        "¿pudiste leer/revisar?, sin respuesta, culpa. "
        "Dato de valor: solo si es nuevo y una sola vez; no inventar. "
        "No martilles el nombre del producto."
    )


def _salvage_follow_up_touch_locally(
    *,
    data: dict[str, Any],
    body: str,
    channel: Channel,
    step_day: int,
    step_objective: str,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    prior_touches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Rearma el borrador localmente (sin otra llamada a OpenAI) y lo deja listo para enviar."""
    from app.services.openai_fallback import build_sdr_playbook_fallback_json, normalize_follow_up_internal

    template = json.loads(
        build_sdr_playbook_fallback_json(
            channel=channel,
            prospect=prospect,
            campaign=campaign,
            product=product,
            step_day=step_day,
            step_objective=step_objective,
            prior_touches=prior_touches,
        )
    )
    salvaged_body = str(body or "").strip()
    if not (
        len(salvaged_body) >= 20
        and _has_followup_bridge(salvaged_body)
        and ("?" in salvaged_body or "¿" in salvaged_body or _BREAKUP_MARKERS.search(salvaged_body))
    ):
        salvaged_body = str(template.get("body") or "").strip()

    internal = normalize_follow_up_internal(
        data.get("internal") if isinstance(data.get("internal"), dict) else {},
        body=salvaged_body,
        prospect=prospect,
        step_day=step_day,
        step_objective=step_objective,
    )
    if len(str(internal.get("response_question") or "").strip()) < 8:
        internal = normalize_follow_up_internal(
            template.get("internal") if isinstance(template.get("internal"), dict) else {},
            body=salvaged_body,
            prospect=prospect,
            step_day=step_day,
            step_objective=step_objective,
        )

    out = {**data, "internal": internal, "body": salvaged_body}
    acc = _ValidationAccum()
    acc.extend(_validate_follow_up_body(salvaged_body, step_day=step_day, channel=channel))
    acc.extend(
        _validate_internal(
            internal,
            prospect,
            campaign,
            follow_up=True,
            step_day=step_day,
            step_objective=step_objective,
            body=salvaged_body,
        )
    )
    if acc.issues:
        fallback_body = str(template.get("body") or "").strip()
        out["body"] = fallback_body
        out["internal"] = normalize_follow_up_internal(
            template.get("internal") if isinstance(template.get("internal"), dict) else {},
            body=fallback_body,
            prospect=prospect,
            step_day=step_day,
            step_objective=step_objective,
        )
    return out


def _validate_greeting_text(text: str, field: str, *, first_name: str) -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_validate_pitch_text(text, field))
    if not text.strip():
        acc.issues.append(f"{field}: saludo faltante")
        return acc
    if not _GREETING_OK.search(text.strip()):
        acc.issues.append(f'{field}: debe empezar con Hola / Buen día / Buenas + nombre')
    if first_name and first_name.lower() not in text.lower():
        acc.issues.append(f"{field}: debe incluir el nombre del prospecto ({first_name})")
    return acc


def _validate_presentation_line(
    text: str,
    field: str,
    *,
    sender_name: str,
    brand_name: str,
    research_brief: str = "",
    prospect_company: str = "",
) -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_validate_pitch_text(text, field))
    t = text.strip()
    if not _PRESENTATION_OK.search(t):
        acc.issues.append(
            f'{field}: debe presentarte (ej. "Soy [SDR]." o "Mi nombre es [SDR]…")'
        )
    sender = sender_name.strip()
    if sender:
        token = sender.split()[0].lower()
        if token not in t.lower():
            acc.issues.append(f"{field}: debe incluir el nombre del SDR ({sender})")

    brief = (research_brief or "").strip().lower()
    low = t.lower()
    no_confirmed = (not brief) or ("dato no confirmado" in brief)
    if _FACT_HOOK_NEEDS_BRIEF.search(t):
        if no_confirmed:
            acc.issues.append(
                f"{field}: no inventes ganchos (LinkedIn/crecimiento/news) sin evidencia "
                "en la investigación previa; usá rol/empresa del CRM"
            )
        else:
            evidence = _brief_evidence_tokens(
                brief=brief, prospect_company=(prospect_company or "")
            )
            if not evidence or not any(tok in low for tok in evidence):
                acc.issues.append(
                    f"{field}: el gancho debe citar un hecho concreto del brief de investigación "
                    "(mencionar la empresa no alcanza); usá rol/empresa CRM si no hay evidencia"
                )

    # No tratar la marca vendedora como si fuera la empresa del prospecto.
    brand = (brand_name or "").strip().lower()
    if brand and len(brand) >= 4 and brand in low:
        company_l = (prospect_company or "").strip().lower()
        if brand not in company_l:
            acc.warnings.append(
                f"{field}: aparece la marca vendedora ({brand_name}); "
                "no la confundas con la empresa del prospecto"
            )
    return acc


def _validate_benefits_text(text: str, field: str, *, outcome_context: str = "") -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_validate_base_text(text, field))
    acc.extend(_collect_pattern_matches(text, field, _GENERIC_CORPORATE, "beneficios genéricos/corporativos"))
    if text and not _mentions_concrete_outcome(text, outcome_context=outcome_context):
        acc.issues.append(f"{field}: debe mencionar el resultado o beneficio concreto (breve)")
    return acc


def _product_alignment_tokens(product: dict[str, str]) -> set[str]:
    stop = {
        "para", "como", "este", "esta", "producto", "servicio", "solución", "solucion",
        "empresa", "clientes", "equipos", "mejor", "ayuda", "ayudamos",
    }
    tokens: set[str] = set()
    for field in ("name", "value_proposition", "description", "pain_points", "benefits"):
        for word in re.findall(r"\w{4,}", (product.get(field) or "").lower()):
            if word not in stop:
                tokens.add(word)
    return tokens


def _validate_product_alignment(text: str, field: str, product: dict[str, str]) -> _ValidationAccum:
    """El mensaje debe basarse en el producto de campaña, no en un pitch genérico inventado."""
    acc = _ValidationAccum()
    if not product or not (product.get("name") or product.get("description")):
        return acc
    if not text.strip():
        return acc
    low = text.lower()
    name = (product.get("name") or "").strip()
    if name and name.lower() in low:
        return acc
    tokens = _product_alignment_tokens(product)
    if tokens and any(token in low for token in tokens):
        return acc
    acc.issues.append(
        f"{field}: debe basarse en el producto seleccionado de la campaña "
        f"({name or 'producto configurado'}), no en una propuesta genérica"
    )
    return acc


def _validate_first_touch_sections(
    sections: dict[str, Any],
    *,
    sender_name: str,
    brand_name: str,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    outcome_context: str = "",
    how_context: str = "",
) -> _ValidationAccum:
    acc = _ValidationAccum()
    from app.services.outreach_display_names import is_placeholder_token, prospect_greeting_name

    first_name = prospect_greeting_name(prospect)
    if is_placeholder_token(first_name):
        first_name = ""

    greeting = str(sections.get("greeting") or "").strip()
    presentation = str(sections.get("presentation") or "").strip()
    problem = str(sections.get("problem") or "").strip()
    solution = str(sections.get("solution") or "").strip()
    benefits = str(sections.get("benefits") or "").strip()
    cta = str(sections.get("cta") or "").strip()
    value_para = _first_touch_value_paragraph(sections)

    for key in _FIRST_TOUCH_SECTION_KEYS:
        if len(str(sections.get(key) or "").strip()) >= 8:
            continue
        if key in ("problem", "benefits"):
            # problem puede ir vacío si solution ya trae problema+beneficio.
            if value_para and (
                _mentions_how_we_do_it(value_para, how_context=how_context)
                or _RESULT_MARKERS.search(value_para)
                or _SECTORAL_VALUE.search(value_para)
            ):
                continue
            if key == "problem" and (
                _WHY_WRITE_MARKERS.search(presentation) or _SECTORAL_VALUE.search(presentation)
            ):
                continue
            if key == "benefits" and value_para and _mentions_concrete_outcome(
                value_para, outcome_context=outcome_context
            ):
                continue
        if key == "solution" and (
            _mentions_how_we_do_it(how_context)
            or (problem and (_RESULT_MARKERS.search(problem) or _SECTORAL_VALUE.search(problem)))
        ):
            continue
        acc.issues.append(f"sections.{key} faltante o incompleto")

    if greeting:
        acc.extend(_validate_greeting_text(greeting, "sections.greeting", first_name=first_name))
    if presentation:
        research = (
            (prospect.get("research_brief") or "")
            or (prospect.get("prospecting_context") or "")
        )
        acc.extend(
            _validate_presentation_line(
                presentation,
                "sections.presentation",
                sender_name=sender_name,
                brand_name=brand_name,
                research_brief=research,
                prospect_company=(prospect.get("company_name") or ""),
            )
        )
    if problem:
        acc.extend(_validate_why_write_text(problem, "sections.problem", prospect, campaign))
    if solution:
        acc.extend(
            _validate_solution_text(solution, "sections.solution", how_context=how_context)
        )
        product_blob = " ".join(
            filter(
                None,
                [
                    product.get("name"),
                    product.get("value_proposition"),
                    product.get("description"),
                    product.get("benefits"),
                    product.get("pain_points"),
                ],
            )
        ).lower()
        for m in _INVENTED_METRIC.finditer(solution):
            token = m.group(0).lower().replace(" ", "")
            if token and token not in product_blob.replace(" ", ""):
                acc.issues.append(
                    "sections.solution: no inventes métricas/% sin que estén en el producto"
                )
                break
    if benefits:
        acc.extend(
            _validate_benefits_text(benefits, "sections.benefits", outcome_context=outcome_context)
        )
    product_text = " ".join(filter(None, [problem, solution, benefits]))
    if product_text:
        acc.extend(_validate_product_alignment(product_text, "sections (por qué escribimos/qué hacemos/beneficios)", product))
    if cta:
        acc.extend(_collect_pattern_matches(cta, "sections.cta", _GENERIC_CTA_BANNED, "CTA genérico prohibido"))
        if not cta.rstrip().endswith("?"):
            acc.issues.append("CTA debe ser pregunta orientada a reunión/conversación")
        if not _CONVERSATION_CTA.search(cta):
            acc.issues.append(
                "CTA debe invitar a reunión o conversación breve (ej. coordinar reunión, conversar 10 min)"
            )
        acc.extend(_validate_pitch_text(cta, "sections.cta"))

    return acc


def _validate_first_touch_body(
    body: str,
    *,
    sender_name: str,
    brand_name: str,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    sections: dict[str, Any] | None = None,
    internal: dict[str, Any] | None = None,
) -> _ValidationAccum:
    acc = _ValidationAccum()
    from app.services.outreach_display_names import is_placeholder_token, prospect_greeting_name

    first_name = prospect_greeting_name(prospect)
    if is_placeholder_token(first_name):
        first_name = ""
    outcome_context = _first_touch_outcome_context(
        internal=internal, sections=sections, body=body
    )
    how_context = _first_touch_how_context(
        internal=internal, sections=sections, body=body
    )

    if sections and isinstance(sections, dict):
        acc.extend(
            _validate_first_touch_sections(
                sections,
                sender_name=sender_name,
                brand_name=brand_name,
                prospect=prospect,
                campaign=campaign,
                product=product,
                outcome_context=outcome_context,
                how_context=how_context,
            )
        )

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    signature_re = re.compile(r"^(saludos|un abrazo|atentamente)\b", re.I)
    content_paras = [p for p in paragraphs if not signature_re.match(p.split("\n", 1)[0].strip())]

    if len(content_paras) < 3:
        acc.issues.append(
            "primer toque: body debe tener saludo+presentación, un párrafo de valor impactante y CTA"
        )

    if paragraphs:
        opening_lines = paragraphs[0].split("\n")
        if opening_lines:
            acc.extend(
                _validate_greeting_text(opening_lines[0], "body.saludo", first_name=first_name)
            )
        if len(opening_lines) >= 2:
            research = (
                (prospect.get("research_brief") or "")
                or (prospect.get("prospecting_context") or "")
            )
            acc.extend(
                _validate_presentation_line(
                    opening_lines[1],
                    "body.presentación",
                    sender_name=sender_name,
                    brand_name=brand_name,
                    research_brief=research,
                    prospect_company=(prospect.get("company_name") or ""),
                )
            )
        elif not _PRESENTATION_OK.search(paragraphs[0]):
            acc.issues.append("body: falta presentación del SDR y la marca")

    value_para = content_paras[1] if len(content_paras) >= 2 else ""
    solution_section = str((sections or {}).get("solution") or "").strip()
    solution_ok_in_sections = bool(
        solution_section and _mentions_how_we_do_it(solution_section, how_context=how_context)
    )
    if value_para and not solution_ok_in_sections:
        acc.extend(
            _validate_solution_text(value_para, "body.valor", how_context=how_context)
        )
        acc.extend(
            _validate_benefits_text(
                value_para, "body.valor", outcome_context=outcome_context
            )
        )
    elif not value_para and not solution_ok_in_sections:
        acc.issues.append("body.valor: falta el párrafo de valor impactante")

    if content_paras:
        product_text = " ".join(content_paras[1:2])
        if product_text:
            acc.extend(
                _validate_product_alignment(
                    product_text, "body (valor impactante)", product
                )
            )

    cta_para = content_paras[-1] if content_paras else ""
    if cta_para and cta_para != value_para:
        acc.extend(_collect_pattern_matches(cta_para, "body.cta", _GENERIC_CTA_BANNED, "CTA genérico prohibido"))
        if not cta_para.rstrip().endswith("?"):
            acc.issues.append("primer toque: CTA debe terminar en ?")
        if not _CONVERSATION_CTA.search(cta_para):
            acc.issues.append("primer toque: CTA debe invitar a reunión o demo breve")

    return acc


def _validate_body(
    channel: Channel,
    body: str,
    *,
    subject: str | None = None,
    first_touch: bool = False,
    step_day: int = 1,
    sender_name: str = "",
    brand_name: str = "",
    prospect: dict[str, str] | None = None,
    campaign: dict[str, str] | None = None,
    product: dict[str, str] | None = None,
    sections: dict[str, Any] | None = None,
    internal: dict[str, Any] | None = None,
) -> _ValidationAccum:
    acc = _ValidationAccum()
    prospect = prospect or {}
    campaign = campaign or {}
    product = product or {}
    if first_touch:
        acc.extend(
            _validate_first_touch_body(
                body,
                sender_name=sender_name,
                brand_name=brand_name,
                prospect=prospect,
                campaign=campaign,
                product=product,
                sections=sections,
                internal=internal,
            )
        )
        if subject:
            acc.extend(_validate_pitch_text(subject, "subject"))
    else:
        acc.extend(_validate_follow_up_body(body, step_day=step_day, channel=channel))
        if subject:
            acc.extend(_validate_base_text(subject, "subject"))
    if first_touch and channel == "email":
        wc = _word_count(body)
        block_issues = [i for i in acc.issues if not i.startswith("longitud:")]
        checklist, _ = _build_first_touch_block_checklist(
            sections=sections if isinstance(sections, dict) else None,
            internal=internal if isinstance(internal, dict) else None,
            issues=block_issues,
        )
        blocks_ok = all(item["ok"] for item in checklist)
        if wc < _EMAIL_DAY1_WORDS_MIN:
            acc.issues.append(
                f"longitud: email primer toque tiene {wc} palabras (mínimo {_EMAIL_DAY1_WORDS_MIN})"
            )
        elif wc > _EMAIL_DAY1_WORDS_MAX:
            acc.issues.append(
                f"longitud: email primer toque tiene {wc} palabras (máximo {_EMAIL_DAY1_WORDS_MAX})"
            )
        elif blocks_ok and (wc < _EMAIL_DAY1_WORDS_IDEAL_MIN or wc > _EMAIL_DAY1_WORDS_IDEAL_MAX):
            acc.warnings.append(
                f"longitud: email primer toque tiene {wc} palabras "
                f"(ideal {_EMAIL_DAY1_WORDS_IDEAL_MIN}-{_EMAIL_DAY1_WORDS_IDEAL_MAX}, "
                f"aceptable {_EMAIL_DAY1_WORDS_MIN}-{_EMAIL_DAY1_WORDS_MAX})"
            )
    elif first_touch and channel == "linkedin":
        n = len(body)
        low = body.lower()
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        from app.services.linkedin_copy_quality import linkedin_copy_issues

        for issue in linkedin_copy_issues(
            body,
            company=prospect.get("company_name") or "",
            product=(product or {}).get("name") or "",
        ):
            acc.issues.append(issue)
        if n < 200 or n > 480:
            acc.issues.append(
                f"longitud: LinkedIn primer toque tiene {n} caracteres (obligatorio 200-480)"
            )
        elif len(paras) < 2:
            acc.issues.append(
                "formato: LinkedIn primer toque debe dividirse en párrafos "
                "(línea en blanco entre bloques; no un solo muro de texto)"
            )
        if not any(x in low for x in ("cómo estás", "como estas", "cómo va", "como va")):
            acc.issues.append(
                "estructura: LinkedIn primer toque debe preguntar cómo está en el saludo («Hola X, ¿cómo estás?»)"
            )
        from app.services.outreach_human_anchor import has_outreach_human_anchor

        if not has_outreach_human_anchor(body):
            acc.issues.append(
                "estructura: LinkedIn primer toque debe contextualizar empresa/rol del prospecto "
                "(enganche humano, no solo pitch de producto)"
            )
        if "lo hacemos mediante" in low or "plataforma multicanal de outreach" in low:
            acc.issues.append(
                "contenido: LinkedIn no debe pegar la ficha del producto; reescribí valor en 1–2 oraciones"
            )
    elif first_touch and channel == "whatsapp":
        wc = _word_count(body)
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        line_count = len([ln for ln in body.splitlines() if ln.strip()])
        if wc > 45 or len(body) > 280 or line_count > 6:
            acc.issues.append(
                f"longitud: WhatsApp primer toque demasiado largo "
                f"({wc} palabras, {line_count} líneas, {len(body)} caracteres; "
                f"máx ~45 palabras / ~280 caracteres)"
            )
        elif len(paras) < 2:
            acc.issues.append(
                "formato: WhatsApp primer toque debe ir en 2–3 micro-párrafos "
                "(no un solo bloque)"
            )
    return acc


def _subject_company_label(company: str) -> str:
    from app.services.outreach_display_names import prospect_company_display

    name = prospect_company_display(company)
    if not name:
        return "tu equipo"
    if len(name) > 42:
        first_word = name.split()[0] if name.split() else name
        return first_word[:42]
    return name


def _playbook_email_subject(
    *,
    step_day: int,
    prospect: dict[str, str],
    raw_subject: str | None,
    product: dict[str, str] | None = None,
) -> str:
    """Asunto email con valor del producto (regla 9)."""
    del step_day  # el gancho sale del producto; el día no fija plantilla genérica
    from app.services.sequence_email_subject_guard import enforce_value_email_subject

    company = _subject_company_label(str(prospect.get("company_name") or ""))
    seed = f"{prospect.get('id') or ''}|{company}|{raw_subject or ''}"
    out = enforce_value_email_subject(
        subject=raw_subject,
        product=product,
        company=company,
        seed=seed,
        sequence_first_touch=True,
        channel="email",
    )
    return (out or "Idea breve")[:72]


def _touch_from_approved_fallback(
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    step_day: int,
    step_objective: str,
    prior_touches: list[dict[str, Any]],
    first_touch: bool,
) -> dict[str, Any]:
    """Plantilla aprobada del playbook — último recurso si la IA no pasa validación."""
    from app.services.openai_fallback import (
        build_sdr_playbook_fallback_json,
        normalize_follow_up_internal,
    )

    raw = build_sdr_playbook_fallback_json(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        step_day=step_day,
        step_objective=step_objective,
        prior_touches=prior_touches,
    )
    data = json.loads(raw)
    if not first_touch:
        body = str(data.get("body") or "").strip()
        data["internal"] = normalize_follow_up_internal(
            data.get("internal") if isinstance(data.get("internal"), dict) else {},
            body=body,
            prospect=prospect,
            step_day=step_day,
            step_objective=step_objective,
        )
    return data


def _ensure_short_product_named_once(body: str, *, product_name: str) -> str:
    """En 1º toque: si la IA omitió el nombre comercial, lo inserta una sola vez."""
    from app.services.linkedin_copy_quality import mention_count

    text = (body or "").strip()
    name = re.sub(r"\s+", " ", (product_name or "").strip())
    if not text or not name or mention_count(text, [name]) >= 1:
        return text
    # «Nosotros / Desde / Con …» → anclar el nombre comercial.
    replaced, n = re.subn(
        r"(?i)\b(nosotros|desde)\s+",
        f"Con {name} ",
        text,
        count=1,
    )
    if n:
        return replaced
    replaced, n = re.subn(
        r"(?i)\bcon\s+(esto|esta\s+soluci[oó]n)\b",
        f"con {name}",
        text,
        count=1,
    )
    if n:
        return replaced
    # Si no hay ancla, agregar una línea corta de valor con el nombre (antes del CTA).
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    insert_at = max(len(paras) - 1, 1) if len(paras) >= 2 else len(paras)
    paras.insert(insert_at, f"Con {name} lo resolvemos de forma concreta.")
    return "\n\n".join(paras)


def _product_allows_nexus_outbound_pitch(product: dict[str, str] | None) -> bool:
    """True si la ficha/campaña es Nexus u outbound (puede usar ese pitch)."""
    pname = str((product or {}).get("name") or "").lower()
    if "nexus" in pname:
        return True
    try:
        from app.services.message_structure_variants import _ficha_claims_outbound_automation

        return bool(_ficha_claims_outbound_automation(product))
    except Exception:
        return False


def _body_looks_like_wrong_product_pitch(
    body: str,
    *,
    product: dict[str, str],
) -> bool:
    """True si el body vende Nexus/outbound genérico y la ficha del producto no lo respalda."""
    text = (body or "").strip()
    if len(text) < 20:
        return True
    if _product_allows_nexus_outbound_pitch(product):
        return False
    # Pitch típico de Nexus / outbound sobre un producto ajeno (Contractia, etc.).
    if re.search(
        r"\bnexus(?:\s+sales)?\b|"
        r"60\s*%\s*(?:a|–|-|—)\s*90|"
        r"tareas\s+manuales\s+de\s+(?:prospecci|outbound)|"
        r"automatiz\w*.{0,40}outbound|"
        r"prospecci[oó]n\s+outbound|"
        r"outreach\s+(?:multicanal|comercial)|"
        r"plataforma\s+multicanal\s+de\s+outreach",
        text,
        re.I,
    ):
        return True
    return False


def _scrub_stray_nexus_brand(body: str, *, product: dict[str, str]) -> str:
    """Si el producto no es Nexus, no dejar el brand Nexus colgado en el copy."""
    if _product_allows_nexus_outbound_pitch(product):
        return body or ""
    name = str(product.get("name") or "").strip() or "nuestra solución"
    text = body or ""
    text = re.sub(r"(?i)\bnexus\s+sales\b", name, text)
    text = re.sub(r"(?i)\bnexus\b", name, text)
    return text


def _rebuild_product_safe_first_touch(
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    prior_touches: list[dict[str, Any]],
    step_day: int,
) -> tuple[str | None, str, SdrReasoningRead]:
    """
    Fallback duro sin pitch Nexus: banco anclado a ficha, o armado mínimo desde VP.
    """
    from app.services.cold_message_bank import render_cold_bank_touch
    from app.services.message_structure_variants import pick_first_touch_variant

    product_copy = _product_for_outreach_copy(product)
    rendered = render_cold_bank_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product_copy,
        prior_touches=prior_touches,
        first_touch=True,
        step_day=step_day,
        seller_template=None,
    )
    subject = rendered.subject
    body = _scrub_stray_nexus_brand(rendered.body, product=product_copy)
    if _body_looks_like_wrong_product_pitch(body, product=product_copy):
        # Nuclear: armar desde ficha sin plantillas que puedan filtrar Nexus.
        from app.services.cold_message_bank import (
            _brand,
            _first_name,
            _product_name,
            _sender,
            _valor_one_liner,
        )
        from app.services.outreach_human_anchor import pick_outreach_context_anchor_short

        sdr = _sender(campaign)
        marca = _brand(campaign)
        pname = _product_name(product_copy, brand=marca)
        nombre = _first_name(prospect)
        empresa = str(prospect.get("company_name") or "").strip()
        rol = str(prospect.get("role") or "").strip()
        valor = _valor_one_liner(
            product_copy,
            product_name=pname,
            market="b2b",
            channel=channel,
            explain_more=True,
            seed=f"{prospect.get('id')}|{campaign.get('id')}|safe",
        )
        valor = _scrub_stray_nexus_brand(valor, product=product_copy)
        if _body_looks_like_wrong_product_pitch(valor, product=product_copy):
            vp = str(
                product_copy.get("value_proposition")
                or product_copy.get("description")
                or ""
            ).strip()
            valor = (
                f"Con {pname}, {vp[0].lower() + vp[1:] if vp else 'resolvemos lo concreto de tu operación.'}"
            )
            if not valor.endswith((".", "!", "?")):
                valor += "."
        anchor = pick_outreach_context_anchor_short(
            empresa=empresa or None,
            rol=rol or None,
            seed=f"{prospect.get('id')}|safe-anchor",
            channel=channel,
        )
        cta = "¿Podemos agendar una llamada breve esta semana o la próxima?"
        if channel == "email":
            body = (
                f"Hola {nombre},\n\n"
                f"Soy {sdr} de {marca}.\n\n"
                f"{anchor} {valor}\n\n"
                f"{cta}\n\n"
                f"Saludos,\n{sdr}"
            )
            from app.services.sequence_email_subject_guard import short_value_subject_hook

            subject = short_value_subject_hook(product_copy)
        elif channel == "whatsapp":
            body = (
                f"Hola {nombre},\n\n"
                f"Soy {sdr} de {marca}.\n\n"
                f"{anchor} {valor}\n\n"
                f"{cta}"
            )
            subject = None
        else:
            body = (
                f"Hola {nombre},\n\n"
                f"Soy {sdr} de {marca}. {anchor}\n\n"
                f"{valor}\n\n"
                f"{cta}"
            )
            subject = None
        body = _ensure_short_product_named_once(body, product_name=pname)
    reasoning = SdrReasoningRead(
        probable_problem=str(product_copy.get("pain_points") or "")[:500],
        why_it_matters="Fallback producto-seguro (sin pitch Nexus ajeno)",
        hypothesis=str(product_copy.get("value_proposition") or product_copy.get("name") or "")[:500],
        response_question="coordinar reunión / call",
        selling_to_role=str(prospect.get("role") or "")[:200],
        bank_template_id="product_safe_fallback",
        structure_variant=pick_first_touch_variant(
            channel=channel,
            prospect_id=prospect.get("id"),
            campaign_id=campaign.get("id"),
        ),
    )
    return subject, body, reasoning


def _guard_campaign_product_isolation(
    subject: str | None,
    body: str,
    reasoning: SdrReasoningRead,
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    prior_touches: list[dict[str, Any]],
    step_day: int,
    sequence_first_touch: bool,
) -> tuple[str | None, str, SdrReasoningRead]:
    """
    Candado final: si el copy vende Nexus/outbound y la ficha no lo respalda,
    regenerar desde la ficha (OpenAI-down / banco / Mi estilo).
    """
    product_copy = _product_for_outreach_copy(product)
    cleaned = _scrub_stray_nexus_brand(body, product=product_copy)
    if not sequence_first_touch:
        # En FU igual bloqueamos brand Nexus ajeno, sin rearmar cold.
        if cleaned != (body or "") and reasoning is not None:
            reasoning.bank_template_id = (
                f"{reasoning.bank_template_id or 'touch'}+nexus_scrub"
            )
        return subject, cleaned, reasoning
    if not _body_looks_like_wrong_product_pitch(cleaned, product=product_copy):
        return subject, cleaned, reasoning
    return _rebuild_product_safe_first_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product_copy,
        prior_touches=prior_touches,
        step_day=step_day,
    )


def _try_openai_first_touch(
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    education: str,
    step_day: int,
    step_objective: str,
    prior_touches: list[dict[str, Any]],
    tone: str,
) -> tuple[str | None, str, SdrReasoningRead] | None:
    """
    1º toque vía OpenAI anclado a la ficha del producto (cualquier producto).
    Devuelve None si no hay key, falla el parse o el body no es usable → caller usa banco.
    """
    if not oai.openai_configured():
        return None
    product_copy = _product_for_outreach_copy(product)
    system = (
        _SDR_PLAYBOOK_SYSTEM
        + "\n\nREGLAS DURAS EXTRA:\n"
        "- Vendé SOLO el PRODUCTO SELECCIONADO de la ficha. "
        "PROHIBIDO pitch de Nexus Sales / outbound genérico si no está en la ficha.\n"
        "- Nombre comercial del producto: OBLIGATORIO exactamente 1 vez "
        f"(nombre corto «{product_copy.get('name') or ''}»). "
        "PROHIBIDO 0 menciones y PROHIBIDO el título largo con |.\n"
        "- CTA con 2 opciones blandas (esta semana o la próxima). "
        "PROHIBIDO decir minutos (10/15). PROHIBIDO «te viene bien» (preferí «te queda bien» / «podemos coordinar»). "
        "No inventes día/hora sin Calendar.\n"
        "- Si usás «Vi que sos…», conectalo al valor en la misma idea (sin corte seco). "
        "PROHIBIDO «me llamó la atención cómo manejan…» u otros procesos inventados.\n"
        "- Email: subject con ángulo corto de VALOR de ESTA ficha (no genérico; "
        "si es contratos/firma NO digas «envíos»).\n"
        "- WhatsApp: más corto; sin firma «Saludos,».\n"
    )
    user = _build_user_prompt(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product_copy,
        education=education,
        step_day=step_day,
        step_objective=step_objective or f"Día {step_day} — primer toque",
        prior_touches=prior_touches,
        tone=tone or str(campaign.get("tone") or ""),
    )
    user = (
        user
        + "\n\nRespondé SOLO un JSON válido (sin markdown). "
        "Claves: internal, sections (greeting/presentation/problem/solution/benefits/cta), "
        "subject (null en LinkedIn/WhatsApp), body."
    )
    max_tokens = 900 if channel == "email" else (720 if channel == "linkedin" else 480)
    try:
        raw = oai._chat(
            system,
            user,
            temperature=0.45,
            max_output_tokens=max_tokens,
        )
    except Exception:
        return None
    if not (raw or "").strip():
        return None

    data: dict[str, Any] | None = None
    try:
        data = _parse_json_response_debug(
            raw,
            debug_base=_generation_debug_base(
                channel=channel,
                step_day=step_day,
                first_touch=True,
                system_prompt=system,
                user_prompt=user,
                raw_response=raw,
                model=getattr(oai, "OPENAI_MODEL", None) or "openai",
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                max_output_tokens=max_tokens,
                temperature=0.45,
            ),
        )
    except SdrResponseParseError as exc:
        salvage = (exc.salvage_body or "").strip()
        if len(salvage) < 40:
            return None
        data = {"body": salvage, "subject": None, "internal": {}}

    if not isinstance(data, dict):
        return None

    subject: str | None = None
    if channel == "email":
        raw_subj = data.get("subject")
        subject = str(raw_subj).strip() if isinstance(raw_subj, str) and raw_subj.strip() else None
        if not subject:
            subject = _playbook_email_subject(
                step_day=step_day,
                prospect=prospect,
                raw_subject=None,
                product=product_copy,
            )

    body = str(data.get("body") or "").strip()
    sections = data.get("sections") if isinstance(data.get("sections"), dict) else None
    if not body and sections:
        body = _assemble_first_touch_body(sections)
    body = (body or "").strip()
    if len(body) < 40:
        return None
    if _body_looks_like_wrong_product_pitch(body, product=product_copy):
        return None
    body = _ensure_short_product_named_once(
        body, product_name=str(product_copy.get("name") or "")
    )
    if channel == "email":
        from app.services.outbound_text_normalize import normalize_outbound_email_body

        body = normalize_outbound_email_body(body)

    internal = data.get("internal") if isinstance(data.get("internal"), dict) else {}
    from app.services.message_structure_variants import pick_first_touch_variant
    from app.services.outreach_language import (
        adapt_outbound_text_to_language,
        language_from_campaign_dict,
    )

    lang = language_from_campaign_dict(campaign)
    subject, body = adapt_outbound_text_to_language(
        body=body,
        subject=subject,
        channel=channel,
        lang=lang,
    )
    reasoning = SdrReasoningRead(
        probable_problem=str(internal.get("probable_problem") or "")[:500],
        why_it_matters=str(internal.get("why_it_matters") or "")[:500],
        hypothesis=str(internal.get("hypothesis") or "")[:500],
        response_question=str(internal.get("response_question") or "")[:300],
        selling_to_role=str(
            internal.get("selling_to_role")
            or prospect.get("selling_to_role")
            or prospect.get("role")
            or ""
        )[:120],
        bank_template_id="openai_product_first_touch",
        structure_variant=pick_first_touch_variant(
            channel=channel,
            prospect_id=prospect.get("id"),
            campaign_id=campaign.get("id"),
        ),
    )
    return subject, body, reasoning


def generate_sdr_playbook_touch(
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    education: str,
    step_day: int,
    step_objective: str,
    prior_touches: list[dict[str, Any]],
    tone: str = "",
    prospect_model: Any | None = None,
    campaign_model: Any | None = None,
    seller_style: Any | None = None,
) -> tuple[str | None, str, SdrReasoningRead]:
    from app.services.first_touch_polish import polish_first_touch
    from app.services.followup_anchor_guard import enforce_no_role_anchor_on_followup
    from app.services.sequence_cta_guard import (
        enforce_cta_repeat_limit,
        polish_outbound_meeting_cta,
    )

    # 1º toque = sin priors en la SECUENCIA (no “primer LI después de un mail”).
    sequence_first = _is_first_touch(prior_touches)
    product_copy = _product_for_outreach_copy(product)

    subject, body, reasoning = _generate_sdr_playbook_touch_raw(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product_copy,
        education=education,
        step_day=step_day,
        step_objective=step_objective,
        prior_touches=prior_touches,
        tone=tone,
        prospect_model=prospect_model,
        campaign_model=campaign_model,
        seller_style=seller_style,
        sequence_first_touch=sequence_first,
    )
    if sequence_first:
        body = polish_first_touch(body)
        if channel == "linkedin":
            from app.services.linkedin_copy_quality import polish_linkedin_outbound

            body = polish_linkedin_outbound(
                body,
                company=prospect.get("company_name") or "",
                product=(product_copy or {}).get("name") or "",
            )
        # Regla: con pocos datos CRM, usarlos. Rol + empresa en el 1º toque (IA no saltea).
        from app.services.cold_message_bank import _ensure_role_and_company
        from app.services.outreach_display_names import (
            prospect_company_for_outreach,
            prospect_role_for_outreach,
        )

        rol = prospect_role_for_outreach(
            prospect.get("role") or prospect.get("selling_to_role"),
            prospect.get("name"),
            company_name=prospect.get("company_name"),
        )
        emp = prospect_company_for_outreach(
            company_name=prospect.get("company_name"),
            email=prospect.get("email"),
            source_provider=prospect.get("source_provider"),
        )
        body = _ensure_role_and_company(
            body or "",
            slots={
                "empresa": emp or "",
                "rol": rol or "",
                "marca": (
                    campaign.get("brand_name")
                    or campaign.get("seller_company_name")
                    or ""
                ).strip(),
            },
        )
        # Regla 3b — JAMÁS salteable: puente ancla → valor (sin corte seco).
        from app.services.sequence_anchor_bridge_guard import enforce_anchor_value_bridge

        body = enforce_anchor_value_bridge(
            body=body or "",
            sequence_first_touch=True,
            seed=f"{prospect.get('id') or ''}|{step_day}",
        )
    # Regla 3 — JAMÁS salteable: follow-ups sin «Vi que sos…».
    body = enforce_no_role_anchor_on_followup(
        body=body or "",
        sequence_first_touch=sequence_first,
    )
    # FU: OBLIGATORIO apalancarse del mensaje/canal anterior (no mensaje suelto).
    from app.services.sequence_followup_leverage_guard import (
        enforce_followup_prior_leverage,
    )

    body = enforce_followup_prior_leverage(
        body=body or "",
        prior_touches=prior_touches,
        sequence_first_touch=sequence_first,
    )
    # Regla 4 — JAMÁS salteable: misma formulación de CTA máx. 2× por secuencia.
    body = enforce_cta_repeat_limit(body=body, prior_touches=prior_touches)
    # Regla 5 — JAMÁS salteable: «Quedo atento» máx. 2× + CTA casi siempre.
    from app.services.sequence_close_guard import enforce_close_and_cta

    body = enforce_close_and_cta(body=body, prior_touches=prior_touches)
    # Regla 6 — JAMÁS salteable: prohibido «¿seguís interesad@?» / «con ganas…».
    from app.services.sequence_interest_check_guard import enforce_no_interest_check

    body = enforce_no_interest_check(body=body, prior_touches=prior_touches)
    # Regla 7 — JAMÁS salteable: cada dato de valor una sola vez con esa persona.
    from app.services.sequence_value_once_guard import enforce_value_said_once

    body = enforce_value_said_once(
        body=body,
        prior_touches=prior_touches,
        sequence_first_touch=sequence_first,
    )
    # Regla 9 — JAMÁS salteable: asunto email con valor del producto.
    if channel == "email":
        from app.services.sequence_email_subject_guard import enforce_value_email_subject

        subject = enforce_value_email_subject(
            subject=subject,
            product=product_copy,
            company=str(prospect.get("company_name") or ""),
            seed=f"{prospect.get('id') or ''}|{step_day}|{subject or ''}",
            sequence_first_touch=sequence_first,
            channel="email",
        )
    # Regla 10 — JAMÁS salteable: no martillar el nombre del producto.
    from app.services.sequence_product_name_guard import enforce_product_name_limit

    body = enforce_product_name_limit(
        body=body or "",
        product_name=str((product_copy or {}).get("name") or ""),
        prior_touches=prior_touches,
        sequence_first_touch=sequence_first,
        brand=str(
            (campaign or {}).get("brand_name") or (product_copy or {}).get("name") or ""
        ),
    )
    # Si la ficha tenía título largo con |, sacarlo del body (dejar nombre corto).
    full_title = str((product_copy or {}).get("full_name") or "").strip()
    short_title = str((product_copy or {}).get("name") or "").strip()
    if full_title and short_title and full_title != short_title and full_title in (body or ""):
        body = (body or "").replace(full_title, short_title)
        body = enforce_product_name_limit(
            body=body,
            product_name=short_title,
            prior_touches=prior_touches,
            sequence_first_touch=sequence_first,
            brand=str((campaign or {}).get("brand_name") or short_title),
        )
    # FU intermedios: ángulo de valor nuevo (el último/breakup puede ir vacío).
    from app.services.sequence_followup_value_guard import enforce_followup_value_angle

    body = enforce_followup_value_angle(
        body=body or "",
        product=product_copy,
        prior_touches=prior_touches,
        sequence_first_touch=sequence_first,
        step_day=step_day,
    )
    # Último: UN solo CTA de reunión por mensaje (nunca dos).
    from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

    body = enforce_one_meeting_cta_per_message(body or "")
    # Orden fijo: inicio+apalancamiento → info → CTA → cierre.
    from app.services.sequence_structure_order_guard import (
        enforce_outbound_structure_order,
    )

    body = enforce_outbound_structure_order(body or "")
    # Último: CTA profesional sin minutos («viene bien» → «queda bien»).
    body = polish_outbound_meeting_cta(body or "")
    return subject, body, reasoning


def _generate_sdr_playbook_touch_raw(
    *,
    channel: Channel,
    prospect: dict[str, str],
    campaign: dict[str, str],
    product: dict[str, str],
    education: str,
    step_day: int,
    step_objective: str,
    prior_touches: list[dict[str, Any]],
    tone: str = "",
    prospect_model: Any | None = None,
    campaign_model: Any | None = None,
    seller_style: Any | None = None,
    sequence_first_touch: bool | None = None,
) -> tuple[str | None, str, SdrReasoningRead]:
    """
    1º toque: OpenAI desde ficha de producto (cualquier producto) → banco si falla.
    Follow-up / Mi estilo: banco o plantilla del SDR.
    """
    from app.services.cold_message_bank import render_cold_bank_touch
    from app.services.outbound_text_normalize import normalize_outbound_email_body
    from app.services.seller_style_compose import (
        render_style_guided_cold_body,
        render_style_guided_linkedin,
        seller_template_for_channel,
    )

    # Regla 11 — cold solo en el 1º toque de la SECUENCIA (no del canal).
    # Si ya hubo mail/WA, el 1º LI también es FU.
    from app.services.cold_message_bank import is_sequence_first_touch

    seq_first = (
        is_sequence_first_touch(prior_touches)
        if sequence_first_touch is None
        else bool(sequence_first_touch) and is_sequence_first_touch(prior_touches)
    )
    product = _product_for_outreach_copy(product)
    seller_template = (
        seller_template_for_channel(
            seller_style,
            channel,
            seed=f"{prospect.get('id') or ''}|{campaign.get('id') or ''}",
            first_touch=seq_first,
            step_day=step_day,
        )
        if seller_style is not None
        else None
    )
    if seller_template:
        from app.services.seller_style_templates import is_variant_turn

        rendered = render_cold_bank_touch(
            channel=channel,
            prospect=prospect,
            campaign=campaign,
            product=product,
            prior_touches=prior_touches,
            first_touch=seq_first,
            step_day=step_day,
            seller_template=seller_template,
            seller_variant=is_variant_turn(
                prospect.get("id"),
                f"{campaign.get('id') or ''}|{channel}|{'cold' if seq_first else 'fu'}",
            ),
        )
        from app.services.sequence_cold_bank_guard import cold_bank_forbidden

        if cold_bank_forbidden(
            prior_touches=prior_touches,
            template_id=getattr(rendered, "template_id", None),
        ):
            rendered = render_cold_bank_touch(
                channel=channel,
                prospect=prospect,
                campaign=campaign,
                product=product,
                prior_touches=prior_touches,
                first_touch=False,
                step_day=step_day,
            )
        if rendered.template_id.startswith("seller-"):
            body = rendered.body
            reasoning = rendered.reasoning
            reasoning.bank_template_id = rendered.template_id
            return _guard_campaign_product_isolation(
                rendered.subject,
                body,
                reasoning,
                channel=channel,
                prospect=prospect,
                campaign=campaign,
                product=product,
                prior_touches=prior_touches,
                step_day=step_day,
                sequence_first_touch=seq_first,
            )

    if (
        channel == "linkedin"
        and seq_first
        and seller_style is not None
        and prospect_model is not None
        and campaign_model is not None
    ):
        styled = render_style_guided_linkedin(prospect_model, campaign_model, style=seller_style)
        if styled:
            from app.services.message_structure_variants import pick_first_touch_variant

            return _guard_campaign_product_isolation(
                None,
                styled,
                SdrReasoningRead(
                    why_it_matters="Borrador guiado por ejemplos del SDR (Mi estilo)",
                    response_question="CTA inferido del ejemplo del SDR",
                    bank_template_id="seller_style_linkedin",
                    structure_variant=pick_first_touch_variant(
                        channel="linkedin",
                        prospect_id=prospect.get("id"),
                        campaign_id=campaign.get("id"),
                    ),
                ),
                channel=channel,
                prospect=prospect,
                campaign=campaign,
                product=product,
                prior_touches=prior_touches,
                step_day=step_day,
                sequence_first_touch=True,
            )

    # Cualquier producto: 1º toque desde ficha vía OpenAI; banco solo como respaldo.
    if seq_first:
        openai_touch = _try_openai_first_touch(
            channel=channel,
            prospect=prospect,
            campaign=campaign,
            product=product,
            education=education,
            step_day=step_day,
            step_objective=step_objective,
            prior_touches=prior_touches,
            tone=tone,
        )
        if openai_touch is not None:
            # OpenAI ya filtra wrong-product; scrub brand por si acaso.
            subj, body, reasoning = openai_touch
            return _guard_campaign_product_isolation(
                subj,
                body,
                reasoning,
                channel=channel,
                prospect=prospect,
                campaign=campaign,
                product=product,
                prior_touches=prior_touches,
                step_day=step_day,
                sequence_first_touch=True,
            )

    rendered = render_cold_bank_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        prior_touches=prior_touches,
        first_touch=seq_first,
        step_day=step_day,
    )
    # Regla 11 — si por cualquier path quedó cold con historial, forzar FU.
    from app.services.sequence_cold_bank_guard import cold_bank_forbidden

    if cold_bank_forbidden(
        prior_touches=prior_touches,
        template_id=getattr(rendered, "template_id", None),
    ):
        rendered = render_cold_bank_touch(
            channel=channel,
            prospect=prospect,
            campaign=campaign,
            product=product,
            prior_touches=prior_touches,
            first_touch=False,
            step_day=step_day,
        )
    body = rendered.body
    subject = rendered.subject
    if seller_style is not None:
        seed = f"{prospect.get('id') or ''}|{campaign.get('id') or ''}|{step_day}"
        body = render_style_guided_cold_body(
            channel=channel,
            body=body,
            style=seller_style,
            seed=seed,
            first_touch=seq_first,
            known_terms=(
                campaign.get("sender_name") or "",
                campaign.get("brand_name") or campaign.get("company_name") or "",
                product.get("name") or "",
            ),
        )
    if channel == "email":
        body = normalize_outbound_email_body(body)
    _ = education  # incluye estilo SDR para paths LLM; cold bank usa seller_style directo

    from app.services.message_structure_variants import (
        pick_first_touch_variant,
        pick_follow_up_variant,
    )

    if seq_first:
        structure_variant = pick_first_touch_variant(
            channel=channel,
            prospect_id=prospect.get("id"),
            campaign_id=campaign.get("id"),
        )
    else:
        structure_variant = pick_follow_up_variant(
            channel=channel,
            prospect_id=prospect.get("id"),
            campaign_id=campaign.get("id"),
            step_day=step_day,
        )
    reasoning = rendered.reasoning
    reasoning.bank_template_id = rendered.template_id
    reasoning.structure_variant = structure_variant

    from app.services.outreach_language import (
        adapt_outbound_text_to_language,
        language_from_campaign_dict,
    )

    lang = language_from_campaign_dict(campaign)
    subject, body = adapt_outbound_text_to_language(
        body=body,
        subject=subject,
        channel=channel,
        lang=lang,
    )
    return _guard_campaign_product_isolation(
        subject,
        body,
        reasoning,
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        prior_touches=prior_touches,
        step_day=step_day,
        sequence_first_touch=seq_first,
    )
