"""Regla 7 — un dato/ángulo de valor se dice una sola vez con esa persona.

Si el follow-up re-pega un claim de valor ya usado en toques previos, se saca.
No inventa datos nuevos: solo evita repetición. Prompt invita a sumar uno nuevo
si la ficha lo permite.
"""

from __future__ import annotations

import re

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")

_STOP = frozenset(
    """
    a al algo alguna algunas alguno algunos ante antes aquello aqui aquí
    asi así aunque bajo bien cada como cómo con contra cual cuales cualquier
    cuando de del desde donde dónde el ella ellas ellos en entre era es esa
    esas ese eso esos esta está estaba estan están este esto estos fue fueron
    ha haber habia había han hasta hay la las le les lo los me mi mis mucho
    muchos muy nada ni no nos nuestra nuestro o os otra otras otro otros para
    pero poco por porque que qué se ser si sí sin sobre su sus te tener ti
    tiene tienen toda todas todo todos tu tus un una uno unos y ya yo
    hola buen día buenas dias días como estas estás andas va tal
    quedo atento saludos gracias
    """.split()
)

# Señal de que la oración aporta valor de producto (no saludo / puente / CTA puro).
_VALUE_SIGNAL_RE = re.compile(
    r"(?i)\b("
    r"\d{1,3}\s*%|"
    r"automatiz\w*|integr\w*|orquest\w*|centraliz\w*|"
    r"ahorr\w*|reduc\w*|aceler\w*|increment\w*|mejor\w*\s+la\s+tasa|"
    r"prospecci\w*|outreach|secuen\w*|pipeline|"
    r"canal(?:es)?|email|linkedin|whatsapp|"
    r"beneficio\w*|permit\w*|facilit\w*|evit\w*\s+tarea|"
    r"sin\s+tarea\s+manual|tareas?\s+manual|"
    r"entreg\w*|same[\s-]?day|flota|ltima\s+milla|ultima\s+milla|"
    r"repartidor\w*|habilit\w*|"
    r"value|roi|conversi\w*"
    r")\b"
)

_CTA_OR_CLOSE_RE = re.compile(
    r"(?i)\b("
    r"reuni[oó]n|llamada|charla|videollamada|agend\w*|coordin\w*|"
    r"10\s*min|15\s*min|quedo atento|hablamos|lo\s+vemos"
    r")\b"
)

_BRIDGE_RE = re.compile(
    r"(?i)^\s*("
    r"te\s+hab[ií]a\s+escrito|retomo|vuelvo\s+a\s+escribir|"
    r"sumo\s+(?:solo\s+)?esto|sin\s+presi[oó]n|por\s+si\s+el\s+mensaje"
    r")"
)

_SIM_THRESHOLD = 0.62
_MIN_TOKENS = 4


def _normalize_tokens(text: str) -> list[str]:
    raw = re.sub(r"[^\wáéíóúüñ%]+", " ", (text or "").lower(), flags=re.I)
    toks = [t for t in raw.split() if t and t not in _STOP and len(t) > 2]
    return toks


def _token_set(text: str) -> frozenset[str]:
    return frozenset(_normalize_tokens(text))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _split_sentences(body: str) -> list[str]:
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    parts: list[str] = []
    for block in text.split("\n"):
        block = block.strip()
        if not block:
            continue
        chunks = _SENTENCE_SPLIT_RE.split(block)
        for c in chunks:
            s = (c or "").strip()
            if s:
                parts.append(s)
    return parts


def is_value_sentence(sentence: str) -> bool:
    s = (sentence or "").strip()
    if len(s) < 24:
        return False
    if _BRIDGE_RE.search(s) and not _VALUE_SIGNAL_RE.search(s):
        return False
    toks = _normalize_tokens(s)
    if len(toks) < _MIN_TOKENS:
        return False
    # CTA/cierre puro sin señal de valor → no es dato de valor.
    if _CTA_OR_CLOSE_RE.search(s) and not _VALUE_SIGNAL_RE.search(s) and len(toks) < 8:
        return False
    if _VALUE_SIGNAL_RE.search(s):
        return True
    # Oración sustancial de medio/cuerpo (posible valor sin keyword tipada).
    if len(toks) >= 8 and "?" not in s and not s.lower().startswith("hola"):
        return True
    return False


def extract_value_sentences(body: str) -> list[str]:
    return [s for s in _split_sentences(body) if is_value_sentence(s)]


def prior_value_fingerprints(prior_touches: list[dict] | None) -> list[frozenset[str]]:
    fps: list[frozenset[str]] = []
    for touch in prior_touches or []:
        body = str(touch.get("body") or touch.get("message_body") or "")
        for sent in extract_value_sentences(body):
            fps.append(_token_set(sent))
    return [fp for fp in fps if len(fp) >= _MIN_TOKENS]


def sentence_repeats_prior(sentence: str, prior_fps: list[frozenset[str]]) -> bool:
    cur = _token_set(sentence)
    if len(cur) < _MIN_TOKENS:
        return False
    for fp in prior_fps:
        if _jaccard(cur, fp) >= _SIM_THRESHOLD:
            return True
    return False


def strip_repeated_value_sentences(
    body: str,
    *,
    prior_touches: list[dict] | None,
) -> str:
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text
    prior_fps = prior_value_fingerprints(prior_touches)
    if not prior_fps:
        return text

    # Reconstruir por líneas para no aplanar párrafos de más.
    out_lines: list[str] = []
    for ln in text.split("\n"):
        if not ln.strip():
            out_lines.append(ln)
            continue
        kept_bits: list[str] = []
        for sent in _split_sentences(ln):
            if is_value_sentence(sent) and sentence_repeats_prior(sent, prior_fps):
                continue
            kept_bits.append(sent)
        if kept_bits:
            out_lines.append(" ".join(kept_bits))
        # si la línea era solo valor repetido → se omite
    out = "\n".join(out_lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def enforce_value_said_once(
    *,
    body: str,
    prior_touches: list[dict] | None,
    sequence_first_touch: bool = False,
) -> str:
    """En follow-ups: saca claims de valor ya usados con esa persona."""
    text = (body or "").strip()
    if not text or sequence_first_touch or not prior_touches:
        return text
    return strip_repeated_value_sentences(text, prior_touches=prior_touches)


def value_once_rules_for_prompt(prior_touches: list[dict] | None) -> str:
    used = extract_value_sentences(
        "\n".join(
            str(t.get("body") or t.get("message_body") or "")
            for t in (prior_touches or [])
        )
    )
    lines = [
        "REGLA DURA VALOR ÚNICO: cada dato/ángulo de valor se dice UNA sola vez "
        "con esta persona. Si sumás valor en este toque, que sea NUEVO "
        "(no repetir lo ya dicho). PROHIBIDO inventar hechos que no estén en la ficha.",
    ]
    if used:
        lines.append("Ángulos/datos de valor YA USADOS (no repetir):")
        for s in used[:8]:
            short = s if len(s) <= 120 else s[:117] + "…"
            lines.append(f"- {short}")
    else:
        lines.append(
            "Aún no hay ángulos previos listados: en FU temprano, dentro de lo posible "
            "sumá UN dato nuevo de la ficha; en FU tardío, preferí hilo + CTA."
        )
    return "\n".join(lines) + "\n"
