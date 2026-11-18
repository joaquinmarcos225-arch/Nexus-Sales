"""Regla 5 — cierre («Quedo atento») máx. 2× por secuencia + CTA de reunión casi siempre.

Enforcement determinístico (no salteable por la IA).
"""

from __future__ import annotations

import re

from app.services.sequence_cta_guard import (
    _CTA_ALTERNATIVES,
    extract_meeting_ctas,
    prior_cta_counts,
    normalize_cta,
)

_MAX_SAME_CLOSE = 2

# Familia de cierre tipo «Quedo atento» (misma idea / repetición molesta).
_CLOSE_FAMILY_RE = re.compile(
    r"(?im)^\s*("
    r"quedo atento(?:\s+a\s+lo\s+que\s+te\s+quede\s+mejor)?|"
    r"quedo\s+a\s+las\s+\w+|"
    r"cualquier\s+cosa\s+me\s+avis[aá]s|"
    r"me\s+avis[aá]s\s+cuando\s+puedas"
    r")\.?\s*$"
)

_CLOSE_INLINE_RE = re.compile(
    r"(?i)\bquedo atento(?:\s+a\s+lo\s+que\s+te\s+quede\s+mejor)?\.?"
)

_ALT_CLOSES: tuple[str, ...] = (
    "Saludos.",
    "Gracias.",
    "",  # sin cierre formal si ya hay CTA
)


def _normalize_close_line(line: str) -> str:
    t = (line or "").strip().lower()
    t = re.sub(r"[.!\s]+$", "", t)
    t = re.sub(r"\s+", " ", t)
    if t.startswith("quedo atento"):
        return "quedo atento"
    return t


def extract_close_family_hits(body: str) -> list[str]:
    hits: list[str] = []
    for ln in (body or "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if _CLOSE_FAMILY_RE.match(s) or (
            len(s.split()) <= 8 and _CLOSE_INLINE_RE.search(s) and "?" not in s
        ):
            hits.append(s)
    return hits


def prior_close_count(prior_touches: list[dict] | None) -> int:
    n = 0
    for touch in prior_touches or []:
        body = str(touch.get("body") or touch.get("message_body") or "")
        n += len(extract_close_family_hits(body))
    return n


def strip_close_family(body: str) -> str:
    lines = (body or "").replace("\r\n", "\n").split("\n")
    kept: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            kept.append(ln)
            continue
        if _CLOSE_FAMILY_RE.match(s):
            continue
        if len(s.split()) <= 8 and _CLOSE_INLINE_RE.search(s) and "?" not in s:
            # Quitar solo el cierre si está al final de la línea.
            cleaned = _CLOSE_INLINE_RE.sub("", s).strip(" .")
            if cleaned:
                kept.append(cleaned)
            continue
        kept.append(ln)
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ensure_meeting_cta(body: str, *, prior_touches: list[dict] | None = None) -> str:
    """Si no hay CTA en pregunta, agrega uno (y saca soft invites duplicados)."""
    from app.services.sequence_cta_guard import (
        extract_soft_meeting_ctas,
        strip_soft_meeting_ctas,
    )

    text = (body or "").strip()
    if extract_meeting_ctas(text):
        # Soft + hard no deben convivir.
        return strip_soft_meeting_ctas(text) if extract_soft_meeting_ctas(text) else text

    # Soft invite solo: upgradear a CTA en pregunta (un solo CTA claro).
    if extract_soft_meeting_ctas(text):
        text = strip_soft_meeting_ctas(text)

    counts = prior_cta_counts(prior_touches)
    cta = _CTA_ALTERNATIVES[0]
    for alt in _CTA_ALTERNATIVES:
        if counts.get(normalize_cta(alt), 0) < 2:
            cta = alt
            break
    if text:
        return f"{text.rstrip()}\n\n{cta}"
    return cta


def enforce_close_and_cta(
    *,
    body: str,
    prior_touches: list[dict] | None,
) -> str:
    """
    1) Familia «Quedo atento» ≤ 2 en toda la secuencia (si ya van 2, se saca del actual).
    2) Casi siempre debe haber CTA de reunión: si falta, se agrega.
    """
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    prior_n = prior_close_count(prior_touches)
    current_hits = extract_close_family_hits(text)
    if prior_n >= _MAX_SAME_CLOSE and current_hits:
        text = strip_close_family(text)
    elif prior_n + len(current_hits) > _MAX_SAME_CLOSE and current_hits:
        # Dejar solo el cupo restante (suele ser sacar el cierre actual).
        text = strip_close_family(text)

    text = ensure_meeting_cta(text, prior_touches=prior_touches)
    # Nunca dejar 2 CTAs de reunión en el mismo mensaje.
    from app.services.sequence_cta_guard import enforce_one_meeting_cta_per_message

    text = enforce_one_meeting_cta_per_message(text)
    return text.strip()


def close_rules_for_prompt(prior_touches: list[dict] | None) -> str:
    n = prior_close_count(prior_touches)
    lines = [
        "REGLA DURA CIERRE: «Quedo atento» (y equivalentes de esa familia) "
        f"como máximo {_MAX_SAME_CLOSE} veces en toda la secuencia. "
        f"Ya usados: {n}.",
        "REGLA DURA CTA: casi todos los mensajes deben ofrecer reunión/llamada/charla "
        "(nosotros ofrecemos). Un solo CTA por mensaje.",
    ]
    if n >= _MAX_SAME_CLOSE:
        lines.append(
            "PROHIBIDO volver a cerrar con «Quedo atento». Preferí CTA claro "
            "y cierre distinto o sin coletilla."
        )
    return "\n".join(lines) + "\n"
