"""Orden fijo de mensaje outbound (todos los canales).

1) Inicio + apalancamiento
2) Información / valor (puede faltar en últimos toques)
3) CTA (uno)
4) Cierre

La redacción puede variar por canal; el orden de bloques no.
"""

from __future__ import annotations

import re

from app.services.sequence_close_guard import (
    extract_close_family_hits,
    strip_close_family,
)
from app.services.sequence_cta_guard import (
    enforce_one_meeting_cta_per_message,
    extract_meeting_ctas,
    extract_soft_meeting_ctas,
    is_meeting_cta,
    strip_soft_meeting_ctas,
)
from app.services.sequence_followup_leverage_guard import has_prior_leverage

_GREETING_RE = re.compile(
    r"(?is)^\s*("
    r"hola\b[^\n]*|"
    r"buen(?:as|os)\s+(?:d[ií]as|tardes|noches)\b[^\n]*|"
    r"qu[eé]\s+tal\b[^\n]*"
    r")\s*$"
)

_SIGNATURE_RE = re.compile(
    r"(?is)^\s*("
    r"saludos(?:\s*,?\s+\w+)?|"
    r"gracias(?:\s*,?\s+\w+)?|"
    r"abrazo|"
    r"cordialmente"
    r")\.?\s*$"
)

_PRESENTATION_RE = re.compile(
    r"(?is)^\s*soy\s+\w+(?:\s+de\s+\w+)?\.?\s*$"
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_units(text: str) -> list[str]:
    """Párrafos; si mezcla categorías, parte por líneas o por oraciones."""
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    units: list[str] = []
    for para in paras:
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        chunks: list[str] = []
        if len(lines) <= 1:
            chunks = [para]
        else:
            cats = {_classify_unit(ln) for ln in lines}
            chunks = lines if len(cats) > 1 else [para]

        for chunk in chunks:
            # Oraciones mezcladas (retomo + soft CTA en el mismo renglón).
            sents = [s.strip() for s in _SENTENCE_SPLIT_RE.split(chunk) if s.strip()]
            if len(sents) <= 1:
                units.append(chunk)
                continue
            cats = {_classify_unit(s) for s in sents}
            if len(cats) == 1:
                units.append(chunk)
            else:
                units.extend(sents)
    return units


def _classify_unit(unit: str) -> str:
    s = (unit or "").strip()
    if not s:
        return "empty"
    if _GREETING_RE.match(s) or _PRESENTATION_RE.match(s):
        return "open"
    if _SIGNATURE_RE.match(s):
        return "sign"
    if extract_close_family_hits(s):
        return "close"
    # CTA duro (pregunta) o soft invite.
    if extract_meeting_ctas(s) or extract_soft_meeting_ctas(s):
        only = s.strip()
        ctas = extract_meeting_ctas(s)
        if ctas:
            if only in ctas or only.rstrip(".") in ctas:
                return "cta"
            rest = only
            for c in ctas:
                rest = rest.replace(c, "").strip()
            if len(rest) < 12:
                return "cta"
            if only.startswith("¿") and only.endswith("?") and is_meeting_cta(only):
                return "cta"
        if extract_soft_meeting_ctas(s):
            return "cta"
        return "cta"
    if has_prior_leverage(s) and len(s) < 220:
        return "leverage"
    return "info"


def enforce_outbound_structure_order(body: str) -> str:
    """Reordena bloques: open/leverage → info → CTA → close → firma."""
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text

    # Un solo CTA (hard gana; soft se saca si hay hard).
    text = enforce_one_meeting_cta_per_message(text)
    # Si quedó soft + hard por algún path, limpiar soft otra vez.
    if extract_meeting_ctas(text) and extract_soft_meeting_ctas(text):
        text = strip_soft_meeting_ctas(text)

    units = _split_units(text)
    if not units:
        return text

    open_u: list[str] = []
    lev_u: list[str] = []
    info_u: list[str] = []
    cta_u: list[str] = []
    close_u: list[str] = []
    sign_u: list[str] = []

    for u in units:
        cat = _classify_unit(u)
        if cat == "open":
            open_u.append(u)
        elif cat == "leverage":
            lev_u.append(u)
        elif cat == "cta":
            cta_u.append(u)
        elif cat == "close":
            close_u.append(u)
        elif cat == "sign":
            sign_u.append(u)
        else:
            info_u.append(u)

    # Preferir CTA en pregunta; si hay varios, el último hard.
    hard_ctas = [c for c in cta_u if extract_meeting_ctas(c)]
    soft_ctas = [c for c in cta_u if not extract_meeting_ctas(c)]
    if hard_ctas:
        cta_u = [hard_ctas[-1]]
    elif soft_ctas:
        cta_u = [soft_ctas[-1]]
    else:
        cta_u = []

    if len(close_u) > 1:
        close_u = [close_u[-1]]

    cleaned_info: list[str] = []
    for block in info_u:
        if extract_close_family_hits(block):
            cleaned = strip_close_family(block)
            if cleaned:
                cleaned_info.append(cleaned)
        elif extract_soft_meeting_ctas(block):
            cleaned = strip_soft_meeting_ctas(block)
            if cleaned:
                cleaned_info.append(cleaned)
        else:
            cleaned_info.append(block)
    info_u = cleaned_info

    parts: list[str] = []
    parts.extend(open_u)
    parts.extend(lev_u)
    parts.extend(info_u)
    parts.extend(cta_u)
    parts.extend(close_u)
    parts.extend(sign_u)

    out = "\n\n".join(p.strip() for p in parts if p and p.strip())
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def structure_order_rules_for_prompt() -> str:
    return (
        "REGLA DURA ORDEN (todos los canales): "
        "1) inicio + apalancamiento del hilo; "
        "2) información/valor (se puede omitir en últimos toques); "
        "3) un solo CTA; "
        "4) cierre. "
        "PROHIBIDO soft invite + CTA pregunta (es doble CTA). "
        "No pongas el cierre ni el CTA antes de la info.\n"
    )
