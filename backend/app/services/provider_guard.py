"""Guardas persistidas de cuota proveedor (sobreviven reinicios Railway)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation_job_state import AutomationJobState

_BRAVE_GUARD_KEY = "provider_guard:brave_quota"
_PROSPEO_GUARD_KEY = "provider_guard:prospeo_low"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _get_guard(db: Session, job_key: str) -> dict[str, Any]:
    row = db.scalars(select(AutomationJobState).where(AutomationJobState.job_key == job_key)).first()
    meta = row.last_result_meta if row and isinstance(row.last_result_meta, dict) else {}
    return meta


def _set_guard(db: Session, job_key: str, meta: dict[str, Any]) -> None:
    row = db.scalars(select(AutomationJobState).where(AutomationJobState.job_key == job_key)).first()
    if row is None:
        row = AutomationJobState(job_key=job_key, run_count=0)
        db.add(row)
    row.last_result_meta = meta
    row.last_success_at = datetime.now(UTC)
    db.flush()


def brave_pause_until(db: Session | None = None) -> datetime | None:
    """Si hay pausa Brave activa (persistida), devuelve until UTC."""
    if db is None:
        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            meta = _get_guard(db, _BRAVE_GUARD_KEY)
        finally:
            db.close()
    else:
        meta = _get_guard(db, _BRAVE_GUARD_KEY)
    return _parse_iso(meta.get("paused_until"))


def brave_quota_paused(db: Session | None = None) -> bool:
    until = brave_pause_until(db)
    return until is not None and until > datetime.now(UTC)


def mark_brave_quota_paused(
    db: Session | None = None,
    *,
    reason: str = "402",
    cooldown_sec: int | None = None,
) -> datetime:
    """Pausa Brave hasta cooldown (default 7 días o env BRAVE_QUOTA_PAUSE_SEC)."""
    if cooldown_sec is None:
        raw = (os.getenv("BRAVE_QUOTA_PAUSE_SEC") or "").strip()
        cooldown_sec = int(raw) if raw.isdigit() else 7 * 86400
    until = datetime.now(UTC) + timedelta(seconds=max(3600, int(cooldown_sec)))
    meta = {
        "paused_until": until.isoformat(),
        "reason": reason,
        "marked_at": datetime.now(UTC).isoformat(),
    }
    if db is None:
        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            _set_guard(db, _BRAVE_GUARD_KEY, meta)
            db.commit()
        finally:
            db.close()
    else:
        _set_guard(db, _BRAVE_GUARD_KEY, meta)
    return until


def clear_brave_quota_pause(db: Session) -> None:
    row = db.scalars(select(AutomationJobState).where(AutomationJobState.job_key == _BRAVE_GUARD_KEY)).first()
    if row:
        row.last_result_meta = {"cleared_at": datetime.now(UTC).isoformat()}


def prospeo_min_credits_to_source() -> int:
    raw = (os.getenv("PROSPEO_MIN_CREDITS_TO_SOURCE") or "150").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 150


def prospeo_pause_reason(db: Session | None = None) -> str | None:
    """None = OK para sourcing. str = motivo de pausa."""
    try:
        from app.services.lead_sourcing.prospeo_api_health import fetch_prospeo_account_health

        health = fetch_prospeo_account_health()
    except Exception as exc:  # noqa: BLE001
        return f"Prospeo no consultable: {exc}"[:120]

    if not health.configured:
        return None
    floor = prospeo_min_credits_to_source()
    remaining = health.remaining_credits
    if health.search_blocked or health.insufficient_credits:
        return health.detail or "Prospeo bloqueado (sin créditos o plan)"
    if remaining is not None and remaining < floor:
        return f"Prospeo bajo reserva ({remaining} cr < mínimo {floor} para sourcing)"
    return None


def sourcing_providers_blocked(db: Session | None = None) -> tuple[bool, str | None]:
    if (os.getenv("BRAVE_SOURCING_PAUSED") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True, "Brave pausado manualmente (BRAVE_SOURCING_PAUSED)"
    if brave_quota_paused(db):
        until = brave_pause_until(db)
        return True, f"Brave pausado hasta {until.isoformat() if until else '?'}"
    reason = prospeo_pause_reason(db)
    if reason:
        return True, reason
    return False, None
