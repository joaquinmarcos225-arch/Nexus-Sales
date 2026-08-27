"""Capa propia antes de Brave/SerpAPI — Paso G."""

from __future__ import annotations

import logging

from app.services.lead_sourcing.nexus_public_fetch import (
    fetch_company_page_signals,
    resolve_domain_hint,
    signals_to_search_hits,
    signals_to_snippet_lines,
)
from app.services.lead_sourcing.providers.web_search_backends import SearchHit, search_web

_logger = logging.getLogger(__name__)


def try_own_fetch_hits(
    *,
    company_domain: str | None = None,
    company_website: str | None = None,
) -> list[SearchHit]:
    dom = resolve_domain_hint(company_website, company_domain)
    if not dom:
        return []
    sig = fetch_company_page_signals(dom)
    if sig is None:
        return []
    return signals_to_search_hits(sig)


def try_own_fetch_snippets(
    *,
    company_name: str = "",
    company_domain: str | None = None,
    company_website: str | None = None,
) -> list[str]:
    dom = resolve_domain_hint(company_website, company_domain)
    if not dom:
        return []
    sig = fetch_company_page_signals(dom)
    if sig is None:
        return []
    return signals_to_snippet_lines(sig, company_name=company_name)


def search_web_tiered(
    query: str,
    *,
    limit: int = 20,
    country: str | None = None,
    provider: str = "web_search",
    company_domain: str | None = None,
    company_website: str | None = None,
    min_own_hits: int = 1,
) -> list[SearchHit]:
    """
    Orden: fetch directo al dominio conocido → Brave/SerpAPI/DDG.
    """
    own = try_own_fetch_hits(
        company_domain=company_domain,
        company_website=company_website,
    )
    if len(own) >= min_own_hits:
        return own[:limit]
    return search_web(
        query,
        limit=limit,
        country=country,
        provider=provider,
    )
