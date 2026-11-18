"""Regla 3 — follow-ups: no re-anclar con «Vi que sos {rol} de {empresa}».

Enforcement determinístico: si el cuerpo de un follow-up trae ancla de rol/empresa
de primer toque, se elimina. La IA / banco no pueden saltearse esta regla.
"""

from __future__ import annotations

import re

# Anclas típicas de cold / primer toque (no deben reaparecer en follow-ups).
_FOLLOWUP_FORBIDDEN_ANCHOR_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?im)^\s*Vi que sos\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?im)^\s*Vi que est[aá]s en\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?im)^\s*Vi tu lugar como\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?im)^\s*Te escribo por tu (?:rol|lugar|trabajo) como\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?im)^\s*Me cruz[eé] con tu perfil\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?im)^\s*Vi \{?Empresa\}? y tu perfil\b[^.!?\n]*[.!]?\s*",
    ),
    # Misma ancla en mitad de párrafo tras presentación.
    re.compile(
        r"(?i)(?<=\.)\s*Vi que sos\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?i)(?<=\.)\s*Vi que est[aá]s en\b[^.!?\n]*[.!]?\s*",
    ),
    re.compile(
        r"(?i)(?<=\.)\s*Vi tu lugar como\b[^.!?\n]*[.!]?\s*",
    ),
)

_FOLLOWUP_ANCHOR_DETECT_RE = re.compile(
    r"(?i)\b("
    r"vi que sos|"
    r"vi que est[aá]s en|"
    r"vi tu lugar como|"
    r"te escribo por tu (?:rol|lugar|trabajo) como|"
    r"me cruz[eé] con tu perfil"
    r")\b"
)


def followup_has_forbidden_role_anchor(body: str) -> bool:
    return bool(_FOLLOWUP_ANCHOR_DETECT_RE.search(body or ""))


def strip_followup_role_anchors(body: str) -> str:
    """Quita anclas de primer toque; deja el resto del mensaje usable."""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text
    out = text
    for pat in _FOLLOWUP_FORBIDDEN_ANCHOR_RES:
        out = pat.sub(" ", out)
    # Compactar espacios / párrafos vacíos.
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" *\n *", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r" +\.", ".", out)
    return out.strip()


def enforce_no_role_anchor_on_followup(*, body: str, sequence_first_touch: bool) -> str:
    """Si no es el 1º toque de la secuencia, prohibido dejar ancla de rol/empresa."""
    if sequence_first_touch:
        return (body or "").strip()
    cleaned = strip_followup_role_anchors(body)
    # Segunda pasada por si quedó residuo.
    if followup_has_forbidden_role_anchor(cleaned):
        cleaned = strip_followup_role_anchors(cleaned)
    return cleaned
