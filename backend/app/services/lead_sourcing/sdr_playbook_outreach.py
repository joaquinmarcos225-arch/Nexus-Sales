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

OBJETIVO ÚNICO: agendar una reunión corta (5–10 min). NUNCA intentar cerrar la venta.

PERSONALIZACIÓN (lo que más vende hoy):
- Usá SIEMPRE la INVESTIGACIÓN PREVIA y/o datos CRM (empresa, rol, señales públicas).
- B2B: gancho sobre la EMPRESA o el equipo del prospecto.
- B2C: gancho sobre la PERSONA (perfil, rol, trayectoria).
- Si no hay dato confirmado: gancho suave con empresa/rol CRM. PROHIBIDO inventar hechos.

PRODUCTO DE CAMPAÑA (OBLIGATORIO):
Leé PRODUCTO SELECCIONADO. Problema + solución deben anclarse a ESE producto (dolor sectorial o beneficio real), no a features de relleno.

PRIMER TOQUE — estructura fija (adaptá longitud al canal):
1) Saludo cordial con nombre
2) Presentación + gancho: Soy [SDR]. Una frase que demuestre investigación (LinkedIn/empresa/persona)
3) Problema + solución: en pocas líneas, el dolor/necesidad que resolvés (sector/rol) + beneficio concreto — NO lista de características
4) CTA de muy bajo esfuerzo: reunión/videollamada corta (5–10 min), idealmente con día/horario tentativo
5) Firma (email): Saludos, [SDR]

PROHIBIDO:
- Culpa directa ("seguramente te pasa", "¿te sucede esto?")
- Pitch de features / cerrar venta
- CTAs genéricos ("¿Qué opinas?", "¿Detectas esta dificultad?")
- Mensajes mucho más largos que los ejemplos del playbook

Texto plano. Sin markdown.
"""

_SDR_PLAYBOOK_FOLLOW_UP_SYSTEM = """
Sos un SDR outbound senior (español). Escribís follow-ups humanos, breves y con valor NUEVO.

REGLA DE ORO: NUNCA digas "¿pudiste leer/revisar mi mensaje anterior?" ni reproches falta de respuesta.
Eso genera culpa y fricción. El mensaje anterior es solo hilo conductor.

ESTRUCTURA DE SEGUIMIENTO (todos los canales):
1) Puente contextual ultra corto (dejar arriba en bandeja / paso rápido / olvidé mencionarte…)
2) Nuevo ángulo de valor: dato, testimonio corto, beneficio distinto o recurso — NO repetir el pitch del Día 1
3) CTA de baja fricción: volver a proponer reunión corta o facilitar la respuesta (esta tarde vs lunes)

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


def _product_context_block(product: dict[str, str], *, for_follow_up: bool = False) -> str:
    if not product or not (
        product.get("name")
        or product.get("description")
        or product.get("original_description")
    ):
        return "PRODUCTO: no configurado — usá solo contexto de campaña.\n\n"
    orig = (product.get("original_description") or product.get("description") or "—")[:2400]
    summary = (product.get("interpreted_summary") or product.get("value_proposition") or "—")[:1200]
    problems = (product.get("extracted_problems") or product.get("pain_points") or "—")[:1200]
    benefits = (product.get("extracted_benefits") or product.get("benefits") or "—")[:1200]
    lines = [
        "PRODUCTO SELECCIONADO EN CAMPAÑA (referencia interna):",
        f"Nombre: {product.get('name') or '—'}",
        f"Descripción original:\n{orig}",
        f"Resumen interpretado (propuesta de valor):\n{summary}",
        f"Problemas que resuelve (extraídos):\n{problems}",
        f"Beneficios que aporta (extraídos):\n{benefits}",
    ]
    if for_follow_up:
        lines.append(
            "MODO FOLLOW-UP: el pitch completo ya se envió en el primer toque. "
            "Usá esta info solo para un ÁNGULO NUEVO (dato, caso, beneficio distinto). "
            "NO repitas la explicación del producto. NUNCA reproches falta de lectura."
        )
    else:
        lines.append(
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
    )
    return f"{approved}\n{shape}\n"


def _channel_rules(channel: Channel, *, first_touch: bool, step_day: int = 1) -> str:
    if first_touch:
        if channel == "email":
            return (
                "CANAL Email primer toque: body ideal 70-110 palabras. "
                "Subject breve con curiosidad, personalizado a la empresa del prospecto. "
                "Bloques grandes editables (sin plantilla de audiencia fija) + CTA reunión.\n"
            )
        if channel == "linkedin":
            return (
                "CANAL LinkedIn primer toque: corto pero podés explayarte más que WhatsApp. "
                "Ideal ~280-480 caracteres (máx ~550). Sin subject. "
                "Dividí en párrafos cortos (línea en blanco entre saludo/presentación, valor y CTA). "
                "PROHIBIDO un solo bloque enorme. PROHIBIDO pegar ficha de producto; "
                "reescribir valor en 2–3 oraciones conversacionales.\n"
            )
        return (
            "CANAL WhatsApp primer toque: MÁS CORTO que LinkedIn. "
            "Ideal 20-35 palabras (máx ~45 / ~260 caracteres). "
            "Informal, chill, rioplatense. Dividí en 2–3 micro-párrafos "
            "(saludo; idea de valor corta; CTA). PROHIBIDO párrafo muro. CTA reunión corta.\n"
        )
    if channel == "email":
        return (
            "CANAL Email follow-up: modo despedida suave, sin re-pitch completo, "
            "CTA liviano + «Quedo atento». Preferí mismo hilo (Re:). 50-90 palabras.\n"
        )
    if channel == "linkedin":
        return (
            "CANAL LinkedIn follow-up: corto (podés 2 párrafos breves), despedida suave, "
            "sin culpa, «Quedo atento». Sin subject.\n"
        )
    return (
        "CANAL WhatsApp follow-up: ultra corto, chill, 2 micro-párrafos máx, "
        "despedida suave, «Quedo atento». Sin culpa.\n"
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
    product_block = _product_context_block(product, for_follow_up=not first_touch)
    role_block = _role_context_block(prospect, campaign)
    edu_block = f"{education[:1400]}\n\n" if (education or "").strip() else ""
    research = (prospect.get("research_brief") or "").strip()
    # Evitar duplicar si education ya trae el brief.
    research_block = ""
    if research and research[:80] not in (education or ""):
        research_block = f"{research[:1200]}\n\n"
    return (
        f"Playbook Día {step_day}. Objetivo: {step_objective}\n"
        f"{_channel_rules(channel, first_touch=first_touch, step_day=step_day)}\n"
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
            "  cta = reunión 5–10 min con ¿...?\n"
            "OBLIGATORIO: saludo + presentación con gancho + problema/solución + CTA reunión. Nunca cerrar venta.\n\n"
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
        return acc

    acc.extend(_validate_base_text(body, "body"))
    acc.extend(_collect_pattern_matches(body, "body", _GENERIC_CORPORATE, "lenguaje corporativo genérico"))
    acc.extend(_collect_pattern_matches(body, "body", _PRODUCT_PITCH, "re-pitch de producto prohibido en follow-up"))

    if step_day in (4, 7, 10, 13) and not _has_followup_bridge(body):
        acc.issues.append(
            f"Día {step_day}: debe tener puente contextual corto (sin reprochar falta de lectura)"
        )
    if step_day in (16, 19) and not (
        _has_followup_bridge(body) or _BREAKUP_MARKERS.search(body)
    ):
        acc.issues.append(
            f"Día {step_day}: debe cerrar con despedida suave (p. ej. Quedo atento)"
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
            f"Día {step_day}: debe invitar a conversar/reunión o cerrar con Quedo atento"
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
        " Follow-up modo despedida suave: bloque corto editable (sin re-pitch del Día 1), "
        "CTA liviano a reunión si aplica, y cierre con «Quedo atento». "
        "PROHIBIDO: ¿pudiste leer/revisar?, sin respuesta, culpa."
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
    invented_hook = re.compile(
        r"(vi en linkedin|vi que .{0,40}crec|sigue creciendo|crecimiento de|"
        r"en estos d[ií]as|este trimestre|le[ií] que|me enter[eé] que)",
        re.I,
    )
    if invented_hook.search(t):
        if no_confirmed:
            acc.issues.append(
                f"{field}: no inventes ganchos (LinkedIn/crecimiento/news) sin evidencia "
                "en la investigación previa; usá rol/empresa del CRM"
            )
        else:
            # Exigir que al menos un token del brief aparezca en el gancho.
            brief_tokens = {
                w
                for w in re.findall(r"[a-záéíóúñ]{5,}", brief)
                if w
                not in {
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
                }
            }
            company_tokens = {
                w for w in re.findall(r"[a-záéíóúñ]{3,}", (prospect_company or "").lower())
            }
            allowed = brief_tokens | company_tokens
            if allowed and not any(tok in low for tok in allowed):
                acc.issues.append(
                    f"{field}: el gancho debe usar un dato del brief o la empresa del CRM; "
                    "no inventes señales"
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
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        if n < 200 or n > 550:
            acc.issues.append(
                f"longitud: LinkedIn primer toque tiene {n} caracteres (obligatorio 200-550)"
            )
        elif len(paras) < 2:
            acc.issues.append(
                "formato: LinkedIn primer toque debe dividirse en párrafos "
                "(línea en blanco entre bloques; no un solo muro de texto)"
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
) -> str:
    company = _subject_company_label(str(prospect.get("company_name") or ""))
    templates = {
        1: f"Automatización de prospección para {company}",
        10: f"Prospección manual en {company}",
        19: f"Prospección en {company}",
    }
    if step_day in templates:
        return templates[step_day][:72]
    raw = (raw_subject or "").strip()
    if raw and not _GENERIC_EMAIL_SUBJECTS.match(raw):
        return raw[:72]
    return raw or "Seguimiento"


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
) -> tuple[str | None, str, SdrReasoningRead]:
    """
    Cold / follow-up outbound: banco determinístico por canal (B2B/B2C).
    Sin inventar industria; valor desde ficha de producto.
    """
    del education, step_objective, tone  # contexto LLM legacy; el banco no lo usa
    from app.services.cold_message_bank import first_touch_on_channel, render_cold_bank_touch
    from app.services.outbound_text_normalize import normalize_outbound_email_body

    first_on_ch = first_touch_on_channel(prior_touches, channel)
    rendered = render_cold_bank_touch(
        channel=channel,
        prospect=prospect,
        campaign=campaign,
        product=product,
        prior_touches=prior_touches,
        first_touch=first_on_ch,
        step_day=step_day,
    )
    body = rendered.body
    subject = rendered.subject
    if channel == "email":
        body = normalize_outbound_email_body(body)
    return subject, body, rendered.reasoning
