"""Restricción local de auto-envío Meow a destinatarios explícitos (pruebas controladas)."""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)


def _normalize_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def meow_autosend_allowlist_digits() -> set[str] | None:
    """
    Si NEXUS_MEOW_AUTOSEND_ALLOW_DIGITS está definido (lista comma-separated),
    solo esos destinos pueden recibir outbounds Meow desde auto-reply.
    Vacío / unset → sin restricción extra (comportamiento previo).
    """
    raw = (os.getenv("NEXUS_MEOW_AUTOSEND_ALLOW_DIGITS") or "").strip()
    if not raw:
        return None
    out = {_normalize_digits(part) for part in raw.split(",") if part.strip()}
    return out or None


def meow_autosend_allowed_for_peer(to_peer_or_digits: str) -> tuple[bool, str]:
    allow = meow_autosend_allowlist_digits()
    if allow is None:
        return True, "allowlist_off"
    digits = _normalize_digits(to_peer_or_digits.split("@", 1)[0])
    if len(digits) < 8:
        return False, "invalid_destination"
    if digits in allow:
        return True, "allowlist_match"
    for allowed in allow:
        if digits.endswith(allowed) or allowed.endswith(digits):
            if len(allowed) >= 10 and len(digits) >= 10:
                return True, "allowlist_match_suffix"
    logger.info(
        "meow autosend blocked by allowlist peer_digits=%s…%s",
        digits[:4],
        digits[-4:] if len(digits) >= 4 else "",
    )
    return False, "autosend_blocked_allowlist"
