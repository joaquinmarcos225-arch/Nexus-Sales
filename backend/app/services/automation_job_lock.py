"""Locks persistentes para evitar jobs duplicados (un solo worker recomendado)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation_job_state import AutomationJobState


def get_or_create_job_row(db: Session, job_key: str) -> AutomationJobState:
    row = db.scalars(select(AutomationJobState).where(AutomationJobState.job_key == job_key)).first()
    if row is None:
        row = AutomationJobState(job_key=job_key, run_count=0)
        db.add(row)
        db.flush()
    return row


def try_acquire_job(db: Session, job_key: str, *, lock_ttl_seconds: int) -> AutomationJobState | None:
    now = datetime.now(UTC)
    row = get_or_create_job_row(db, job_key)
    db.refresh(row)
    if row.locked_until is not None and row.locked_until > now:
        return None
    row.locked_until = now + timedelta(seconds=lock_ttl_seconds)
    row.last_started_at = now
    row.last_error = None
    row.run_count = int(row.run_count or 0) + 1
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def finish_job_success(
    db: Session,
    job_key: str,
    *,
    meta: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    row = db.scalars(select(AutomationJobState).where(AutomationJobState.job_key == job_key)).first()
    if row is None:
        return
    row.locked_until = None
    row.last_finished_at = now
    row.last_success_at = now
    row.last_error = None
    row.last_result_meta = meta
    db.add(row)
    db.commit()


def finish_job_error(db: Session, job_key: str, exc: BaseException) -> None:
    now = datetime.now(UTC)
    row = db.scalars(select(AutomationJobState).where(AutomationJobState.job_key == job_key)).first()
    if row is None:
        return
    row.locked_until = None
    row.last_finished_at = now
    row.last_error = f"{type(exc).__name__}: {exc}"[:4000]
    db.add(row)
    db.commit()
