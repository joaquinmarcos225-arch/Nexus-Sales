"""Regla 6 — outbound: prohibido preguntar si «sigue interesad@» / «seguís con ganas…».

Es cold/outbound: nosotros ofrecemos reunión. La IA no puede saltearlo.
"""

from __future__ import annotations

import re

from app.services.sequence_close_guard import ensure_meeting_cta

# Preguntas / coletillas que miden interés residual del prospecto (estilo inbound).
_INTEREST_CHECK_QUESTION_RE = re.compile(
    r"(?i)¿\s*(?:"
    r"segu[ií]s?\s+(?:con\s+ganas\b[^?]*|interesad\w*\b[^?]*)|"
    r"sigue(?:s)?\s+(?:con\s+ganas\b[^?]*|interesad\w*\b[^?]*)|"
    r"(?:todav[ií]a|a[uú]n)\s+(?:te\s+)?interesa\w*\b[^?]*|"
    r"te\s+sigue\s+interesando\b[^?]*|"
    r"(?:manten[eé]s|hay)\s+(?:el\s+)?inter[eé]s\b[^?]*|"
    r"sigue(?:s)?\s+con\s+ganas\b[^?]*|"
    r"still\s+interested\b[^?]*|"
    r"tiene\s+sentido\b[^?]*seguir\s+explorando\b[^?]*"
    r")\s*\??"
)

# Misma idea sin signos de apertura (línea = pregunta).
_INTEREST_CHECK_LINE_RE = re.compile(
    r"(?im)^\s*(?:"
    r"segu[ií]s?\s+(?:con\s+ganas\b[^?\n]*|interesad\w*\b[^?\n]*)|"
    r"sigue(?:s)?\s+(?:con\s+ganas\b[^?\n]*|interesad\w*\b[^?\n]*)|"
    r"(?:todav[ií]a|a[uú]n)\s+(?:te\s+)?interesa\w*\b[^?\n]*|"
    r"te\s+sigue\s+interesando\b[^?\n]*|"
    r"still\s+interested\b[^?\n]*|"
    r"tiene\s+sentido\b[^?\n]*seguir\s+explorando\b[^?\n]*"
    r")\s*\??\s*$"
)

# Condicional tipo «Si seguís interesad@, …» / «Si todavía te interesa, …».
_INTEREST_IF_CLAUSE_RE = re.compile(
    r"(?i)\b[Ss]i\s+(?:"
    r"segu[ií]s?\s+interesad\w*|"
    r"sigue(?:s)?\s+interesad\w*|"
    r"(?:todav[ií]a|a[uú]n)\s+te\s+interesa\w*|"
    r"te\s+sigue\s+interesando"
    r")\s*,?\s*"
)

_DETECT_RE = re.compile(
    r"(?i)\b(?:"
    r"segu[ií]s?\s+(?:con\s+ganas\b|interesad\w*)|"
    r"sigue(?:s)?\s+(?:con\s+ganas\b|interesad\w*)|"
    r"(?:todav[ií]a|a[uú]n)\s+(?:te\s+)?interesa\w*|"
    r"te\s+sigue\s+interesando|"
    r"still\s+interested|"
    r"seguir\s+explorando"
    r")\b"
)


def has_interest_check(body: str) -> bool:
    text = body or ""
    if _INTEREST_CHECK_QUESTION_RE.search(text):
        return True
    if _INTEREST_CHECK_LINE_RE.search(text):
        return True
    if _INTEREST_IF_CLAUSE_RE.search(text) and _DETECT_RE.search(text):
        return True
    return False


def strip_interest_checks(body: str) -> str:
    text = (body or "").replace("\r\n", "\n")
    if not text.strip():
        return text.strip()

    out = _INTEREST_CHECK_QUESTION_RE.sub(" ", text)
    lines: list[str] = []
    for ln in out.split("\n"):
        if _INTEREST_CHECK_LINE_RE.match(ln.strip()):
            continue
        cleaned = _INTEREST_IF_CLAUSE_RE.sub("", ln)
        lines.append(cleaned)
    out = "\n".join(lines)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" *\n *", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r" +\.", ".", out)
    out = re.sub(r" +\?", "?", out)
    return out.strip()


def enforce_no_interest_check(
    *,
    body: str,
    prior_touches: list[dict] | None = None,
) -> str:
    """Saca chequeos de interés residual y deja CTA de reunión si hace falta."""
    text = (body or "").strip()
    if not text:
        return text
    if not has_interest_check(text):
        return text
    cleaned = strip_interest_checks(text)
    # Segunda pasada por si quedó residuo.
    if has_interest_check(cleaned):
        cleaned = strip_interest_checks(cleaned)
    return ensure_meeting_cta(cleaned, prior_touches=prior_touches).strip()


def interest_check_rules_for_prompt() -> str:
    return (
        "REGLA DURA OUTBOUND: PROHIBIDO preguntar si «seguís interesad@», "
        "«seguís con ganas de verlo/explorarlo», «todavía te interesa», "
        "«te sigue interesando» o equivalentes. No es inbound: nosotros "
        "ofrecemos reunión/llamada/charla (CTA), no medimos interés residual.\n"
    )
