"""Normalización de texto plano outbound (email / mensajes)."""

from __future__ import annotations

import re
from typing import Any, Sequence

_GREETING_RE = re.compile(
    r"^(hola|buen[oa]s(?:\s+d[ií]as|\s+tardes|\s+noches)?|hey|hi|hello)\b",
    re.IGNORECASE,
)

_OPENING_GREETING_PREFIX = re.compile(
    r"^(?:hola|buen[oa]s?(?:\s+d[ií]as|\s+tardes|\s+noches)?|hey|hi|hello)"
    r"(?:\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][\wÁÉÍÓÚÜÑáéíóúüñ'’.-]{0,40})?"
    r"\s*[,!]?\s*",
    re.IGNORECASE,
)


def normalize_outbound_email_body(text: str | None) -> str:
    """
    Quita saltos de línea 'blandos' dentro de un párrafo (el modelo a veces
    parte oraciones cada ~40 caracteres). Respeta párrafos separados por línea en blanco
    y deja el saludo corto ('Hola X,') en su propia primera línea.
    """
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    # Une palabras partidas con guión al final de línea: "prospec-\nción" → "prospección"
    raw = re.sub(r"(\w)-\n+(\w)", r"\1\2", raw)

    paragraphs = re.split(r"\n\s*\n+", raw)
    out: list[str] = []
    for para in paragraphs:
        lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
        if not lines:
            continue
        if (
            len(lines) >= 2
            and _GREETING_RE.match(lines[0])
            and lines[0].endswith(",")
            and len(lines[0]) <= 48
        ):
            rest = " ".join(lines[1:])
            out.append(f"{lines[0]}\n{rest}" if rest else lines[0])
        else:
            out.append(" ".join(lines))
    return "\n\n".join(out)


def _msg_direction(msg: Any) -> str:
    if msg is None:
        return ""
    if isinstance(msg, dict):
        return str(msg.get("direction") or "").strip().lower()
    return str(getattr(msg, "direction", "") or "").strip().lower()


def conversation_allows_opening_greeting(history: Sequence[Any] | None) -> bool:
    """
    Saludo solo en frío o en la 1ª réplica tras el primer inbound.
    A partir del 2º mensaje nuestro en el hilo: sin Hola / Buen día.
    """
    msgs = list(history or [])
    saw_inbound = False
    outbound_after_inbound = 0
    for msg in msgs:
        direction = _msg_direction(msg)
        if direction == "inbound":
            saw_inbound = True
        elif direction == "outbound" and saw_inbound:
            outbound_after_inbound += 1
    if not saw_inbound:
        return True
    return outbound_after_inbound == 0


def strip_opening_greeting(text: str | None) -> str:
    """Saca 'Hola X,' / 'Buen día,' del inicio (línea o prefijo)."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    parts = raw.split("\n", 1)
    first = parts[0].strip()
    rest = parts[1].lstrip("\n") if len(parts) > 1 else ""
    match = _OPENING_GREETING_PREFIX.match(first)
    if not match:
        return raw
    remainder = first[match.end() :].strip()
    if remainder:
        if remainder[0].islower():
            remainder = remainder[0].upper() + remainder[1:]
        return f"{remainder}\n{rest}".strip() if rest else remainder
    return rest.strip()


def apply_opening_greeting_policy(
    text: str | None,
    *,
    allow_greeting: bool,
) -> str:
    raw = (text or "").strip()
    if not raw or allow_greeting:
        return raw
    return strip_opening_greeting(raw)
