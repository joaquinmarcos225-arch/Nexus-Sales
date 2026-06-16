"""Trazas temporales de diagnóstico — Lead Sourcing (quitar cuando termine el debug)."""

from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger("lead_sourcing.diag")


class DiagTrace:
    """Loguea ENTER → pasos → RESPONSE; si cuelga, el último paso queda en logs."""

    def __init__(self, label: str, **ctx: Any) -> None:
        self.label = label
        self.ctx = ctx
        self.t0 = time.perf_counter()
        self.last_step = "ENTER"
        _logger.warning("[LS-DIAG] >>> ENTER %s ctx=%s", label, ctx)

    def step(self, name: str, **extra: Any) -> None:
        self.last_step = name
        elapsed_ms = int((time.perf_counter() - self.t0) * 1000)
        _logger.warning(
            "[LS-DIAG] ... step=%s elapsed_ms=%s label=%s ctx=%s extra=%s",
            name,
            elapsed_ms,
            self.label,
            self.ctx,
            extra or None,
        )

    def done(self, **extra: Any) -> None:
        elapsed_ms = int((time.perf_counter() - self.t0) * 1000)
        _logger.warning(
            "[LS-DIAG] <<< RESPONSE label=%s elapsed_ms=%s last_step=%s ctx=%s extra=%s",
            self.label,
            elapsed_ms,
            self.last_step,
            self.ctx,
            extra or None,
        )

    def fail(self, exc: BaseException) -> None:
        elapsed_ms = int((time.perf_counter() - self.t0) * 1000)
        _logger.warning(
            "[LS-DIAG] <<< ERROR label=%s elapsed_ms=%s last_step=%s err=%s ctx=%s",
            self.label,
            elapsed_ms,
            self.last_step,
            exc,
            self.ctx,
        )
