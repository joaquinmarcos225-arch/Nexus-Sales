"""Regla 4 — el mismo CTA de reunión puede repetirse como máximo 2 veces por secuencia.

Enforcement determinístico: si el body actual reusa un CTA que ya apareció
2 veces en priors, se reemplaza por otra formulación no agotada.
"""

from __future__ import annotations

import re
from collections import Counter

_QUESTION_RE = re.compile(r"¿[^¿?]*\?")
_MEETING_WORDS_RE = re.compile(
    r"(?i)\b(reuni[oó]n|llamada|charla|conversaci[oó]n|videollamada|call|meet|coordin\w*|agend\w*|"
    r"\d+\s+minutos|lo\s+vemos|lo\s+charlamos|hablamos|un\s+rato|un\s+espacio|"
    r"te\s+queda\s+bien|10\s*[-–]?\s*15\s*min|espacio\s+corto)\b"
)
_SMALL_TALK_RE = re.compile(
    r"(?i)^¿\s*(c[oó]mo\s+(est[aá]s|and[aá]s|va|te\s+va)|qu[eé]\s+tal)\b[^?]*\?$"
)

# Soft invites sin ¿? que igual son CTA de reunión (rompen el orden si quedan + un CTA duro).
_SOFT_MEETING_CTA_RE = re.compile(
    r"(?i)(?:"
    r"[^.!?\n]*\bsi\s+te\s+(?:sirve|calza)\b[^.!?\n]*(?:avisame|armamos|coordinamos|agendamos)[^.!?\n]*[.!]?"
    r"|"
    r"[^.!?\n]*\bsi\s+(?:en\s+alg[uú]n\s+momento\s+)?quer[eé]s\s+explorar\w*[^.!?\n]*[.!]?"
    r"|"
    r"[^.!?\n]*\b(?:avisame\s+y\s+)?armamos\s+un\s+espacio\s+corto[^.!?\n]*[.!]?"
    r"|"
    r"[^.!?\n]*\bcoordinamos\s+un\s+espacio[^.!?\n]*[.!]?"
    r")"
)

# Relleno que suele ir pegado al soft CTA y no es valor.
_SOFT_CTA_FILLER_RE = re.compile(
    r"(?i)\bno\s+quiero\s+saturar\.?"
)

# Alternativas tipicas de CTA (outbound: ofrecemos reunión).
# Preferimos 2 opciones blandas cuando NO hay Calendar (día/hora reales las pone calendar_cta).
_CTA_ALTERNATIVES: tuple[str, ...] = (
    "¿Te viene mejor esta semana o la próxima para una llamada breve?",
    "¿Preferís esta semana o la que viene para una videollamada corta?",
    "¿Te calza hoy u otro día esta semana para 10 minutos?",
    "¿Coordinamos 10 o 15 minutos esta semana?",
    "¿Hacemos una charla corta esta semana o la que viene?",
    "¿Agendamos 10 minutos para ver si encaja?",
    "¿Te sirve una reunión breve para chequear si aplica?",
    "¿Armamos un espacio corto de 10 minutos?",
)

_MAX_SAME_CTA = 2


def normalize_cta(cta: str) -> str:
    text = (cta or "").strip().lower()
    text = text.replace("¿", "").replace("?", "")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[¡!.,;:]+$", "", text)
    return text.strip()


def is_meeting_cta(question: str) -> bool:
    q = (question or "").strip()
    if not q or _SMALL_TALK_RE.match(q):
        return False
    return bool(_MEETING_WORDS_RE.search(q))


def extract_meeting_ctas(body: str) -> list[str]:
    found: list[str] = []
    for q in _QUESTION_RE.findall(body or ""):
        q = q.strip()
        if is_meeting_cta(q):
            found.append(q)
    return found


def extract_soft_meeting_ctas(body: str) -> list[str]:
    found: list[str] = []
    for m in _SOFT_MEETING_CTA_RE.finditer(body or ""):
        s = m.group(0).strip(" \t")
        if s:
            found.append(s)
    return found


def has_any_meeting_cta(body: str) -> bool:
    return bool(extract_meeting_ctas(body) or extract_soft_meeting_ctas(body))


def strip_soft_meeting_ctas(body: str) -> str:
    """Saca invites blandos (y filler tipo «No quiero saturar»)."""
    text = (body or "").replace("\r\n", "\n")
    if not text.strip():
        return text.strip()
    text = _SOFT_MEETING_CTA_RE.sub(" ", text)
    text = _SOFT_CTA_FILLER_RE.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +\.", ".", text)
    text = re.sub(r"\.\s*\.", ".", text)
    return text.strip()


def prior_cta_counts(prior_touches: list[dict] | None) -> Counter[str]:
    counts: Counter[str] = Counter()
    for touch in prior_touches or []:
        body = str(touch.get("body") or touch.get("message_body") or "")
        for cta in extract_meeting_ctas(body):
            key = normalize_cta(cta)
            if key:
                counts[key] += 1
    return counts


def _pick_replacement(counts: Counter[str]) -> str:
    for alt in _CTA_ALTERNATIVES:
        key = normalize_cta(alt)
        if counts.get(key, 0) < _MAX_SAME_CTA:
            return alt
    # Fallback: siempre devolver algo usable.
    return _CTA_ALTERNATIVES[0]


def enforce_one_meeting_cta_per_message(body: str) -> str:
    """NUNCA dos CTAs de reunión en el mismo mensaje.

    Soft invite («Si te sirve, avisame…») + pregunta dura = dos CTAs.
    Si hay CTA en pregunta, se sacan los soft y queda una sola pregunta.
    """
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    hard = extract_meeting_ctas(text)
    soft = extract_soft_meeting_ctas(text)

    if hard:
        # Preferir el CTA en pregunta; soft sobra.
        text = strip_soft_meeting_ctas(text)
        hard = extract_meeting_ctas(text)
        if len(hard) <= 1:
            return text.strip()
        keep = hard[-1]
        out = text
        for cta in reversed(hard[:-1]):
            idx = out.rfind(cta)
            if idx < 0:
                continue
            before = out[:idx].rstrip()
            after = out[idx + len(cta) :].lstrip()
            out = f"{before}\n\n{after}".strip() if after else before.strip()
        while out.count(keep) > 1:
            idx = out.find(keep)
            out = (out[:idx] + out[idx + len(keep) :]).strip()
            out = re.sub(r"\n{3,}", "\n\n", out)
        if keep not in out:
            out = f"{out.rstrip()}\n\n{keep}".strip()
        return re.sub(r"\n{3,}", "\n\n", out).strip()

    # Solo soft: dejar uno (el último) y sacar filler.
    if len(soft) > 1:
        keep_soft = soft[-1]
        for s in soft[:-1]:
            text = text.replace(s, " ", 1)
        text = _SOFT_CTA_FILLER_RE.sub(" ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if keep_soft not in text:
            text = f"{text}\n\n{keep_soft}".strip()
        return text
    if soft:
        # Limpiar filler aunque haya un solo soft.
        text = _SOFT_CTA_FILLER_RE.sub(" ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text.strip()


def enforce_cta_repeat_limit(
    *,
    body: str,
    prior_touches: list[dict] | None,
) -> str:
    """
    Si el CTA del mensaje actual ya se usó 2 veces en la secuencia, lo cambia.
    No inventa un segundo CTA en el mismo mensaje (sigue siendo uno).
    """
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    # Primero: un solo CTA por mensaje (la IA no puede dejar dos).
    text = enforce_one_meeting_cta_per_message(text)

    counts = prior_cta_counts(prior_touches)
    current = extract_meeting_ctas(text)
    if not current:
        return text

    # Solo el último CTA de reunión del mensaje (el que “cuenta” como cierre).
    cta = current[-1]
    key = normalize_cta(cta)
    used = counts.get(key, 0)
    if used < _MAX_SAME_CTA:
        return text

    replacement = _pick_replacement(counts)
    # Evitar reemplazar por el mismo normalizado.
    if normalize_cta(replacement) == key:
        for alt in _CTA_ALTERNATIVES:
            if normalize_cta(alt) != key and counts.get(normalize_cta(alt), 0) < _MAX_SAME_CTA:
                replacement = alt
                break

    # Sustituir la última aparición del CTA actual.
    idx = text.rfind(cta)
    if idx < 0:
        # fallback case-insensitive
        low = text.lower()
        idx = low.rfind(cta.lower())
        if idx < 0:
            return text
        end = idx + len(cta)
        return (text[:idx] + replacement + text[end:]).strip()
    end = idx + len(cta)
    return (text[:idx] + replacement + text[end:]).strip()


def prior_ctas_for_prompt(prior_touches: list[dict] | None) -> str:
    """Bloque corto para el prompt: CTAs ya usados y conteo."""
    counts = prior_cta_counts(prior_touches)
    if not counts:
        return ""
    lines = [
        "REGLA DURA CTA: la misma formulación de CTA de reunión puede usarse "
        f"como máximo {_MAX_SAME_CTA} veces en toda la secuencia. "
        "Si ya está en 2, OBLIGATORIO otra redacción (mismo pedido: agendar).",
        "CTAs ya usados (normalizados → veces):",
    ]
    for key, n in counts.most_common():
        lines.append(f"- «{key}» × {n}")
    return "\n".join(lines) + "\n"