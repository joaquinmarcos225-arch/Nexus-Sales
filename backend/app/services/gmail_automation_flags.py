"""Bypass temporal de automatización Gmail (scheduler + ticks). No elimina código."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_GMAIL_AUTOMATION_LOGGED = False


def gmail_automation_enabled() -> bool:
    """
    ENABLE_GMAIL_AUTOMATION=1 → scheduler Gmail / inbound / refresh en background activos.
    Cualquier otro valor o ausente → desactivado (no bloquea HTTP ni startup).
    """
    raw = (os.getenv("ENABLE_GMAIL_AUTOMATION") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def log_startup_gmail_automation_flag() -> None:
    global _GMAIL_AUTOMATION_LOGGED
    if _GMAIL_AUTOMATION_LOGGED:
        return
    _GMAIL_AUTOMATION_LOGGED = True
    if gmail_automation_enabled():
        logger.info("[gmail-automation] ENABLE_GMAIL_AUTOMATION=on — ticks Gmail en scheduler activos.")
    else:
        logger.warning(
            "[gmail-automation] ENABLE_GMAIL_AUTOMATION=off — "
            "scheduler Gmail/inbound/calendar/oauth ticks desactivados. "
            "Lead Sourcing y GET /lead-sourcing/* no usan Gmail. "
            "Poné ENABLE_GMAIL_AUTOMATION=1 para reactivar."
        )


def log_gmail_automation_skipped(operation: str) -> None:
    logger.warning("[gmail-automation] skipped %s (ENABLE_GMAIL_AUTOMATION=off)", operation)
