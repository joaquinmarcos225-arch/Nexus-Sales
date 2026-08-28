"""Control de costo del sourcing: límites conservadores y pausa por campaña atascada."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.services.lead_sourcing import pipeline_store as store

_META_KEY = "sourcing_stall"


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


def stall_zero_import_ticks() -> int:
    raw = (os.getenv("SOURCING_STALL_ZERO_IMPORT_TICKS") or "3").strip()
    try:
        return max(1, min(20, int(raw)))
    except ValueError:
        return 3


def stall_cooldown_sec() -> int:
    raw = (os.getenv("SOURCING_STALL_COOLDOWN_SEC") or "").strip()
    if raw.isdigit():
        return max(3600, int(raw))
    return 24 * 3600


def max_auto_passes_default() -> int:
    raw = (
        os.getenv("LEAD_SOURCING_AUTO_MAX_PASSES")
        or os.getenv("LEAD_SOURCING_AUTO_MAX_PASSES")
        or "5"
    ).strip()
    if raw.isdigit():
        return max(1, min(12, int(raw)))
    return 5


def effective_passes_limit(
    remaining_slots: int,
    *,
    max_pipeline_passes: int | None = None,
) -> int:
    need = max(0, int(remaining_slots))
    if need <= 0:
        return 0
    base = max_pipeline_passes if max_pipeline_passes is not None else max_auto_passes_default()
    return max(1, min(base, need, 5))


def passes_for_remaining(remaining_slots: int, *, hard_cap: int | None = None) -> int:
    need = max(0, int(remaining_slots))
    if need <= 0:
        return 0
    cap = hard_cap if hard_cap is not None else max_auto_passes_default()
    suggested = max(1, min(5, need))
    return max(1, min(max(cap, 5), suggested))


def auto_company_limit(remaining_slots: int) -> int:
    need = max(1, int(remaining_slots))
    return min(25, max(5, need + 3))


def auto_people_limit(remaining_slots: int) -> int:
    need = max(1, int(remaining_slots))
    return min(30, max(10, need * 2))


def brave_search_limits(*, n_region_locs: int = 1) -> tuple[int, int]:
    """(max_queries, per_query_limit) — cap bajo para no quemar Brave."""
    n_locs = max(1, int(n_region_locs))
    max_q = min(5, max(3, 2 + n_locs // 2))
    per_query = 6
    return max_q, per_query


def _stall_blob(meta: dict[str, Any]) -> dict[str, Any]:
    raw = meta.get(_META_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def get_stall_state(db: Session, campaign_id: int) -> dict[str, Any]:
    row = store.get_or_create(db, int(campaign_id))
    return _stall_blob(store.load_meta(row))


def is_sourcing_stalled(db: Session, campaign_id: int) -> tuple[bool, str | None]:
    stall = get_stall_state(db, campaign_id)
    if not stall.get("stalled"):
        return False, None
    retry_after = _parse_iso(stall.get("retry_after"))
    if retry_after is not None and retry_after <= datetime.now(UTC):
        return False, None
    reason = stall.get("reason") or "Sourcing pausado (varias rondas sin importar)"
    return True, str(reason)


def should_block_quota_force_full(db: Session, campaign_id: int) -> bool:
    """Evita relanzar Brave full cuando la campaña ya está atascada o Brave no está."""
    stalled, _ = is_sourcing_stalled(db, campaign_id)
    if stalled:
        return True
    stall = get_stall_state(db, campaign_id)
    ticks = int(stall.get("zero_import_ticks") or 0)
    if ticks >= max(1, stall_zero_import_ticks() - 1):
        return True
    from app.services.provider_guard import sourcing_providers_blocked

    blocked, _ = sourcing_providers_blocked(db)
    if blocked:
        return True
    from app.services.lead_sourcing.providers.web_search_backends import brave_quota_exhausted

    return bool(brave_quota_exhausted())


def record_sourcing_tick(
    db: Session,
    campaign_id: int,
    *,
    imported: int,
    pipeline_runs: int,
) -> bool:
    """
    Actualiza contador de ticks sin importación. Devuelve True si quedó en stall.
    """
    row = store.get_or_create(db, int(campaign_id))
    meta = store.load_meta(row)
    stall = _stall_blob(meta)
    now = datetime.now(UTC)

    if int(imported) > 0:
        meta.pop(_META_KEY, None)
        store.save_meta(row, meta)
        db.flush()
        return False

    ticks = int(stall.get("zero_import_ticks") or 0) + 1
    threshold = stall_zero_import_ticks()
    stall["zero_import_ticks"] = ticks
    stall["last_tick_at"] = now.isoformat()
    stall["last_pipeline_runs"] = int(pipeline_runs)

    if ticks >= threshold:
        stall["stalled"] = True
        stall["stalled_at"] = now.isoformat()
        stall["retry_after"] = (now + timedelta(seconds=stall_cooldown_sec())).isoformat()
        stall["reason"] = (
            f"Sourcing pausado tras {ticks} intentos sin importar "
            f"(ICP estricto o pool agotado). Reintento automático más tarde."
        )
    meta[_META_KEY] = stall
    store.save_meta(row, meta)
    db.flush()
    return bool(stall.get("stalled"))


def clear_sourcing_stall(db: Session, campaign_id: int) -> None:
    row = store.get_or_create(db, int(campaign_id))
    meta = store.load_meta(row)
    if _META_KEY in meta:
        meta.pop(_META_KEY, None)
        store.save_meta(row, meta)
        db.flush()


def clear_all_sourcing_stalls(db: Session) -> int:
    """Limpia stalls de todas las campañas (p. ej. tras recargar créditos)."""
    from sqlalchemy import select

    from app.models.lead_sourcing_pipeline import LeadSourcingPipeline

    cleared = 0
    rows = db.scalars(select(LeadSourcingPipeline)).all()
    for row in rows:
        meta = store.load_meta(row)
        if _META_KEY in meta:
            meta.pop(_META_KEY, None)
            store.save_meta(row, meta)
            cleared += 1
    if cleared:
        db.flush()
    return cleared
