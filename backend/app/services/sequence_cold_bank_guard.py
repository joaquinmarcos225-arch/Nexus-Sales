"""Regla 11 — un toque que no es el 1º de la secuencia NUNCA usa plantilla cold.

Ej. 2º LinkedIn tras un mail: banco `*-linkedin-fu-*`, no `*-linkedin-cold-*`.
"""

from __future__ import annotations

import re

_COLD_TEMPLATE_RE = re.compile(r"(?i)(?:^|-)cold(?:-|\d|$)")


def is_cold_bank_template_id(template_id: str | None) -> bool:
    tid = (template_id or "").strip()
    if not tid:
        return False
    # seller / style no son banco cold
    if tid.startswith("seller"):
        return False
    return bool(_COLD_TEMPLATE_RE.search(tid)) or "-cold-" in tid.lower()


def cold_bank_forbidden(
    *,
    prior_touches: list[dict] | None,
    template_id: str | None = None,
) -> bool:
    """True si hay historial y el template/id es cold (violación regla 11)."""
    if not (prior_touches or []):
        return False
    return is_cold_bank_template_id(template_id)


def cold_bank_rules_for_prompt(*, sequence_first_touch: bool) -> str:
    if sequence_first_touch:
        return (
            "REGLA DURA BANCO: este es el 1º toque de la secuencia — estructura cold OK "
            "(presentación + ancla + valor). Los siguientes toques serán follow-up.\n"
        )
    return (
        "REGLA DURA BANCO / LINKEDIN: NO es el 1º toque de la secuencia. "
        "PROHIBIDO plantilla cold (re-presentarte, «Vi que sos…», pitch de Día 1). "
        "Usá follow-up: retomo del hilo + CTA. El banco debe ser FU, nunca cold.\n"
    )
