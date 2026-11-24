"""Divide copy LinkedIn en burbujas cortas (varios envíos manuales, estilo chat)."""

from __future__ import annotations

import re

BUBBLE_SEP = "\n⏵\n"
_MAX_CHARS = 320
_MAX_WORDS = 55


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def split_linkedin_outbound_bubbles(body: str, *, first_touch: bool = False) -> list[str]:
    """
    Parte el texto en bloques enviables por separado.
    Preferencia: párrafos (doble salto); si hay un muro, corta por oraciones.
    """
    raw = (body or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    if BUBBLE_SEP in raw:
        parts = [p.strip() for p in raw.split(BUBBLE_SEP) if p.strip()]
        if parts:
            return parts

    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(paras) >= 2:
        return _merge_tiny_bubbles(paras)

    if first_touch and len(paras) == 1 and len(paras[0]) > 180:
        return _split_first_touch_single_block(paras[0])

    if len(paras) == 1 and (_word_count(paras[0]) > _MAX_WORDS or len(paras[0]) > _MAX_CHARS):
        return _split_by_sentences(paras[0])

    return paras if paras else [raw]


def format_linkedin_draft_storage(bubbles: list[str]) -> str:
    cleaned = [b.strip() for b in bubbles if (b or "").strip()]
    if len(cleaned) <= 1:
        return cleaned[0] if cleaned else ""
    return BUBBLE_SEP.join(cleaned)


def parse_linkedin_draft_storage(draft: str | None) -> list[str]:
    raw = (draft or "").strip()
    if not raw:
        return []
    if BUBBLE_SEP in raw:
        return [p.strip() for p in raw.split(BUBBLE_SEP) if p.strip()]
    # Separador suelto (legacy / pegados mal).
    if "⏵" in raw:
        return [p.strip() for p in raw.split("⏵") if p.strip()]
    return [raw]


def linkedin_full_clipboard_text(draft: str | None) -> str:
    """Texto completo para copiar/pegar (nunca solo la 1ª burbuja)."""
    raw = (draft or "").strip()
    if not raw:
        return ""
    parts = parse_linkedin_draft_storage(raw)
    if len(parts) <= 1:
        return raw
    return "\n\n".join(parts)


def _merge_tiny_bubbles(paras: list[str]) -> list[str]:
    out: list[str] = []
    buf = ""
    for p in paras:
        if not buf:
            buf = p
            continue
        if len(buf) + len(p) < 120 and _word_count(buf) + _word_count(p) <= _MAX_WORDS:
            buf = f"{buf}\n\n{p}"
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def _split_by_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return [text]
    out: list[str] = []
    buf = ""
    for p in parts:
        if not buf:
            buf = p
        elif len(buf) + len(p) + 1 <= _MAX_CHARS and _word_count(buf) + _word_count(p) <= _MAX_WORDS:
            buf = f"{buf} {p}"
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out or [text]


def _split_first_touch_single_block(text: str) -> list[str]:
    """Heurística: saludo → presentación → motivo (3 burbujas)."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) >= 3:
        return _merge_tiny_bubbles(lines[:3]) + lines[3:]
    low = text.lower()
    greet_m = re.match(
        r"^((?:hola|buen[oa]s?\s+(?:d[ií]as|tardes|noches))[^.?\n]{0,80}[.?!]?)\s*",
        text,
        flags=re.I,
    )
    if greet_m:
        greet = greet_m.group(1).strip()
        rest = text[greet_m.end() :].strip()
        intro_m = re.match(
            r"^((?:soy|me llamo|te escribo desde|te contacto desde)[^.?\n]{0,120}[.?!]?)\s*",
            rest,
            flags=re.I,
        )
        if intro_m:
            intro = intro_m.group(1).strip()
            pitch = rest[intro_m.end() :].strip()
            bubbles = [b for b in (greet, intro, pitch) if b]
            if len(bubbles) >= 2:
                return bubbles
    return _split_by_sentences(text)
