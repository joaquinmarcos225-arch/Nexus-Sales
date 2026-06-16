"""Timeouts por etapa — re-export (compat)."""

from app.services.lead_sourcing.pipeline_runtime import (
    PipelineTimeoutError,
    run_with_timeout,
    stage_timeout_sec,
)

__all__ = ["PipelineTimeoutError", "run_with_timeout", "stage_timeout_sec"]
