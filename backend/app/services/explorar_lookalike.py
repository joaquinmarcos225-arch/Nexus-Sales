"""Explorar · empresas parecidas a partir del link de LinkedIn de la empresa.

Prospeo no acepta el URL en el lookalike: primero enrich-company → company_id,
después search-person con company_lookalike.company_oids.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.models.explorar import ExplorarSearchCache

_logger = logging.getLogger(__name__)

MAX_SEEDS = 3
CACHE_TTL = timedelta(days=90)
_COMPANY_PATH_RE = re.compile(r"^/company/([^/]+)", re.IGNORECASE)
_PERSON_PATH_RE = re.compile(r"^/(in|sales/people)/", re.IGNORECASE)


@dataclass
class LookalikeResolve:
    seeds: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    person_urls: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


def _strip_url(raw: str) -> str:
    return str(raw or "").strip().strip("<>\"'")


def parse_lookalike_inputs(values: list[str] | None) -> list[str]:
    """Parte links pegados con coma, espacio o salto de línea. Máximo 3 únicos."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        for part in re.split(r"[\s,;]+", _strip_url(raw)):
            token = part.strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
            if len(out) >= MAX_SEEDS:
                return out
    return out


def classify_linkedin_url(raw: str) -> tuple[str, str | None]:
    """Devuelve ('company', url canónica), ('person', None) o ('invalid', None)."""
    url = _strip_url(raw)
    if not url:
        return "invalid", None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid", None
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host.endswith("linkedin.com"):
        return "invalid", None
    path = unquote(parsed.path or "")
    if _PERSON_PATH_RE.match(path):
        return "person", None
    match = _COMPANY_PATH_RE.match(path)
    if not match:
        return "invalid", None
    slug = match.group(1).strip()
    if not slug or slug.lower() in {"search", "admin", "setup"}:
        return "invalid", None
    return "company", f"https://www.linkedin.com/company/{slug}"


def _cache_key(canonical_url: str) -> str:
    raw = json.dumps({"lookalike_company": canonical_url}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_seed_cache(db: Session, canonical_url: str) -> dict[str, Any] | None:
    row = db.get(ExplorarSearchCache, _cache_key(canonical_url))
    if row is None:
        return None
    created = row.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created is not None and datetime.now(timezone.utc) - created > CACHE_TTL:
        db.delete(row)
        db.flush()
        return None
    try:
        data = json.loads(row.payload_json)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("company_id") else None


def _write_seed_cache(db: Session, canonical_url: str, seed: dict[str, Any]) -> None:
    key = _cache_key(canonical_url)
    existing = db.get(ExplorarSearchCache, key)
    if existing is not None:
        db.delete(existing)
        db.flush()
    db.add(
        ExplorarSearchCache(
            cache_key=key,
            company_id=0,
            payload_json=json.dumps(seed, ensure_ascii=False),
        )
    )
    db.flush()


def _seed_from_company(company: dict[str, Any], canonical_url: str) -> dict[str, Any] | None:
    oid = str(
        company.get("company_id") or company.get("company_oid") or company.get("id") or ""
    ).strip()
    if not oid:
        return None
    domain = str(company.get("domain") or company.get("website") or "").strip()
    if domain:
        domain = domain.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").strip("/")
    name = str(company.get("name") or company.get("company_name") or "").strip()
    return {
        "company_id": oid,
        "name": name or None,
        "domain": domain or None,
        "linkedin_url": canonical_url,
    }


def resolve_lookalike_seeds(
    db: Session,
    urls: list[str] | None,
    *,
    enrich_fn: Callable[[str], dict[str, Any]] | None = None,
) -> LookalikeResolve:
    """Pasa links de empresa a IDs Prospeo. Cache 90 días (mismo plazo gratis de enrich)."""
    out = LookalikeResolve()
    tokens = parse_lookalike_inputs(urls)
    if not tokens:
        return out

    enrich = enrich_fn
    if enrich is None:
        from app.services.lead_sourcing.providers.prospeo_mvp import enrich_company_linkedin

        def enrich(url: str) -> dict[str, Any]:
            return enrich_company_linkedin(linkedin_url=url)

    for raw in tokens:
        kind, canonical = classify_linkedin_url(raw)
        if kind == "person":
            out.person_urls.append(raw)
            continue
        if kind != "company" or not canonical:
            out.invalid.append(raw)
            continue
        cached = _read_seed_cache(db, canonical)
        if cached:
            out.seeds.append(cached)
            continue
        try:
            company = enrich(canonical) or {}
        except Exception as e:
            from app.services.explorar import ExplorarError
            from app.services.lead_sourcing.providers.base import ProviderNotConfiguredError

            if isinstance(e, ExplorarError):
                raise
            if isinstance(e, ProviderNotConfiguredError):
                raise ExplorarError(str(e), status_code=503) from e
            _logger.warning("lookalike enrich falló %s: %s", canonical, e)
            out.unresolved.append(raw)
            continue
        seed = _seed_from_company(company, canonical) if isinstance(company, dict) else None
        if seed is None:
            out.unresolved.append(raw)
            continue
        _write_seed_cache(db, canonical, seed)
        out.seeds.append(seed)
    return out


def lookalike_filter(company_oids: list[str]) -> dict[str, Any] | None:
    oids = [str(o).strip() for o in company_oids if str(o or "").strip()]
    # Un ID por empresa; Prospeo admite hasta 10, nosotros recortamos a 3 seeds.
    seen: list[str] = []
    for oid in oids:
        if oid not in seen:
            seen.append(oid)
        if len(seen) >= MAX_SEEDS:
            break
    if not seen:
        return None
    return {
        "company_oids": seen,
        "minimum_tier": "T2",
        "match_all": False,
        "same_language": False,
    }
