"""Fetch público de sitios corporativos (sin Brave) — Paso G v1."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse

from app.services.lead_sourcing.company_extraction.fetch import DirectoryFetchError, fetch_html
from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain
from app.services.lead_sourcing.providers.web_search_backends import SearchHit

_logger = logging.getLogger(__name__)

_STRIP_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class CompanyPageSignals:
    domain: str
    url: str
    title: str
    description: str
    site_name: str
    industry_hint: str


def _normalize_url(domain_or_url: str) -> str:
    raw = (domain_or_url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw.lstrip('/')}"
    return raw


def _meta_content(html: str, *, name: str | None = None, prop: str | None = None) -> str:
    if not html:
        return ""
    key = name or prop or ""
    if not key:
        return ""
    patterns = (
        rf'<meta[^>]+(?:name|property)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']{{3,500}})',
        rf'<meta[^>]+content=["\']([^"\']{{3,500}})["\'][^>]+(?:name|property)=["\']{re.escape(key)}["\']',
    )
    for pat in patterns:
        m = re.search(pat, html, re.I | re.DOTALL)
        if m:
            return _clean_text(unescape(m.group(1)))
    return ""


def _clean_text(raw: str) -> str:
    t = unescape(raw or "")
    t = _STRIP_TAGS.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:500]


def parse_page_meta(html: str, url: str) -> CompanyPageSignals:
    dom = _website_domain(url) or ""
    title_m = re.search(r"<title[^>]*>([^<]{1,240})</title>", html or "", re.I | re.DOTALL)
    title = _clean_text(title_m.group(1)) if title_m else ""
    description = (
        _meta_content(html, name="description")
        or _meta_content(html, prop="og:description")
        or _meta_content(html, name="twitter:description")
    )
    site_name = _meta_content(html, prop="og:site_name") or _meta_content(html, name="application-name")
    industry_hint = _meta_content(html, name="keywords")[:180]
    return CompanyPageSignals(
        domain=dom,
        url=url,
        title=title,
        description=description,
        site_name=site_name,
        industry_hint=industry_hint,
    )


def fetch_company_page_signals(domain_or_url: str) -> CompanyPageSignals | None:
    """GET homepage corporativa y extrae meta tags. None si falla o HTML vacío."""
    url = _normalize_url(domain_or_url)
    if not url:
        return None
    try:
        html = fetch_html(url)
    except DirectoryFetchError as exc:
        _logger.debug("nexus fetch failed url=%s: %s", url, exc)
        return None
    if not (html or "").strip():
        return None
    sig = parse_page_meta(html, url)
    if not sig.title and not sig.description:
        return None
    try:
        from app.services.lead_sourcing.cogs_runtime_metrics import record_nexus_fetch

        record_nexus_fetch()
    except Exception:
        pass
    return sig


def signals_to_search_hits(sig: CompanyPageSignals) -> list[SearchHit]:
    hits: list[SearchHit] = []
    if sig.title:
        snippet = sig.description or sig.site_name or sig.domain
        hits.append((sig.url, sig.title, snippet))
    elif sig.description:
        hits.append((sig.url, sig.site_name or sig.domain, sig.description))
    return hits


def signals_to_snippet_lines(sig: CompanyPageSignals, *, company_name: str = "") -> list[str]:
    lines: list[str] = []
    label = company_name or sig.site_name or sig.domain
    if sig.title:
        parts = [sig.title.strip()]
        if sig.description:
            parts.append(sig.description.strip())
        parts.append(sig.url)
        lines.append(" — ".join(p for p in parts if p))
    elif sig.description:
        lines.append(f"{label} — {sig.description.strip()} — {sig.url}")
    if sig.industry_hint and len(sig.industry_hint) > 8:
        lines.append(f"{label} — keywords: {sig.industry_hint[:180]}")
    return lines[:3]


def resolve_domain_hint(*urls: str | None) -> str | None:
    for raw in urls:
        dom = _website_domain(raw or "")
        if dom:
            return dom
    return None
