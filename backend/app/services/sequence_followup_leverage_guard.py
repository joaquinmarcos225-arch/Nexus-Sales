"""Regla dura — todo follow-up se apalanca del mensaje/canal anterior.

Sin «Vi que sos…» en FU: el apalancamiento es el hilo previo
(«te había escrito por LinkedIn», «retomo…»). Nunca un mensaje suelto.
"""

from __future__ import annotations

import re

_LEVERAGE_RE = re.compile(
    r"(?i)\b("
    r"te\s+hab[ií]a\s+escrito|"
    r"te\s+escrib[ií]|"
    r"te\s+envi[eé]|"
    r"retomo|"
    r"vuelvo\s+(?:sobre|a\s+escribir)|"
    r"mensaje\s+anterior|"
    r"mail\s+anterior|"
    r"por\s+(?:linkedin|whatsapp|mail|email|ac[aá])\b|"
    r"lo\s+que\s+te\s+(?:dec[ií]a|comentaba)|"
    r"hilo\s+(?:anterior|previo)"
    r")\b"
)

_GREETING_RE = re.compile(
    r"(?is)^\s*(hola[^\n]*\n+)",
)


def _norm_channel(channel: str) -> str:
    ch = (channel or "").strip().lower()
    if ch in ("li", "linkedin"):
        return "linkedin"
    if ch in ("wa", "whatsapp", "wpp"):
        return "whatsapp"
    if ch in ("mail", "email", "e-mail"):
        return "email"
    return ch or "email"


def _channel_label(channel: str) -> str:
    ch = _norm_channel(channel)
    if ch == "linkedin":
        return "LinkedIn"
    if ch == "whatsapp":
        return "WhatsApp"
    return "mail"


def has_prior_leverage(body: str) -> bool:
    return bool(_LEVERAGE_RE.search(body or ""))


def build_retomo_line(prior_touches: list[dict] | None) -> str:
    prior = prior_touches or []
    if not prior:
        return "Retomo el mensaje anterior."
    last = prior[-1]
    last_ch = _norm_channel(str(last.get("channel") or "email"))
    label = _channel_label(last_ch)
    return f"Te había escrito por {label}."


def inject_prior_leverage(body: str, *, prior_touches: list[dict] | None) -> str:
    text = (body or "").replace("\r\n", "\n").strip()
    if not text:
        return text
    line = build_retomo_line(prior_touches)
    m = _GREETING_RE.match(text)
    if m:
        rest = text[m.end() :].lstrip()
        return f"{m.group(1).rstrip()}\n\n{line}\n\n{rest}".strip()
    return f"{line}\n\n{text}".strip()


def enforce_followup_prior_leverage(
    *,
    body: str,
    prior_touches: list[dict] | None,
    sequence_first_touch: bool,
) -> str:
    """Si no es 1º toque y falta apalancamiento del hilo, lo inserta."""
    text = (body or "").strip()
    if not text or sequence_first_touch or not prior_touches:
        return text
    if has_prior_leverage(text):
        return text
    return inject_prior_leverage(text, prior_touches=prior_touches)


def followup_leverage_rules_for_prompt(*, sequence_first_touch: bool) -> str:
    if sequence_first_touch:
        return ""
    return (
        "REGLA DURA APALANCAMIENTO: este NO es el 1º toque. "
        "OBLIGATORIO retomar el hilo previo («te había escrito por LinkedIn/mail/WhatsApp», "
        "«retomo…»). PROHIBIDO mensaje suelto sin apalancarse del anterior. "
        "No re-anclar rol/empresa («Vi que sos…»).\n"
    )
