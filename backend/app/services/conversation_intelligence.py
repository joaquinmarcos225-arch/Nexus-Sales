"""Clasificación inbound: objeciones, interés y señales para prompts (sin envío real)."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Orden ordinal para mezclar interés
_INTEREST_RANK = {"low": 0, "medium": 1, "high": 2}


def fold_accents(s: str) -> str:
    """Minúsculas + sin tildes (mejor matcheo de intención en español)."""
    raw = (s or "").lower().strip()
    if not raw:
        return ""
    nk = unicodedata.normalize("NFD", raw)
    return "".join(c for c in nk if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class InboundSignals:
    objection_type: str | None
    interest_level: str
    prospect_wants_meeting: bool
    """Interés o curiosidad por hablar de agenda / timing (no implica compromiso)."""

    explicit_meeting_commitment: bool
    """True solo si el prospecto acepta explícitamente coordinar llamada/reunión/demo o pide horarios con intención de cerrar."""

    asks_concrete_questions: bool
    is_brushoff: bool
    prospect_timing_hold: bool
    """Pide espacio / volver más adelante / timing incómodo sin rechazo definitivo (no insistir con reunión ya)."""

    defer_resume_at_iso: str | None
    """ISO8601 opcional cuando el prospecto da fecha concreta (p. ej. 'julio', 'en 3 meses')."""


def _keyword_probe(text: str) -> tuple[str | None, bool, bool]:
    """Heurística rápida antes de LLM: (objection_guess, brushoff, wants_meeting)."""
    t = text.lower()
    objection: str | None = None
    if any(
        x in t
        for x in (
            "no tenemos tiempo",
            "sin tiempo",
            "muy ocupados",
            "no da el tiempo",
            "agenda llena",
        )
    ):
        objection = "no_time"
    elif any(
        x in t
        for x in (
            "ya usamos",
            "ya tenemos herramienta",
            "estamos con ",
            "contratamos ",
            "vendor actual",
        )
    ):
        objection = "competitor"
    elif any(
        x in t
        for x in (
            "no nos interesa",
            "no estamos interesados",
            "no me interesa",
            "no me interesa por ahora",
            "no interesa por ahora",
            "pasamos",
            "no gracias",
            "dejen de escrib",
        )
    ):
        objection = "not_interested"
    elif any(
        x in t
        for x in (
            "ahora no",
            "más adelante",
            "mas adelante",
            "otro momento",
            "quizá en ",
            "este no es el momento",
            "no es el momento",
            "volvé en",
            "volve en",
            "volvé dentro",
            "escribime en",
            "hablamos en",
            "en unos meses",
            "trimestre que viene",
            "próximo trimestre",
            "por ahora no",
            "porfa",
            "por favor",
            "hablame",
            "hableme",
            "hablame dentro",
            "hableme dentro",
            "dentro de",
            "en 2 dias",
            "en dos dias",
            "un par de dias",
        )
    ):
        objection = "timing"
    elif any(
        x in t
        for x in (
            "no es prioridad",
            "no es una prioridad",
            "no prioridad",
            "no priorizamos",
            "baja prioridad",
            "no es priorit",
        )
    ):
        objection = "not_priority"
    elif any(
        x in t
        for x in (
            "mandame info",
            "envíame",
            "enviame",
            "pasame material",
            "brochure",
            "one pager",
        )
    ):
        objection = "send_info"

    brushoff = bool(
        objection
        or any(
            x in t
            for x in ("gracias", "no gracias", "estamos bien", "no por ahora")
        )
    )
    wants_meeting = bool(
        re.search(
            r"\b(reuni[oó]n|llamad[ao]|call|agendar|coordina|calendario|demo|zoom)\b",
            t,
            re.I,
        )
    )
    return objection, brushoff, wants_meeting


def _timing_hold_probe(text: str, objection: str | None) -> bool:
    if objection == "timing":
        return True
    t = text.lower()
    needles = (
        "ahora no",
        "este no es el momento",
        "no es el momento",
        "no es buen momento",
        "más adelante",
        "mas adelante",
        "más tarde",
        "volvé en",
        "volve en",
        "volvé dentro",
        "escribime en",
        "escribí en",
        "contactame en",
        "hablamos en",
        "en unos meses",
        "unos meses",
        "trimestre que viene",
        "próximo trimestre",
        "proximo trimestre",
        "año que viene",
        "ano que viene",
        "retomamos en",
        "volvé el",
        "recién el",
        "coordinamos en otro",
        "hablame",
        "hableme",
        "dentro de",
        "par de dias",
        "par de días",
        "porfa",
    )
    return any(n in t for n in needles)


def prospect_text_implies_explicit_meeting(text: str) -> bool:
    """API pública para scoring/heurísticas en otros módulos."""
    return _explicit_meeting_commitment_heuristic(text)


def inbound_wants_immediate_booking(text: str | None) -> bool:
    """
    Prioridad absoluta comercial: el prospecto empuja a cerrar agenda / link / coordinación concreta.
    Anula postergación blanda y fuerza CTA de calendario en redacción.
    """
    base = normalize_inbound_text_for_classification(text or "")
    t = fold_accents(base)
    if not t:
        return False
    if any(
        x in t
        for x in (
            "no nos interesa",
            "no estamos interesados",
            "no me interesa",
            "no gracias",
            "no quiero reunion",
        )
    ):
        return False
    if re.search(r"\bahora no\b|\bno ahora\b|ahora no tengo", t):
        return False
    if _explicit_meeting_commitment_heuristic(base):
        return True
    if "quiero agendar" in t and re.search(r"\b(ahora|ya|ya mismo)\b", t):
        return True
    if re.search(r"\b(agendemos|agendar)\s+ahora\b", t):
        return True
    link_ask = any(
        x in t
        for x in (
            "pasame el link",
            "pasame link",
            "pasame el calendario",
            "link de calendario",
            "link calendario",
            "link para agendar",
            "mandame el link",
        )
    )
    if link_ask:
        return True
    meeting_ctx = any(
        x in t
        for x in (
            "reunion",
            "llamada",
            "meet",
            "zoom",
            "teams",
            "demo",
            "encuentro",
            "videollamada",
            "charla",
            "agenda",
        )
    )
    if not meeting_ctx:
        return False
    if any(
        x in t
        for x in (
            "agendemos",
            "coordinemos",
            "coordinamos",
            "coordinar",
            "podemos coordinar",
            "podes coordinar",
            "puedes coordinar",
            "reservemos",
            "reservamos",
            "hagamosla",
            "cerramos agenda",
        )
    ):
        return True
    if re.search(r"\b(ahora|ya mismo)\b", t):
        return True
    if "cuando" in t and re.search(
        r"\b(pod(es)?|podes|queda|vais|va|conviene|te viene)\b",
        t,
    ):
        return True
    if any(x in t for x in ("me sirve", "dale", "perfecto", "listo", "de una")) and (
        "reunion" in t or "llamada" in t or "meet" in t
    ):
        return True
    return False


def _apply_booking_priority_to_signals(text: str, sig: InboundSignals) -> InboundSignals:
    """Si hay booking inmediato y no rechazo duro, prioriza cierre de agenda sobre timing/postergación."""
    if not inbound_wants_immediate_booking(text):
        return sig
    if sig.objection_type == "not_interested":
        return sig
    cleared_obj = sig.objection_type if sig.objection_type not in ("timing",) else None
    return InboundSignals(
        objection_type=cleared_obj,
        interest_level="high",
        prospect_wants_meeting=True,
        explicit_meeting_commitment=True,
        asks_concrete_questions=sig.asks_concrete_questions,
        is_brushoff=False,
        prospect_timing_hold=False,
        defer_resume_at_iso=None,
    )


def _explicit_meeting_commitment_heuristic(text: str) -> bool:
    """
    Compromiso claro de coordinar (no basta 'me interesa', 'contame más', ni pedido de brochure).
    """
    raw = (text or "").strip()
    if not raw:
        return False
    t = fold_accents(raw)
    neg = (
        "sin reunion",
        "no quiero reunion",
        "no reunion",
        "no gracias",
        "no nos interesa",
    )
    if any(x in t for x in neg):
        return False
    # Pedir info/material no es compromiso de agenda
    if any(
        x in t
        for x in (
            "mandame info",
            "enviame info",
            "pasame material",
            "brochure",
            "one pager",
            "pdf",
            "precio",
            "cuanto cuesta",
        )
    ):
        # salvo que también coordinen explícitamente
        if not re.search(
            r"\b(agendemos\w*|coordinemos|coordinamos|hablemos|llamada|reunion\s+de|charlamos|"
            r"pasame\s+(el\s+)?(link|calendario|horario)|cuando\s+(te\s+)?(va|queda))\b",
            t,
            re.I,
        ):
            return False
    return bool(
        re.search(
            r"\b("
            r"agenda(?:me|mos|moslo|rlo|r)?|agendemos\w*|coordinemos|coordinamos|coordinar|"
            r"hablemos|charlamos|juntemonos|veamonos|"
            r"llamada|videollamada|meet\.|zoom|teams|"
            r"pasame\s+(el\s+)?(link|calendario|horarios?)|"
            r"que\s+horario|cuando\s+(te\s+)?(va|podes|puedes|queda)|"
            r"reservemos|reservamos|"
            r"dale[, ]*\s*(coordinemos|agendemos|hablamos)|"
            r"si[, ]*\s*(coordinemos|agendemos|hablamos|charlamos)"
            r")\b",
            t,
            re.I,
        )
    )


def merge_interest_level(
    current: str | None,
    incoming: str | None,
    objection: str | None,
) -> str:
    """Combina niveles; objeción fuerte ancla a bajo/medio."""
    cur = (current or "low").strip().lower()
    inc = (incoming or "low").strip().lower()
    if cur not in _INTEREST_RANK:
        cur = "low"
    if inc not in _INTEREST_RANK:
        inc = "low"
    if objection in ("not_interested", "timing", "no_time", "competitor"):
        # Aún puede recuperarse, pero no saltamos a high de golpe
        base = min(_INTEREST_RANK[cur], _INTEREST_RANK[inc], 1)
        return "low" if base == 0 else "medium"
    return inc if _INTEREST_RANK[inc] >= _INTEREST_RANK[cur] else cur


def pick_interest_from_keywords(text: str, prior: str | None) -> str:
    t = text.lower()
    score = _INTEREST_RANK.get((prior or "low").lower(), 0)
    if any(
        x in t
        for x in ("cuánto cuesta", "precio", "demo", "implementación", "integración con")
    ):
        score = max(score, 2)
    elif any(x in t for x in ("contame", "me interesa", "interesante", "dale", "perfecto")):
        score = max(score, 2)
    elif len(t) > 120 and "?" in t:
        score = max(score, 1)
    elif len(t) < 25 and t.count(" ") < 5:
        score = min(score, 1)
    return "high" if score >= 2 else "medium" if score == 1 else "low"


def build_signals_from_keywords(text: str, prior_interest: str | None) -> InboundSignals:
    objection, brushoff, wants_meeting = _keyword_probe(text)
    timing_hold = _timing_hold_probe(text, objection)
    if timing_hold and objection is None:
        objection = "timing"
    iq = pick_interest_from_keywords(text, prior_interest)
    if objection == "send_info":
        iq = merge_interest_level(iq, "medium", objection)
    asks_q = "?" in text and len(text) > 15
    explicit = _explicit_meeting_commitment_heuristic(text)
    return InboundSignals(
        objection_type=objection,
        interest_level=iq,
        prospect_wants_meeting=wants_meeting,
        explicit_meeting_commitment=explicit,
        asks_concrete_questions=asks_q,
        is_brushoff=brushoff and objection is None,
        prospect_timing_hold=timing_hold,
        defer_resume_at_iso=None,
    )


def normalize_inbound_text_for_classification(raw: str | None) -> str:
    """
    Quita envoltorios Nexus (p. ej. prefijo Gmail) para keywords + LLM.
    Si el clasificador veía solo headers, a veces devolvía objection none y explicit_meeting_commitment true,
    bloqueando la postergación automática.
    """
    t = (raw or "").strip()
    if not t:
        return ""
    low = t.lower()
    if low.startswith("[gmail · respuesta real]") or low.startswith("[gmail ·"):
        parts = t.split("\n\n", 1)
        if len(parts) >= 2:
            return parts[1].strip()
    return t


def _inbound_anchor_is_soft_postpone(norm: str) -> bool:
    """Frases típicas de 'retomemos después' (anulan falsos positivos de compromiso de agenda del LLM)."""
    t = (norm or "").lower()
    return any(
        x in t
        for x in (
            "habl",
            "hable",
            "escrib",
            "volvé",
            "volve",
            "contactame",
            "contáctame",
            "retom",
            "más adelante",
            "mas adelante",
            "por ahora",
            "luego",
            "después",
            "despues",
            "cuando puedas",
            "cuándo puedas",
            "avisame",
            "avísame",
        )
    )


def timing_deferral_should_apply(sig: InboundSignals, inbound_text: str | None = None) -> bool:
    """
    Postergación operativa: señales del clasificador + ancla en el texto (p. ej. 'hablame en 3 días').
    Si `inbound_text` se omite, solo se usan señales (comportamiento previo).
    """
    if inbound_text is not None:
        norm = normalize_inbound_text_for_classification(inbound_text)
        if inbound_wants_immediate_booking(norm):
            return False
        rel = parse_spanish_relative_days(norm)
        if rel is not None:
            if _inbound_anchor_is_soft_postpone(norm):
                return True
            if not sig.explicit_meeting_commitment:
                return True
    if sig.explicit_meeting_commitment:
        return False
    return bool(sig.prospect_timing_hold or sig.objection_type == "timing")


def parse_classifier_json(raw: str) -> dict:
    txt = raw.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*", "", txt)
        txt = re.sub(r"```\s*$", "", txt).strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        start = txt.find("{")
        end = txt.rfind("}")
        if start >= 0 and end > start:
            return json.loads(txt[start : end + 1])
        raise


def classify_inbound_full(
    *,
    inbound_text: str,
    prior_interest: str | None,
    conversation_digest: str,
    education: str,
) -> InboundSignals:
    """Keywords + clasificación compacta por modelo (fallback silencioso a keywords)."""
    from app.services import openai_service

    text = normalize_inbound_text_for_classification(inbound_text)
    kw = build_signals_from_keywords(text, prior_interest)
    try:
        raw = openai_service.classify_inbound_json_raw(
            inbound_text=text,
            conversation_digest=conversation_digest,
            education=education,
        )
        llm = parse_classifier_json(raw)
        merged = merge_llm_signals(kw, llm, prior_interest)
    except Exception:
        merged = kw
    return _apply_booking_priority_to_signals(text, merged)


def should_allow_meeting_nudge(
    signals: InboundSignals,
    *,
    inbound_turn_index: int,
) -> bool:
    """Sugerir link/cierre suave hacia reunión (no crea fila Meeting)."""
    if signals.objection_type == "not_interested":
        return False
    if signals.explicit_meeting_commitment:
        return True
    if signals.prospect_timing_hold:
        return False
    if signals.objection_type == "timing":
        return False
    if signals.interest_level == "high" and inbound_turn_index >= 1:
        return True
    if signals.prospect_wants_meeting and signals.interest_level in ("medium", "high"):
        return True
    if signals.interest_level == "medium" and inbound_turn_index >= 3:
        return True
    return False


def prospect_status_from_inbound_signals(current_status: str, sig: InboundSignals) -> str:
    """
    Regla: meeting_booked SOLO con explicit_meeting_commitment (o heurística equivalente en sig).
    Interés alto sin compromiso -> interested. Mensaje entrante típico -> replied.
    """
    from app.models.enums import ProspectStatus

    if current_status == ProspectStatus.failed.value:
        return current_status
    if current_status == ProspectStatus.meeting_booked.value:
        if sig.objection_type == "not_interested":
            return ProspectStatus.not_interested.value
        return ProspectStatus.meeting_booked.value
    if sig.objection_type == "not_interested":
        return ProspectStatus.not_interested.value
    if sig.explicit_meeting_commitment:
        return ProspectStatus.interested.value
    if sig.prospect_timing_hold or sig.objection_type == "timing":
        return ProspectStatus.replied.value
    if (sig.interest_level or "").lower() == "high":
        return ProspectStatus.interested.value
    if current_status in (ProspectStatus.imported.value, ProspectStatus.compatible.value):
        return ProspectStatus.contacted.value
    if current_status == ProspectStatus.contacted.value:
        return ProspectStatus.replied.value
    return current_status


def merge_llm_signals(
    keyword_sig: InboundSignals,
    llm: dict,
    prior_interest: str | None,
) -> InboundSignals:
    obj = llm.get("objection")
    objection = keyword_sig.objection_type
    if isinstance(obj, str) and obj and obj != "none" and obj != "null":
        objection = obj
    # No dejar que el LLM borre una objeción timing detectada por heurística.
    if (not objection or str(objection).lower() in ("none", "null")) and keyword_sig.objection_type == "timing":
        objection = "timing"

    incoming_i = llm.get("interest")
    if isinstance(incoming_i, str):
        incoming_i = incoming_i.lower().strip()
    else:
        incoming_i = keyword_sig.interest_level
    iq = merge_interest_level(prior_interest or "low", incoming_i, objection)
    if keyword_sig.interest_level == "high":
        iq = merge_interest_level(iq, "high", objection)

    soft_kw = bool(keyword_sig.prospect_timing_hold or keyword_sig.objection_type == "timing")
    lm_commit = llm.get("explicit_meeting_commitment")
    # Si hay señales fuertes de postergación por keywords, no aceptar "compromiso de reunión" solo del LLM.
    if lm_commit is True and not soft_kw:
        explicit = True
    elif lm_commit is True and soft_kw:
        explicit = bool(keyword_sig.explicit_meeting_commitment)
    elif lm_commit is False:
        explicit = bool(keyword_sig.explicit_meeting_commitment)
    else:
        explicit = bool(keyword_sig.explicit_meeting_commitment)

    th = bool(keyword_sig.prospect_timing_hold or llm.get("prospect_timing_hold") is True)
    defer_iso = llm.get("defer_resume_at")
    if not isinstance(defer_iso, str) or not str(defer_iso).strip():
        defer_iso = None
    else:
        defer_iso = str(defer_iso).strip()

    return InboundSignals(
        objection_type=objection if objection and objection != "none" else None,
        interest_level=iq,
        prospect_wants_meeting=bool(
            keyword_sig.prospect_wants_meeting or llm.get("wants_meeting")
        ),
        explicit_meeting_commitment=explicit,
        asks_concrete_questions=bool(
            keyword_sig.asks_concrete_questions or llm.get("asks_questions")
        ),
        is_brushoff=bool(keyword_sig.is_brushoff or llm.get("brushoff")),
        prospect_timing_hold=th,
        defer_resume_at_iso=defer_iso,
    )


def parse_defer_resume_hint_iso(iso: str | None) -> datetime | None:
    """Interpreta `defer_resume_at` del clasificador (UTC si viene naive)."""
    if not iso or not isinstance(iso, str):
        return None
    t = iso.strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _defer_due_at_day_offset(now: datetime, days: int) -> datetime:
    """Fecha de re-contacto a las 14:00 UTC, N días después de `now` (mínimo 1)."""
    d = max(1, min(int(days), 365))
    when = (now.astimezone(UTC) + timedelta(days=d)).date()
    return datetime(when.year, when.month, when.day, 14, 0, 0, tzinfo=UTC)


def parse_spanish_relative_days(text: str | None) -> int | None:
    """
    Extrae N días desde frases relativas en español (p. ej. 'dentro de 2 días', 'en 3 dias').
    None si no hay número claro.
    """
    if not text or not isinstance(text, str):
        return None
    t = text.lower()
    if any(
        x in t
        for x in (
            "próxima semana",
            "proxima semana",
            "semana que viene",
            "la semana que viene",
        )
    ):
        return 7
    if "mes que viene" in t or "próximo mes" in t or "proximo mes" in t:
        return 30
    if "un par de" in t and ("día" in t or "dia" in t):
        return 2
    if re.search(r"\b(dos|2)\s*d[ií]as?\b", t):
        return 2
    if "48 horas" in t or "48 hs" in t:
        return 2
    if "una semana" in t:
        return 7
    m = re.search(r"(?:dentro de|en)\s*(\d{1,3})\s*d[ií]as?", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 120:
            return n
    m2 = re.search(r"\b(\d{1,3})\s*d[ií]as?\b", t)
    if m2:
        n = int(m2.group(1))
        if 1 <= n <= 120 and any(
            k in t
            for k in (
                "habl",
                "hable",
                "escrib",
                "contact",
                "volv",
                "por ahora",
                "dentro",
                "despues",
                "después",
            )
        ):
            return n
    return None


def infer_defer_resume_utc(
    *,
    inbound_text: str | None,
    defer_iso: str | None,
    now: datetime | None = None,
) -> datetime:
    """
    Combina ISO del clasificador (si válido y futuro) con heurística de días en español.
    Si no hay señal concreta, default 7 días (postergación blanda sin fecha).
    """
    n = now or datetime.now(UTC)
    if n.tzinfo is None:
        n = n.replace(tzinfo=UTC)
    n = n.astimezone(UTC)
    parsed = parse_defer_resume_hint_iso(defer_iso)
    if parsed is not None:
        floor = n + timedelta(hours=6)
        cap = n + timedelta(days=365)
        if parsed < floor:
            return _defer_due_at_day_offset(n, 1)
        return min(parsed, cap)
    rel = parse_spanish_relative_days(inbound_text)
    if rel is not None:
        return _defer_due_at_day_offset(n, rel)
    return _defer_due_at_day_offset(n, 7)


COMMERCIAL_RESPONSE_LABELS: dict[str, str] = {
    "interesado": "Interesado",
    "no_interesado": "No interesado",
    "pedir_mas_info": "Pide más info",
    "derivar_a_otra_persona": "Derivar a otra persona",
    "contactar_mas_adelante": "Contactar más adelante",
    "respuesta_automatica": "Respuesta automática",
    "desconocido": "Desconocido",
}

REPLY_OBJECTIVE_LABELS: dict[str, str] = {
    "agendar": "Agendar reunión / llamada",
    "informar": "Responder con información",
    "seguimiento": "Mantener conversación",
    "timing": "Respetar timing",
    "rechazo": "Cerrar conversación",
    "referir": "Derivar contacto",
}

CLOSURE_KIND_REJECTION = "rejection"
CLOSURE_KIND_NOT_PRIORITY = "not_priority"

_SOFT_TIMING_MARKERS = (
    "por ahora",
    "ahora no",
    "mas adelante",
    "más adelante",
    "no es prioridad",
    "no es una prioridad",
    "no prioridad",
    "otro momento",
    "no es el momento",
)


def _prospect_first_name(name: str | None) -> str:
    raw = (name or "").strip()
    if not raw:
        return "ahí"
    return raw.split()[0]


def resolve_closure_kind(
    *,
    text: str,
    response_class: str | None = None,
    reply_objective: str | None = None,
) -> str | None:
    """
    Si el prospecto rechazó o postergó, devuelve el tipo de cierre profesional.
    None = seguir conversación consultiva normal.
    """
    obj = (reply_objective or "").strip().lower()
    rc = (response_class or "").strip().lower()
    if obj not in ("rechazo", "timing") and rc not in ("no_interesado", "contactar_mas_adelante"):
        return None

    norm = fold_accents(normalize_inbound_text_for_classification(text or ""))
    soft_timing = any(m in norm for m in _SOFT_TIMING_MARKERS)

    if obj == "timing" or rc == "contactar_mas_adelante":
        return CLOSURE_KIND_NOT_PRIORITY
    if rc == "no_interesado" and soft_timing:
        return CLOSURE_KIND_NOT_PRIORITY
    if obj == "rechazo" or rc == "no_interesado":
        return CLOSURE_KIND_REJECTION
    return CLOSURE_KIND_NOT_PRIORITY


def build_professional_closure_reply(
    *,
    prospect_name: str | None,
    closure_kind: str,
) -> str:
    """Cierre SDR experimentado — sin pitch, sin re-explicar producto."""
    first = _prospect_first_name(prospect_name)
    if closure_kind == CLOSURE_KIND_REJECTION:
        return (
            f"Perfecto {first}, gracias por responder.\n\n"
            f"Lo dejamos acá para no molestarte. Si más adelante cambia la prioridad, "
            f"con gusto retomamos la conversación.\n\n"
            f"Éxitos."
        )
    return (
        f"Gracias por comentarlo, {first}.\n\n"
        f"Entiendo que no sea el momento adecuado. Quedo a disposición si en el futuro "
        f"tiene sentido retomarlo."
    )


def inbound_has_explicit_meeting_slot(text: str | None) -> bool:
    """True si el mensaje incluye fecha/hora concreta para reunión."""
    from app.services.meeting_slot_parser import prospect_proposed_meeting_slot

    return prospect_proposed_meeting_slot(text)


def meeting_acceptance_detected(text: str | None) -> bool:
    """El prospecto confirma un horario o acepta coordinar (no solo pregunta)."""
    norm = fold_accents(normalize_inbound_text_for_classification(text or ""))
    if not norm or len(norm) < 3:
        return False
    if inbound_has_explicit_meeting_slot(text):
        if re.search(r"\bagenda(?:me|mos|moslo|rlo|r)?\b", norm):
            return True
        if "me queda" in norm or "te queda" in norm:
            return True
    if not any(
        x in norm
        for x in (
            "si ",
            "sí ",
            "dale",
            "perfecto",
            "confirmado",
            "me queda",
            "te queda",
            "de acuerdo",
            "ok ",
            "oka",
            "listo",
            "agendado",
            "agendame",
            "agenda me",
            "nos vemos",
            "ahi nos",
            "ahí nos",
        )
    ):
        return False
    time_hint = (
        "lunes",
        "martes",
        "miercoles",
        "miércoles",
        "jueves",
        "viernes",
        "manana",
        "mañana",
        "semana",
        "10",
        "11",
        "15",
        "16",
        "17",
        "hs",
        "am",
        "pm",
        "horario",
    )
    return any(h in norm for h in time_hint) or _explicit_meeting_commitment_heuristic(text or "")


def should_book_meeting_from_inbound(
    *,
    text: str,
    sig: InboundSignals,
    reply_objective: str | None,
) -> bool:
    """Reservar reunión solo con aceptación explícita o horario concreto en el mensaje."""
    from app.services.meeting_slot_parser import prospect_proposed_meeting_slot

    if prospect_proposed_meeting_slot(text):
        return True
    obj = (reply_objective or "").strip().lower()
    if meeting_acceptance_detected(text):
        return True
    if sig.explicit_meeting_commitment and obj == "agendar":
        norm = fold_accents(normalize_inbound_text_for_classification(text))
        if any(
            x in norm
            for x in (
                "confirm",
                "agend",
                "coordin",
                "nos vemos",
                "te espero",
                "me queda bien",
            )
        ):
            return True
    return False


def inbound_requests_meeting_or_demo(text: str | None) -> bool:
    """El prospecto pide llamada, reunión, demo o coordinar horario (no solo curiosidad genérica)."""
    norm = fold_accents(normalize_inbound_text_for_classification(text or ""))
    if not norm:
        return False
    if inbound_has_explicit_meeting_slot(text):
        return True
    if _explicit_meeting_commitment_heuristic(text or ""):
        return True
    if inbound_wants_immediate_booking(text):
        return True
    if re.search(r"\bagenda(?:me|mos|moslo|rlo|r)?\b", norm):
        return True
    meeting_words = (
        "llamada",
        "reunion",
        "videollamada",
        "demo",
        "meet",
        "zoom",
        "teams",
        "charla",
        "coordinar",
        "agendar",
        "agendemos",
        "coordinemos",
    )
    if not any(w in norm for w in meeting_words):
        return False
    intent = (
        "podemos",
        "podes",
        "puedes",
        "te parece",
        "te va",
        "te queda",
        "me interesa",
        "quiero",
        "gustaria",
        "proxima semana",
        "semana que viene",
        "cuando",
    )
    return any(i in norm for i in intent)


def product_explanation_deferred_to_meeting(text: str | None) -> bool:
    """
    Pregunta exploratoria ('cómo funciona') junto con pedido de reunión —
    el SDR experimentado responde en la llamada, no repite el pitch por email.
    """
    norm = fold_accents(normalize_inbound_text_for_classification(text or ""))
    if not inbound_requests_meeting_or_demo(text):
        return False
    soft_explore = (
        "como funciona",
        "entender mejor",
        "conocer mas",
        "conocer mejor",
        "ver como",
        "mostrar",
        "explicar",
        "contame mas",
        "mas info",
        "mas informacion",
    )
    return any(s in norm for s in soft_explore)


def resolve_reply_objective(
    *,
    text: str,
    sig: InboundSignals,
    response_class: str | None = None,
) -> str:
    """
    Objetivo comercial del próximo mensaje SDR (etapa de conversación).
    Prospección → interés → agendar → demo → propuesta → cierre.
    """
    if response_class == "no_interesado" or sig.objection_type == "not_interested":
        return "rechazo"
    if response_class == "derivar_a_otra_persona":
        return "referir"
    if (
        response_class == "contactar_mas_adelante"
        or sig.prospect_timing_hold
        or sig.objection_type in ("timing", "no_time", "not_priority")
    ) and not inbound_requests_meeting_or_demo(text):
        return "timing"

    if inbound_has_explicit_meeting_slot(text):
        return "agendar"
    if inbound_requests_meeting_or_demo(text):
        return "agendar"
    if sig.explicit_meeting_commitment or (
        sig.prospect_wants_meeting and sig.interest_level in ("high", "medium")
    ):
        return "agendar"

    if response_class == "pedir_mas_info" or sig.objection_type == "send_info":
        return "informar"
    if sig.asks_concrete_questions and sig.interest_level in ("high", "medium"):
        return "informar"
    return "seguimiento"


def _auto_reply_probe(text: str) -> bool:
    t = fold_accents(text)
    return bool(
        re.search(
            r"(fuera de (?:la )?oficina|out of office|respuesta automatica|auto[\s-]?reply|"
            r"no (?:responda|reply)|mensaje automatico|estoy de vacaciones|"
            r"no estoy disponible|away from (?:the )?office|autoresponder)",
            t,
            re.I,
        )
    )


def _referral_probe(text: str) -> bool:
    t = fold_accents(text)
    return bool(
        re.search(
            r"(no soy (?:la )?persona|no es mi area|habla con|hablá con|contacta(?:r)? a|"
            r"contactá a|escribile a|el responsable es|deriv|redirig|otra persona|"
            r"no manejo|no me corresponde|te paso con|habla con mi)",
            t,
            re.I,
        )
    )


def classify_commercial_response(text: str, sig: InboundSignals) -> tuple[str, str]:
    """
    Clasificación comercial para secuencias SDR (modo manual/testing).
    Devuelve (response_class, label).
    """
    norm = fold_accents(normalize_inbound_text_for_classification(text))

    if _auto_reply_probe(norm):
        cls = "respuesta_automatica"
    elif _referral_probe(norm):
        cls = "derivar_a_otra_persona"
    elif any(
        x in norm
        for x in (
            "no me interesa",
            "no me interesa por ahora",
            "no interesa por ahora",
            "no estoy interesad",
            "no nos interesa",
            "no gracias",
            "dejen de escrib",
            "no es para nosotros",
            "no encaja",
        )
    ):
        cls = "no_interesado"
    elif sig.objection_type == "not_interested" or (
        sig.is_brushoff and sig.interest_level == "low"
    ):
        cls = "no_interesado"
    elif sig.objection_type == "not_priority" or any(
        x in norm
        for x in (
            "no es prioridad",
            "no es una prioridad",
            "no prioridad",
            "mas adelante",
            "más adelante",
            "ahora no",
        )
    ):
        cls = "contactar_mas_adelante"
    elif sig.objection_type in ("timing", "no_time") or sig.prospect_timing_hold:
        if inbound_requests_meeting_or_demo(text) and sig.interest_level in ("high", "medium"):
            cls = "interesado"
        else:
            cls = "contactar_mas_adelante"
    elif inbound_requests_meeting_or_demo(text) and sig.interest_level in ("high", "medium"):
        cls = "interesado"
    elif sig.objection_type == "send_info" or (
        sig.asks_concrete_questions
        and any(
            x in norm
            for x in (
                "info",
                "informacion",
                "material",
                "brochure",
                "pdf",
                "precio",
                "costo",
                "como funciona",
                "contame mas",
                "mandame",
                "enviame",
                "pasame",
            )
        )
    ):
        cls = "pedir_mas_info"
    elif (
        sig.explicit_meeting_commitment
        or sig.interest_level == "high"
        or (sig.interest_level == "medium" and sig.prospect_wants_meeting)
        or inbound_requests_meeting_or_demo(text)
        or any(
            x in norm
            for x in (
                "me interesa",
                "interesante",
                "dale",
                "coordinemos",
                "agendemos",
                "conocer mas",
                "conocer más",
            )
        )
    ):
        cls = "interesado"
    elif sig.interest_level == "medium" or sig.asks_concrete_questions:
        cls = "pedir_mas_info"
    else:
        cls = "desconocido"

    return cls, COMMERCIAL_RESPONSE_LABELS.get(cls, "Desconocido")
