"""Get/set investigación reutilizable (snippets web, etc.) con TTL."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.nexus_research_cache import NexusResearchCache

_logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 168  # 7 días
KIND_OUTREACH_SNIPPETS = "outreach_web_snippets"


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_research_key_part(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s[:180]


def outreach_snippets_cache_key(
    *,
    mode: str,
    company_name: str,
    country: str | None,
) -> str | None:
    company = normalize_research_key_part(company_name)
    if not company or company in {"—", "-", "n/a", "sin empresa"}:
        return None
    mode_n = normalize_research_key_part(mode) or "b2b"
    country_n = normalize_research_key_part(country) or "any"
    return f"outreach_snippets:v1:{mode_n}:{company}:{country_n}"


def get_research_payload(
    db: Session,
    cache_key: str,
) -> Any | None:
    if not cache_key:
        return None
    try:
        row = db.scalars(
            select(NexusResearchCache).where(NexusResearchCache.cache_key == cache_key).limit(1)
        ).first()
        if row is None:
            return None
        exp = row.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp is not None and exp < _now():
            return None
        return json.loads(row.payload_json)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("research cache get failed key=%s: %s", cache_key[:80], exc)
        return None


def set_research_payload(
    db: Session,
    *,
    cache_key: str,
    kind: str,
    payload: Any,
    ttl_hours: int = DEFAULT_TTL_HOURS,
) -> None:
    if not cache_key:
        return
    try:
        body = json.dumps(payload, ensure_ascii=False)
        if len(body) > 50_000:
            return
        ttl = max(1, int(ttl_hours))
        expires = _now() + timedelta(hours=ttl)
        row = db.scalars(
            select(NexusResearchCache).where(NexusResearchCache.cache_key == cache_key).limit(1)
        ).first()
        if row is None:
            db.add(
                NexusResearchCache(
                    cache_key=cache_key,
                    kind=kind,
                    payload_json=body,
                    ttl_hours=ttl,
                    expires_at=expires,
                )
            )
        else:
            row.kind = kind
            row.payload_json = body
            row.ttl_hours = ttl
            row.expires_at = expires
        db.flush()
    except Exception as exc:  # noqa: BLE001
        _logger.debug("research cache set failed key=%s: %s", cache_key[:80], exc)
