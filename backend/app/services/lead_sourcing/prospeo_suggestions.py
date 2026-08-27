"""Prospeo Search Suggestions — valores canónicos para location / job title."""

from __future__ import annotations

import logging
import threading
from typing import Any, Literal

from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
from app.services.lead_sourcing.providers.prospeo_mvp import _post_json_result

_logger = logging.getLogger(__name__)

_PROSPEO_SUGGESTIONS = "https://api.prospeo.io/search-suggestions"

_cache_lock = threading.Lock()
_location_cache: dict[str, list[str]] = {}
_title_cache: dict[str, list[str]] = {}

SuggestionKind = Literal["location", "job_title"]


def _api_configured() -> bool:
    return bool((getenv("PROSPEO_API_KEY") or "").strip())


def suggest_locations(query: str, *, limit: int = 8) -> list[str]:
    """Devuelve nombres canónicos de ubicación (COUNTRY primero)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    key = q.lower()
    with _cache_lock:
        if key in _location_cache:
            return list(_location_cache[key][:limit])

    if not _api_configured():
        return []

    try:
        result = _post_json_result(_PROSPEO_SUGGESTIONS, {"location_search": q[:80]})
    except (ProviderAPIError, ProviderNotConfiguredError) as e:
        _logger.info("Prospeo location suggestions failed for %r: %s", q, e)
        return []

    payload = result.payload if isinstance(result.payload, dict) else {}
    raw = payload.get("location_suggestions") or []
    names: list[str] = []
    countries: list[str] = []
    others: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                typ = str(item.get("type") or "").upper()
                if not name:
                    continue
                if typ == "COUNTRY":
                    countries.append(name)
                else:
                    others.append(name)
            elif isinstance(item, str) and item.strip():
                others.append(item.strip())
    names = countries + others
    # Dedup preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        lk = n.lower()
        if lk in seen:
            continue
        seen.add(lk)
        unique.append(n)

    with _cache_lock:
        _location_cache[key] = unique
    return unique[:limit]


def suggest_job_titles(query: str, *, limit: int = 8) -> list[str]:
    """Devuelve títulos canónicos ordenados por popularidad."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    key = q.lower()
    with _cache_lock:
        if key in _title_cache:
            return list(_title_cache[key][:limit])

    if not _api_configured():
        return [q]  # fallback: término libre + CONTAINS

    try:
        result = _post_json_result(_PROSPEO_SUGGESTIONS, {"job_title_search": q[:80]})
    except (ProviderAPIError, ProviderNotConfiguredError) as e:
        _logger.info("Prospeo job title suggestions failed for %r: %s", q, e)
        return [q]

    payload = result.payload if isinstance(result.payload, dict) else {}
    raw = payload.get("job_title_suggestions") or []
    titles: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                titles.append(item.strip())
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("title") or "").strip()
                if name:
                    titles.append(name)
    if q.lower() not in {t.lower() for t in titles}:
        titles = [q, *titles]
    with _cache_lock:
        _title_cache[key] = titles
    return titles[:limit]


def clear_suggestions_cache() -> None:
    with _cache_lock:
        _location_cache.clear()
        _title_cache.clear()
