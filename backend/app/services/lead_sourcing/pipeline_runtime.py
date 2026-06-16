"""Utilidades de runtime del pipeline (stdlib + constantes, sin ciclos de import)."""

from __future__ import annotations

import concurrent.futures
from collections.abc import Callable
from typing import TypeVar

from app.services.lead_sourcing.timeouts_config import STAGE_TIMEOUT_SEC

T = TypeVar("T")


class PipelineTimeoutError(TimeoutError):
    """Timeout de una etapa del pipeline."""

    def __init__(self, message: str, *, label: str = "") -> None:
        super().__init__(message)
        self.label = label


def stage_timeout_sec(step: str) -> int:
    return STAGE_TIMEOUT_SEC.get(step, 120)


def run_with_timeout(func: Callable[[], T], timeout_sec: float, label: str) -> T:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(func)
        try:
            return fut.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError as e:
            raise PipelineTimeoutError(
                f"Timeout ({int(timeout_sec)}s): {label}",
                label=label,
            ) from e
