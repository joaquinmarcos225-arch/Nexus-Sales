"""Generación playbook SDR outbound — claridad sobre qué hacemos, no suposiciones de dolor."""

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
_EMAIL_DAY1_WORDS_MAX = 130
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

# Suposiciones de dolor / hipótesis sobre el prospecto — prohibidas en todo el mensaje.
_PAIN_ASSUMPTION_BANNED = re.compile(
    r"(seguramente\s+(?:te|le)\s+(?:pasa|ocurre|enfrent|tien)|"
    r"probablemente\s+(?:te|le|teng)|"
    r"suelen?\s+(?:tener|pasar|enfrentar|sufrir|encontrar|padecer)|"
    r"much[oa]s\s+(?:empresas|equipos|organizaciones|compa[nñ][ií]as|especialistas)\s+"
    r"(?:tienen|sufren|enfrentan|padecen|experimentan)|"
    r"a\s+menudo\s+(?:ven|tienen|sufren|enfrentan|padecen)|"
    r"hablando\s+con\s+(?:l[ií]deres|directores|equipos|especialistas)|"
    r"en\s+empresas\s+similares|"
    r"¿(?:te|le)\s+(?:pasa|sucede|identific|reconoc)|"
    r"detect(?:ás|as)\s+esta\s+dificultad|"
    r"(?:los|las)\s+\w+\s+suelen\s+tener)",
    re.I,
)

# Lenguaje corporativo genérico.
_GENERIC_CORPORATE = re.compile(
    r"(consolid(?:ar|a)\s+informaci[oó]n|centraliz(?:ar|a)\s+informaci[oó]n|"
    r"mejora(?:r)?\s+(?:la\s+)?eficiencia|optimiza(?:r)?\s+procesos|"
    r"datos relevantes|decisiones [áa]giles|potencia(?:r)?\s+productividad|"
    r"informaci[oó]n dispersa|visibilidad(?:\s+unificada)?|procesos m[aá]s eficientes|"
    r"alinear equipos|sinergias|transformaci[oó]n digital|best practices|"
    r"operaciones m[aá]s [áa]giles|toma de decisiones|datos confiables)",
    re.I,
)

_PRESENTATION_OK = re.compile(
    r"(soy\s+\S+|te escribo desde|escribo desde|desde\s+\S+|mi nombre es)",
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
Sos un SDR outbound B2B senior (español). Escribís claro, directo y breve.

OBJETIVO: mostrar qué hacemos, qué resultado generamos, y abrir una conversación (reunión breve).
NO intentás convencer al prospecto de que tiene un problema.

PRODUCTO DE CAMPAÑA (OBLIGATORIO):
Leé el bloque PRODUCTO SELECCIONADO. El mensaje debe basarse en ESE producto y su resultado.
PROHIBIDO inventar propuesta de valor genérica ni suponer dolores del prospecto.

PRIMER TOQUE — estructura fija:
1) Saludo con nombre del prospecto
2) Presentación: Soy [SDR] de [empresa/producto]
3) Por qué escribo: "Te escribo porque ayudamos a..." (resultado principal, sin suposiciones)
4) Qué hacemos: explicación breve del producto o proceso
5) Qué resultado genera: beneficio concreto en lenguaje simple (ej. reducir tiempo manual de prospección, contactar más prospectos en menos tiempo, centralizar outreach, más conversaciones reales)
6) Invitación a reunión/conversación breve

PROHIBIDO:
- Suponer dolores ("seguramente te pasa", "suelen tener problemas", "muchas empresas sufren")
- Lenguaje corporativo vacío
- CTAs genéricos ("¿Te sucede esto?", "¿Qué opinas?", "¿Detectas esta dificultad?")

Texto plano. Sin markdown. Más corto es mejor.
"""

_SDR_PLAYBOOK_FOLLOW_UP_SYSTEM = """
Sos un SDR outbound B2B senior (español). Escribís seguimientos humanos, breves y que EVOLUCIONAN.

REGLA DE ORO — SECUENCIA 21 DÍAS:
- Día 1 = único toque con explicación completa del producto (qué hacemos + resultado + reunión).
- Días 4, 7, 13, 16, 19 = más corto, más humano, MENOS vendedor. NO repetir el pitch del Día 1.
- Día 10 = aportar valor (dato, caso, aprendizaje). NO repetir el pitch.

OBLIGATORIO:
- Leé el HISTORIAL de toques anteriores y referenciá con naturalidad.
- Cada toque tiene UN objetivo distinto (routing, continuar sí/no, valor, timing, cierre).
- Máximo UNA frase recordatoria del tema si hace falta — nunca re-explicar producto entero.

PROHIBIDO en follow-ups:
- Reescribir el pitch del Día 1 ("Te escribo porque… Lo hacemos mediante… Esto les permite…")
- Listar features, beneficios completos o pedir reunión como en el primer contacto (salvo invitación muy suave en Día 10)
- Suponer dolores del prospecto
- Lenguaje corporativo vacío

Texto plano. Sin markdown.
"""

_PRIOR_TOUCH_REFERENCE = re.compile(
    r"(te hab[ií]a escrito|retomo|mis mensajes|mensaje anterior|mensajes anteriores|"
    r"seguimiento|hace unos d[ií]as|como coment[eé]|en mi (?:email|mensaje)|"
    r"sin respuesta|sin novedades|sin tu respuesta)",
    re.I,
)

_HUMAN_FOLLOWUP_CTA = re.compile(
    r"(persona indicada|alguien m[aá]s del equipo|con qui[eé]n|"
    r"tiene sentido seguir|prefer[ií]s que|dej(?:emos|ar)lo para|"
    r"no est[aá] en agenda|evaluando para este a[nñ]o|"
    r"seguir conversando|dejar para m[aá]s adelante|"
    r"prioridad ahora|en el radar|seguir o|dejarlo para)",
    re.I,
)

_VALUE_ADD_MARKERS = re.compile(
    r"(caso|cliente|empresa|dato|%|\d+\s*%|aprendizaje|insight|"
    r"estudio|tendencia|observamos|vimos que|logr[oó]|redujo|aument[oó])",
    re.I,
)

_BREAKUP_MARKERS = re.compile(
    r"(cierro|no seguir ocupando|quedo a disposici[oó]n|puerta abierta|"
    r"no tuve respuesta|dejo esta conversaci[oó]n|saludos|"
    r"m[aá]s adelante tiene sentido)",
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
            "MODO FOLLOW-UP: el pitch completo ya se envió en Día 1. "
            "Usá esta info solo como contexto — NO repitas la explicación del producto. "
            "Día 10: podés citar un dato/caso relacionado; resto de días: máximo una frase recordatoria."
        )
    else:
        lines.append(
            "Usá esta info para explicar QUÉ HACEMOS y QUÉ RESULTADO genera. "
            "NO inventes dolores del prospecto ni supongas que 'seguramente le pasa' algo."
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
            text, field, _PAIN_ASSUMPTION_BANNED, "suposición de dolor/problema prohibida"
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
    if text and not _WHY_WRITE_MARKERS.search(text):
        acc.issues.append(
            f'{field}: debe explicar por qué escribís (ej. "Te escribo porque ayudamos a…")'
        )
    if text and not _RESULT_MARKERS.search(text):
        acc.issues.append(f"{field}: debe mencionar el resultado principal que generamos")
    return acc


def _validate_solution_text(text: str, field: str, *, how_context: str = "") -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_validate_base_text(text, field))
    acc.extend(_collect_pattern_matches(text, field, _GENERIC_CORPORATE, "explicación genérica/corporativa"))
    if text and not _mentions_how_we_do_it(text, how_context=how_context):
        acc.issues.append(
            f'{field}: debe explicar qué hacemos / cómo (ej. "Lo hacemos mediante…", '
            f'"Utilizamos…", "Automatizamos…" + funcionamiento concreto)'
        )
    return acc


def _validate_banned_text(text: str, field: str) -> _ValidationAccum:
    return _validate_pitch_text(text, field)


def _is_first_touch(prior_touches: list[dict[str, Any]]) -> bool:
    return not prior_touches


def _prior_touches_block(prior: list[dict[str, Any]]) -> str:
    if not prior:
        return "HISTORIAL: primer contacto — no hay toques anteriores.\n\n"

    lines = [
        "HISTORIAL COMPLETO DE TOQUES SIN RESPUESTA.",
        "La secuencia EVOLUCIONA: el pitch completo del producto ya fue en Día 1.",
        "OBLIGATORIO: referenciá toques anteriores con naturalidad — NO repitas el mismo pitch.",
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
) -> str:
    sender = sender_name.strip() or "[nombre SDR]"
    brand = brand_name.strip() or "[empresa/producto]"
    first = prospect_first_name.strip() or "[Nombre]"
    role = prospect_role.strip() or "rol del prospecto"
    industry = prospect_industry.strip() or "industria del prospecto"

    base = f"""
ESTRUCTURA OBLIGATORIA — PRIMER TOQUE (sections en JSON + body armado):
Audiencia: {role} · {industry}. Claridad sobre qué hacemos — sin suponer dolores.

LÍNEA 1 — greeting: "Hola {first}," / "Buen día {first}," / "Buenas {first},"
LÍNEA 2 — presentation: "Soy {sender} de {brand}." (obligatorio este formato)

BLOQUE problem (por qué escribo): "Te escribo porque ayudamos a [resultado principal]…"
  Enfocado en el resultado que generamos, NO en suponer dolores del prospecto.
  PROHIBIDO: "seguramente te pasa", "suelen tener problemas", "muchas empresas sufren".

BLOQUE solution (qué hacemos / cómo): "Lo hacemos mediante…" / "Lo logramos mediante…" / "Utilizamos…" / "Automatizamos…"
  Explicá herramienta + funcionamiento (automatización, canales, integración). Basado en el PRODUCTO SELECCIONADO.

BLOQUE benefits (qué resultado genera): "Esto les permite [beneficio concreto]…"
  Resultado tangible en lenguaje simple. Ejemplos válidos:
  - reducir el tiempo manual de prospección
  - contactar más prospectos en menos tiempo
  - centralizar outreach por Mail, WhatsApp y LinkedIn
  - ayudar al SDR a dedicar más tiempo a conversaciones reales
  - generar más oportunidades comerciales sin aumentar carga manual

internal.probable_problem = el mismo resultado concreto (NO dolor del prospecto).

BLOQUE cta: Invitación a reunión/conversación breve (termina en ?). Ejemplos:
  - "¿Te interesaría coordinar una reunión breve para mostrarte cómo funciona?"
  - "¿Tendría sentido conversar 10 minutos para mostrarte cómo aplica?"
  PROHIBIDO: "¿Te sucede esto?" / "¿Detectas esta dificultad?" / "¿Qué opinas?"

Completá las 6 sections. body = greeting + presentation (líneas seguidas) + bloques separados por línea en blanco.
Más corto y directo es mejor.
"""
    if channel == "email":
        return (
            base
            + "Email: ideal 70-110 palabras; aceptable 55-130. Más corto y directo es mejor.\n"
        )
    if channel == "linkedin":
        return base + "LinkedIn: misma estructura, 250-380 caracteres, ultra conciso.\n"
    return base + "WhatsApp: misma estructura, 1-4 líneas, ultra conciso.\n"


def _assemble_first_touch_body(sections: dict[str, Any]) -> str:
    opening: list[str] = []
    for key in ("greeting", "presentation"):
        val = str(sections.get(key) or "").strip()
        if val:
            opening.append(val)
    parts: list[str] = []
    if opening:
        parts.append("\n".join(opening))
    for key in ("problem", "solution", "benefits", "cta"):
        val = str(sections.get(key) or "").strip()
        if val:
            parts.append(val)
    return "\n\n".join(parts)


def _follow_up_structure_block(*, step_day: int, channel: Channel) -> str:
    evolution = (
        "SECUENCIA EVOLUTIVA: el pitch completo del producto SOLO fue en Día 1. "
        "Este toque debe ser más corto, más humano y con un objetivo distinto.\n"
        "PROHIBIDO reescribir el pitch del Día 1 (qué hacemos + cómo + beneficios + reunión).\n"
    )
    if step_day == 4 and channel == "linkedin":
        return evolution + """
DÍA 4 — LinkedIn · SEGUIMIENTO HUMANO (NO re-vender el producto):

Objetivo: referenciar el email del Día 1 y preguntar si es la persona indicada o a quién hablar.

Tono ejemplo (adaptá al historial, no copies literal):
"Hola [Nombre].
Te había escrito hace unos días porque ayudamos a empresas a [resultado en UNA frase].
Quería saber si sos la persona indicada para evaluar este tipo de iniciativas o si debería hablar con alguien más del equipo."

Reglas:
- Referenciá el Día 1 (email) explícitamente.
- Máximo UNA frase recordatoria del tema — NO re-explicar producto, features ni beneficios.
- CTA humano de routing (¿persona indicada? ¿con quién hablar?).
- 180-320 caracteres. Sin pedir reunión como en Día 1.
"""
    if step_day == 7 and channel == "whatsapp":
        return evolution + """
DÍA 7 — WhatsApp · CONTACTO RÁPIDO (NO explicar producto):

Objetivo: retomar mensajes previos y preguntar si tiene sentido seguir o dejarlo.

Tono ejemplo:
"Hola [Nombre].
Retomo mis mensajes anteriores para no insistir por distintos canales sin sentido.
¿Tiene sentido seguir conversando sobre este tema o preferís que lo deje para más adelante?"

Reglas:
- 1-3 líneas. Ultra breve.
- Referenciá email/LinkedIn previos.
- NO expliques qué hacemos ni cómo funciona el producto.
- CTA sí/no o continuar vs dejarlo.
"""
    if step_day == 10 and channel == "email":
        return evolution + """
DÍA 10 — Email · APORTAR VALOR (NO repetir pitch):

Objetivo: compartir un dato, mini caso o aprendizaje relacionado con el problema que resuelve el producto.

Reglas:
- Referenciá toques anteriores sin respuesta (Día 1, 4, 7).
- El valor es el protagonista: dato concreto, caso breve, insight del sector.
- NO repitas la estructura del Día 1 (presentación + qué hacemos + cómo + beneficios).
- Cierre con invitación SUAVE a conversar (no pitch de reunión como Día 1).
- 50-90 palabras.
"""
    if step_day == 13 and channel == "linkedin":
        return evolution + """
DÍA 13 — LinkedIn · NUEVO ÁNGULO (timing / prioridad):

Objetivo: preguntar si el tema está en agenda o no es prioridad ahora.

Tono ejemplo:
"Hola [Nombre].
Capaz este tema no es prioridad ahora mismo.
¿Es algo que están evaluando para este año o directamente no está en agenda?"

Reglas:
- Referenciá la secuencia previa brevemente.
- NO re-expliques producto ni pidas reunión.
- 150-280 caracteres. Pregunta directa sobre timing/prioridad.
"""
    if step_day == 16 and channel == "whatsapp":
        return evolution + """
DÍA 16 — WhatsApp · ÚLTIMO INTENTO HUMANO (NO explicar producto):

Objetivo: cierre activo — ¿seguimos o lo dejamos?

Tono ejemplo:
"Hola [Nombre].
Retomo mis mensajes anteriores y no te molesto más.
Solo quería saber si tiene sentido seguir conversando o si preferís que lo dejemos para más adelante."

Reglas:
- 1-3 líneas. Referenciá historial.
- NO expliques producto. NO pidas reunión.
- CTA mínimo esfuerzo (sí/no, seguir o dejar).
"""
    if step_day == 19 and channel == "email":
        return evolution + """
DÍA 19 — Email · RUPTURA ELEGANTE (NO vender, NO explicar, NO insistir):

Objetivo: cerrar la secuencia con respeto y dejar puerta abierta.

Tono ejemplo:
"Hola [Nombre].
Como no tuve respuesta, cierro esta conversación para no seguir ocupando espacio en tu bandeja.
Si más adelante tiene sentido retomar el tema, quedo a disposición.
Saludos."

Reglas:
- Referenciá que no hubo respuesta (sin culpar).
- PROHIBIDO: pitch, explicar producto, pedir reunión, presionar.
- 40-70 palabras. Despedida profesional.
"""
    return evolution + """
SEGUIMIENTO — toque posterior al Día 1:
- Referenciá al menos un toque anterior.
- Objetivo único y humano. NO repetir pitch del Día 1.
- Más corto que el mensaje anterior. CTA de baja fricción.
"""


def _channel_rules(channel: Channel, *, first_touch: bool, step_day: int = 1) -> str:
    if first_touch:
        if channel == "email":
            return (
                "CANAL Email Día 1: body ideal 70-110 palabras, aceptable 55-130. "
                "Subject 3-6 palabras (resultado o qué hacemos).\n"
            )
        if channel == "linkedin":
            return "CANAL LinkedIn Día 1: body 250-380 caracteres. Sin subject.\n"
        return "CANAL WhatsApp Día 1: 1-4 líneas. Ultra directo. Sin subject.\n"
    if step_day == 4:
        return "CANAL LinkedIn Día 4: 180-320 caracteres. Seguimiento humano, sin re-pitch.\n"
    if step_day == 7:
        return "CANAL WhatsApp Día 7: 1-3 líneas (~40-220 caracteres). Contacto rápido.\n"
    if step_day == 10:
        return "CANAL Email Día 10: 50-90 palabras. Valor + invitación suave. Subject breve.\n"
    if step_day == 13:
        return "CANAL LinkedIn Día 13: 150-280 caracteres. Ángulo timing/prioridad.\n"
    if step_day == 16:
        return "CANAL WhatsApp Día 16: 1-3 líneas (~40-220 caracteres). Último intento humano.\n"
    if step_day == 19:
        return "CANAL Email Día 19: 40-70 palabras. Ruptura elegante. Subject breve neutro.\n"
    if channel == "email":
        return "CANAL Email follow-up: breve, humano, sin re-pitch.\n"
    if channel == "linkedin":
        return "CANAL LinkedIn follow-up: breve, conversacional. Sin subject.\n"
    return "CANAL WhatsApp follow-up: 1-3 líneas. Ultra directo.\n"


def _structure_instructions(
    *,
    channel: Channel,
    step_day: int,
    prior_touches: list[dict[str, Any]],
    campaign: dict[str, str],
    prospect: dict[str, str],
) -> str:
    if _is_first_touch(prior_touches):
        first = (prospect.get("name") or "").split()[0] if prospect.get("name") else ""
        return _first_touch_structure_block(
            channel=channel,
            sender_name=str(campaign.get("sender_name") or ""),
            brand_name=str(campaign.get("brand_name") or ""),
            prospect_first_name=first,
            prospect_role=str(prospect.get("role") or ""),
            prospect_industry=str(prospect.get("industry") or ""),
        )
    return _follow_up_structure_block(step_day=step_day, channel=channel)


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
    first = pname.split()[0] if pname else "Hola"
    first_touch = _is_first_touch(prior_touches)
    sender = (campaign.get("sender_name") or "").strip() or "SDR"
    brand = (campaign.get("brand_name") or "").strip() or campaign.get("name") or "empresa"
    structure = _structure_instructions(
        channel=channel,
        step_day=step_day,
        prior_touches=prior_touches,
        campaign=campaign,
        prospect=prospect,
    )
    product_block = _product_context_block(product, for_follow_up=not first_touch)
    role_block = _role_context_block(prospect, campaign)
    edu_block = f"{education[:600]}\n\n" if (education or "").strip() else ""
    return (
        f"Playbook Día {step_day}. Objetivo: {step_objective}\n"
        f"{_channel_rules(channel, first_touch=first_touch, step_day=step_day)}\n"
        f"{structure}\n"
        f"Remitente SDR: {sender} · Empresa/producto: {brand}\n"
        f"Tono: {tone or campaign.get('tone') or 'profesional cercano'}\n\n"
        f"{role_block}"
        f"{product_block}"
        f"{edu_block}"
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
            "  probable_problem = resultado principal que generamos (NO dolor del prospecto)\n"
            "  why_it_matters = por qué escribimos / relevancia para su rol\n"
            "  hypothesis = qué hacemos / cómo lo hacemos (breve)\n"
            "  response_question = CTA de reunión\n"
            "sections (van al mensaje):\n"
            "  problem = por qué escribo (Te escribo porque ayudamos a…)\n"
            "  solution = qué hacemos (Lo hacemos mediante…)\n"
            "  benefits = qué resultado genera — concreto y simple (ej. reducir tiempo manual de prospección, "
            "contactar más prospectos en menos tiempo, centralizar outreach, más conversaciones reales)\n"
            "OBLIGATORIO en Día 1: saludo + presentación + qué hacemos + cómo + resultado + CTA.\n"
            "probable_problem y benefits deben nombrar el mismo resultado tangible.\n\n"
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
                'JSON: {"internal":{"probable_problem":"","why_it_matters":"","hypothesis":"","response_question":"","selling_to_role":""},'
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
    outcome_context: str = "",
    how_context: str = "",
) -> _ValidationAccum:
    acc = _ValidationAccum()
    if follow_up:
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
                    "suposición de dolor/problema prohibida",
                )
            )
    probable = str(internal.get("probable_problem") or "").strip()
    if probable and not _mentions_concrete_outcome(probable, outcome_context=outcome_context):
        acc.issues.append(
            "internal.probable_problem: debe describir el resultado que generamos, no un dolor del prospecto"
        )
    hypothesis = str(internal.get("hypothesis") or "").strip()
    if hypothesis and not _mentions_how_we_do_it(hypothesis, how_context=how_context):
        acc.issues.append("internal.hypothesis: debe explicar brevemente qué hacemos / cómo lo hacemos")
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
    acc.extend(_validate_base_text(body, "body"))
    acc.extend(_collect_pattern_matches(body, "body", _GENERIC_CORPORATE, "lenguaje corporativo genérico"))
    acc.extend(_collect_pattern_matches(body, "body", _PRODUCT_PITCH, "re-pitch de producto prohibido en follow-up"))

    if step_day in (4, 7, 10, 16) and not _PRIOR_TOUCH_REFERENCE.search(body):
        acc.issues.append(
            f"Día {step_day}: debe referenciar mensajes/toques anteriores del historial"
        )
    if step_day == 19 and not (
        _PRIOR_TOUCH_REFERENCE.search(body) or _BREAKUP_MARKERS.search(body)
    ):
        acc.issues.append("Día 19: debe indicar que no hubo respuesta antes de cerrar")

    pitch_blocks = _pitch_block_count(body)
    if step_day in (4, 7, 13, 16, 19) and pitch_blocks >= 2:
        acc.issues.append(
            f"Día {step_day}: no repetir estructura del pitch del Día 1 — mensaje más humano y breve"
        )
    if step_day == 10 and pitch_blocks >= 3:
        acc.issues.append("Día 10: aportá valor (dato/caso), no reescribas el pitch completo del Día 1")

    if step_day == 4:
        if not _HUMAN_FOLLOWUP_CTA.search(body):
            acc.issues.append(
                "Día 4: debe preguntar si es la persona indicada o con quién hablar del tema"
            )
        if _RE_PITCH_STRUCTURE.search(body):
            acc.issues.append("Día 4: no re-explicar cómo funciona el producto (máximo una frase recordatoria)")
        n = len(body)
        if n < 120 or n > 340:
            acc.issues.append(f"Día 4: LinkedIn debe tener 180-320 caracteres (tiene {n})")

    elif step_day == 7:
        if not _HUMAN_FOLLOWUP_CTA.search(body):
            acc.issues.append("Día 7: debe preguntar si seguir conversando o dejarlo para más adelante")
        if _WHAT_WE_DO_MARKERS.search(body) or _RE_PITCH_STRUCTURE.search(body):
            acc.issues.append("Día 7: no explicar el producto — solo retomar y preguntar")
        if len(body) > 280:
            acc.issues.append(f"Día 7: WhatsApp demasiado largo ({len(body)} caracteres, máx ~220)")

    elif step_day == 10:
        if not _VALUE_ADD_MARKERS.search(body):
            acc.issues.append("Día 10: debe aportar valor concreto (dato, caso, aprendizaje o insight)")
        wc = _word_count(body)
        if wc < 35 or wc > 100:
            acc.issues.append(f"Día 10: email de valor debe tener 50-90 palabras (tiene {wc})")

    elif step_day == 13:
        if not _HUMAN_FOLLOWUP_CTA.search(body):
            acc.issues.append("Día 13: debe preguntar sobre prioridad/timing (¿está en agenda este año?)")
        if _RE_PITCH_STRUCTURE.search(body):
            acc.issues.append("Día 13: nuevo ángulo humano — no re-explicar producto")
        n = len(body)
        if n < 100 or n > 300:
            acc.issues.append(f"Día 13: LinkedIn debe tener 150-280 caracteres (tiene {n})")

    elif step_day == 16:
        if not _HUMAN_FOLLOWUP_CTA.search(body):
            acc.issues.append("Día 16: debe preguntar si seguir o dejarlo — último intento humano")
        if _WHAT_WE_DO_MARKERS.search(body) or _RE_PITCH_STRUCTURE.search(body):
            acc.issues.append("Día 16: no explicar producto")
        if len(body) > 280:
            acc.issues.append(f"Día 16: WhatsApp demasiado largo ({len(body)} caracteres)")

    elif step_day == 19:
        if not _BREAKUP_MARKERS.search(body):
            acc.issues.append("Día 19: debe cerrar elegantemente (cierro conversación, quedo a disposición)")
        if _CONVERSATION_CTA.search(body) and re.search(r"reuni[oó]n", body, re.I):
            acc.issues.append("Día 19: ruptura elegante — no pedir reunión ni insistir")
        if _WHAT_WE_DO_MARKERS.search(body) or _RE_PITCH_STRUCTURE.search(body):
            acc.issues.append("Día 19: no vender ni explicar producto")
        wc = _word_count(body)
        if wc < 25 or wc > 85:
            acc.issues.append(f"Día 19: email de cierre debe tener 40-70 palabras (tiene {wc})")

    return acc


def _first_touch_retry_hint() -> str:
    return (
        " Día 1: incluí un resultado concreto en probable_problem, sections.benefits y body.resultado. "
        "Ejemplos: reducir tiempo manual de prospección; contactar más prospectos en menos tiempo; "
        "centralizar outreach; más conversaciones reales; más oportunidades sin aumentar carga manual."
    )


def _follow_up_retry_hint(step_day: int) -> str:
    hints = {
        4: " Seguimiento humano: referenciá Día 1, UNA frase del tema, preguntá si es la persona indicada.",
        7: " Contacto rápido: retomá mensajes previos, preguntá seguir o dejarlo. Sin explicar producto.",
        10: " Aportá un dato/caso/insight. No repitas el pitch del Día 1. Invitación suave al final.",
        13: " Preguntá si está en agenda o no es prioridad. Sin re-pitch.",
        16: " Último intento humano: ¿seguimos o lo dejamos? Sin producto.",
        19: " Ruptura elegante: cerrá sin vender ni explicar. Puerta abierta.",
    }
    return hints.get(step_day, " Secuencia evolutiva: no repetir pitch del Día 1.")


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
) -> _ValidationAccum:
    acc = _ValidationAccum()
    acc.extend(_validate_pitch_text(text, field))
    t = text.strip()
    if not re.search(r"^soy\s+", t, re.I):
        acc.issues.append(f'{field}: debe ser "Soy [nombre SDR] de [empresa]"')
    sender = sender_name.strip()
    brand = brand_name.strip()
    if sender:
        token = sender.split()[0].lower()
        if token not in t.lower():
            acc.issues.append(f"{field}: debe incluir el nombre del SDR ({sender})")
    if brand:
        brand_word = brand.split()[0].lower()
        if brand_word not in t.lower():
            acc.issues.append(f"{field}: debe incluir la empresa/producto ({brand})")
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
    first_name = (prospect.get("name") or "").split()[0] if prospect.get("name") else ""

    for key in _FIRST_TOUCH_SECTION_KEYS:
        if len(str(sections.get(key) or "").strip()) < 8:
            if key == "solution" and _mentions_how_we_do_it(how_context):
                continue
            acc.issues.append(f"sections.{key} faltante o incompleto")

    greeting = str(sections.get("greeting") or "").strip()
    presentation = str(sections.get("presentation") or "").strip()
    problem = str(sections.get("problem") or "").strip()
    solution = str(sections.get("solution") or "").strip()
    benefits = str(sections.get("benefits") or "").strip()
    cta = str(sections.get("cta") or "").strip()

    if greeting:
        acc.extend(_validate_greeting_text(greeting, "sections.greeting", first_name=first_name))
    if presentation:
        acc.extend(
            _validate_presentation_line(
                presentation, "sections.presentation", sender_name=sender_name, brand_name=brand_name
            )
        )
    if problem:
        acc.extend(_validate_why_write_text(problem, "sections.problem", prospect, campaign))
    if solution:
        acc.extend(
            _validate_solution_text(solution, "sections.solution", how_context=how_context)
        )
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
    first_name = (prospect.get("name") or "").split()[0] if prospect.get("name") else ""
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
    if len(paragraphs) < 5:
        acc.issues.append(
            "primer toque: body debe tener saludo+presentación, por qué escribimos, qué hacemos, resultado y CTA"
        )

    if paragraphs:
        opening_lines = paragraphs[0].split("\n")
        if opening_lines:
            acc.extend(
                _validate_greeting_text(opening_lines[0], "body.saludo", first_name=first_name)
            )
        if len(opening_lines) >= 2:
            acc.extend(
                _validate_presentation_line(
                    opening_lines[1],
                    "body.presentación",
                    sender_name=sender_name,
                    brand_name=brand_name,
                )
            )
        elif len(paragraphs) >= 1 and not re.search(r"^soy\s+", paragraphs[0], re.I | re.M):
            acc.issues.append('body: falta línea "Soy [SDR] de [empresa]"')

    if len(paragraphs) >= 2:
        acc.extend(_validate_why_write_text(paragraphs[1], "body.por_qué_escribo", prospect, campaign))
    solution_section = str((sections or {}).get("solution") or "").strip()
    solution_ok_in_sections = bool(
        solution_section and _mentions_how_we_do_it(solution_section, how_context=how_context)
    )
    if len(paragraphs) >= 3 and not solution_ok_in_sections:
        acc.extend(
            _validate_solution_text(
                paragraphs[2], "body.qué_hacemos", how_context=how_context
            )
        )
    elif len(paragraphs) < 3 and not solution_ok_in_sections:
        acc.issues.append("body.qué_hacemos: falta párrafo de qué hacemos / cómo")
    if len(paragraphs) >= 4:
        acc.extend(
            _validate_benefits_text(
                paragraphs[3], "body.resultado", outcome_context=outcome_context
            )
        )

    if len(paragraphs) >= 2:
        product_text = " ".join(paragraphs[1:4]) if len(paragraphs) >= 4 else " ".join(paragraphs[1:])
        acc.extend(_validate_product_alignment(product_text, "body (por qué escribimos/qué hacemos/resultado)", product))

    if paragraphs:
        last = paragraphs[-1]
        acc.extend(_collect_pattern_matches(last, "body.cta", _GENERIC_CTA_BANNED, "CTA genérico prohibido"))
        if not last.rstrip().endswith("?"):
            acc.issues.append("primer toque: debe terminar con invitación a reunión/conversación (?)")
        if not _CONVERSATION_CTA.search(last):
            acc.issues.append("primer toque: CTA final debe invitar a reunión o conversación breve")

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
        if n < 250 or n > 380:
            acc.issues.append(f"longitud: LinkedIn primer toque tiene {n} caracteres (obligatorio 250-380)")
    elif first_touch and channel == "whatsapp":
        line_count = len([ln for ln in body.splitlines() if ln.strip()])
        if line_count > 6 or len(body) > 450:
            acc.issues.append(
                f"longitud: WhatsApp primer toque demasiado largo ({line_count} líneas, {len(body)} caracteres)"
            )
    return acc


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
    first_touch = _is_first_touch(prior_touches)
    sender = str(campaign.get("sender_name") or "")
    brand = str(campaign.get("brand_name") or campaign.get("name") or "")
    user = _build_user_prompt(
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
    from app.services.openai_fallback import is_openai_fallback_enabled

    last_accum = _ValidationAccum()
    data: dict[str, Any] = {}
    if is_openai_fallback_enabled():
        attempts = 1
    else:
        attempts = 4 if first_touch else 3
    last_generation_debug: dict[str, Any] | None = None

    for attempt in range(1, attempts + 1):
        extra = ""
        if last_accum.issues:
            hint = (
                " Incluí saludo, presentación, por qué escribís, qué hacemos, resultado concreto y CTA de reunión. "
                "Basate en el PRODUCTO SELECCIONADO. Sin suposiciones de dolor."
                + _first_touch_retry_hint()
                if first_touch
                else _follow_up_retry_hint(step_day)
            )
            extra = "\n\nCORREGÍ: " + "; ".join(last_accum.issues) + "." + hint
        system_prompt = _SDR_PLAYBOOK_SYSTEM if first_touch else _SDR_PLAYBOOK_FOLLOW_UP_SYSTEM
        user_prompt = user + extra
        if first_touch:
            max_output_tokens = 850
        elif step_day == 10:
            max_output_tokens = 420
        elif step_day == 19:
            max_output_tokens = 280
        else:
            max_output_tokens = 320 if channel == "email" else 220
        temperature = random.uniform(0.55, 0.72)
        from app.services.openai_fallback import build_sdr_playbook_fallback_json

        chat = oai._raw_chat_with_meta(
            system_prompt,
            user_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            fallback_factory=lambda: build_sdr_playbook_fallback_json(
                channel=channel,
                prospect=prospect,
                campaign=campaign,
                product=product,
                step_day=step_day,
                step_objective=step_objective,
                prior_touches=prior_touches,
            ),
        )
        is_fallback = bool(getattr(chat, "fallback", False))
        last_generation_debug = _generation_debug_base(
            channel=channel,
            step_day=step_day,
            first_touch=first_touch,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_response=chat.text,
            model=chat.model,
            input_tokens=chat.input_tokens,
            output_tokens=chat.output_tokens,
            total_tokens=chat.total_tokens,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        data = _parse_json_response_debug(chat.text, debug_base=last_generation_debug)
        sections_raw = data.get("sections") if isinstance(data.get("sections"), dict) else None
        if first_touch and sections_raw:
            assembled = _assemble_first_touch_body(sections_raw)
            if assembled:
                data["body"] = assembled
        body = str(data.get("body") or "").strip()
        if len(body) < 20:
            last_accum = _ValidationAccum(issues=["body vacío"])
            continue
        if is_fallback:
            from app.services.openai_fallback import apply_fallback_marker_to_body

            data["body"] = apply_fallback_marker_to_body(body)
            last_accum = _ValidationAccum()
            break
        internal = data.get("internal") if isinstance(data.get("internal"), dict) else {}
        subject_raw = str(data.get("subject") or "").strip() if channel == "email" else None
        outcome_context = (
            _first_touch_outcome_context(internal=internal, sections=sections_raw, body=body)
            if first_touch
            else ""
        )
        how_context = (
            _first_touch_how_context(internal=internal, sections=sections_raw, body=body)
            if first_touch
            else ""
        )
        last_accum = _ValidationAccum()
        last_accum.extend(
            _validate_internal(
                internal,
                prospect,
                campaign,
                follow_up=not first_touch,
                step_day=step_day,
                outcome_context=outcome_context,
                how_context=how_context,
            )
        )
        last_accum.extend(
            _validate_body(
                channel,
                body,
                subject=subject_raw or None,
                first_touch=first_touch,
                step_day=step_day,
                sender_name=sender,
                brand_name=brand,
                prospect=prospect,
                campaign=campaign,
                product=product,
                sections=sections_raw,
                internal=internal,
            )
        )
        if first_touch and internal:
            rq = str(internal.get("response_question") or "")
            if rq and not _CONVERSATION_CTA.search(rq):
                last_accum.issues.append("internal.response_question debe ser CTA de conversación")
            if rq:
                last_accum.extend(
                    _collect_pattern_matches(
                        rq, "internal.response_question", _GENERIC_CTA_BANNED, "CTA genérico prohibido"
                    )
                )
        if not last_accum.issues:
            break

    body = str(data.get("body") or "").strip()
    subject_raw = str(data.get("subject") or "").strip() if channel == "email" else None
    sections_raw = data.get("sections") if isinstance(data.get("sections"), dict) else None
    if len(body) < 20:
        raise SdrResponseParseError(
            message="OpenAI devolvió un borrador SDR vacío.",
            debug={
                **(last_generation_debug or {}),
                "parse_error": "body vacío o demasiado corto tras parseo JSON (< 20 caracteres)",
                "stacktrace": None,
            },
            salvage_body=_salvage_body_from_raw(
                str((last_generation_debug or {}).get("raw_response") or ""),
                str((last_generation_debug or {}).get("stripped_response") or ""),
            ),
        )
    internal = data.get("internal") if isinstance(data.get("internal"), dict) else {}
    if last_accum.issues:
        raise SdrDraftValidationError(
            _validation_report(
                channel=channel,
                step_day=step_day,
                body=body,
                subject=subject_raw or None,
                sections=sections_raw,
                internal=internal,
                accum=last_accum,
                attempts=attempts,
                generation_debug=last_generation_debug,
            )
        )
    reasoning = SdrReasoningRead(
        probable_problem=str(internal.get("probable_problem") or "")[:400],
        why_it_matters=str(internal.get("why_it_matters") or "")[:400],
        hypothesis=str(internal.get("hypothesis") or "")[:400],
        response_question=str(internal.get("response_question") or "")[:300],
        selling_to_role=str(
            internal.get("selling_to_role")
            or prospect.get("selling_to_role")
            or prospect.get("role")
            or ""
        )[:200],
    )

    subject: str | None = None
    if channel == "email":
        pname = (prospect.get("name") or "").strip()
        first = pname.split()[0] if pname else "Hola"
        subject = str(data.get("subject") or "").strip() or None
        if subject:
            subject = oai._normalize_gmail_subject_human(
                subject,
                first_name=first,
                full_name=pname,
                company=str(prospect.get("company_name") or ""),
                product_name="",
            )

    return subject, body, reasoning
