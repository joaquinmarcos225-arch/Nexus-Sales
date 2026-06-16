"""Logs de etapa + recuperación de runs colgados."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.lead_sourcing import pipeline_store as store
from app.services.lead_sourcing.pipeline_runtime import stage_timeout_sec
from app.services.lead_sourcing.timeouts_config import STALE_RUN_BUFFER_SEC

MAX_LOG_ENTRIES = 40

RUNNING_STAGES = frozenset(
    {
        "searching_companies",
        "preparing_phantom",
    "phantom_ready",
        "extracting_people",
        "enriching_contacts",
    }
)

STABLE_STAGE_AFTER: dict[str, str] = {
    "companies": "companies_found",
    "preparing_phantom": "phantom_ready",
    "prepare_phantom": "phantom_ready",
    "extract_companies": "phantom_ready",
    "people": "leads_detected",
    "enrich": "ready_to_import",
    "score": "leads_detected",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_log(
    meta: dict,
    *,
    step: str,
    stage: str,
    event: str,
    message: str = "",
    duration_ms: int | None = None,
    result_count: int | None = None,
) -> dict:
    logs: list[dict] = meta.get("stage_logs") if isinstance(meta.get("stage_logs"), list) else []
    entry = {
        "step": step,
        "stage": stage,
        "event": event,
        "message": message,
        "at": _utc_now_iso(),
    }
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if result_count is not None:
        entry["result_count"] = result_count
    logs.append(entry)
    meta["stage_logs"] = logs[-MAX_LOG_ENTRIES:]
    return entry


def mark_running(meta: dict, *, step: str, stage: str) -> None:
    meta["run_state"] = {
        "running": True,
        "step": step,
        "stage": stage,
        "started_at": _utc_now_iso(),
        "stale": False,
    }
    append_log(meta, step=step, stage=stage, event="started", message="Request iniciado")


def mark_finished(meta: dict, *, step: str, stage: str, message: str = "", result_count: int | None = None) -> None:
    started_at = (meta.get("run_state") or {}).get("started_at")
    duration_ms = _duration_ms_since(started_at)
    append_log(
        meta,
        step=step,
        stage=stage,
        event="completed",
        message=message or "Request terminado",
        duration_ms=duration_ms,
        result_count=result_count,
    )
    meta["run_state"] = {
        "running": False,
        "step": step,
        "stage": stage,
        "started_at": started_at,
        "finished_at": _utc_now_iso(),
        "stale": False,
    }


def mark_error(meta: dict, *, step: str, stage: str, message: str, event: str = "error") -> None:
    started_at = (meta.get("run_state") or {}).get("started_at")
    duration_ms = _duration_ms_since(started_at)
    append_log(
        meta,
        step=step,
        stage=stage,
        event=event,
        message=message,
        duration_ms=duration_ms,
    )
    run = meta.get("run_state") if isinstance(meta.get("run_state"), dict) else {}
    run["running"] = False
    run["stale"] = event == "timeout"
    run["finished_at"] = _utc_now_iso()
    meta["run_state"] = run


def _duration_ms_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        start = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - start
        return max(0, int(delta.total_seconds() * 1000))
    except ValueError:
        return None


def _parse_iso(iso: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _clear_stuck_run(
    db: Session,
    row,
    meta: dict,
    *,
    step: str,
    stage: str,
    message: str,
) -> dict:
    mark_error(meta, step=step, stage=stage, message=message, event="timeout")
    meta["last_error"] = message
    stable = STABLE_STAGE_AFTER.get(step) or STABLE_STAGE_AFTER.get(stage)
    if stable:
        store.set_stage(row, stable)
    elif row.stage in RUNNING_STAGES:
        store.set_stage(row, "error")
    store.save_meta(row, meta)
    db.commit()
    return meta


def recover_stale_run(
    db: Session,
    row,
    meta: dict,
    *,
    max_running_sec: int | None = None,
) -> dict:
    """Si un paso quedó colgado (crash/timeout HTTP), liberar run_state y permitir reintentar."""
    run = meta.get("run_state")
    if not isinstance(run, dict) or not run.get("running"):
        return meta

    started_at = run.get("started_at")
    step = str(run.get("step") or "")
    stage = str(run.get("stage") or row.stage or "idle")

    if not started_at:
        return _clear_stuck_run(
            db,
            row,
            meta,
            step=step or stage,
            stage=stage,
            message="Pipeline quedó en «running» sin timestamp. Se liberó automáticamente.",
        )

    started = _parse_iso(str(started_at))
    if started is None:
        return _clear_stuck_run(
            db,
            row,
            meta,
            step=step or stage,
            stage=stage,
            message="Pipeline en «running» con fecha inválida. Se liberó automáticamente.",
        )

    limit = max_running_sec if max_running_sec is not None else (stage_timeout_sec(step) + STALE_RUN_BUFFER_SEC)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    if elapsed <= limit:
        return meta

    msg = (
        f"El paso «{step or stage}» quedó colgado ({int(elapsed)}s). "
        "Probable timeout o caída del backend. Reintentá el paso."
    )
    return _clear_stuck_run(db, row, meta, step=step or stage, stage=stage, message=msg)


def run_state_read(meta: dict) -> dict[str, Any]:
    run = meta.get("run_state")
    if not isinstance(run, dict):
        return {"running": False, "step": None, "stage": None, "started_at": None, "stale": False}
    return {
        "running": bool(run.get("running")),
        "step": run.get("step"),
        "stage": run.get("stage"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "stale": bool(run.get("stale")),
    }


def run_state_for_read(meta: dict) -> dict[str, Any]:
    """
  Para GET: nunca bloquear la UI por run_state.running colgado.
  No escribe en BD — solo ajusta lo que se devuelve al frontend.
    """
    raw = run_state_read(meta)
    if not raw.get("running"):
        return raw
    raw["running"] = False
    raw["stale"] = True
    if not raw.get("finished_at"):
        raw["finished_at"] = _utc_now_iso()
    return raw
