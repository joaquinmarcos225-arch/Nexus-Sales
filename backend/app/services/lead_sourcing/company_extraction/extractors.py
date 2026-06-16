"""Extractores por plataforma (HTML + enlaces embebidos)."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

from app.services.lead_sourcing.company_extraction.fetch import (
    DirectoryFetchError,
    absolutize,
    build_page_urls,
    fetch_html,
)
from app.services.lead_sourcing.company_extraction.models import (
    ExtractedCompanyRow,
    ExtractionSourceResult,
)

# --- Patrones de perfil de empresa por plataforma ---
_LINK_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "wellfound",
        re.compile(
            r'href=["\'](?:https?://(?:www\.)?wellfound\.com)?/company/([a-zA-Z0-9][-a-zA-Z0-9]*)/?["\']',
            re.I,
        ),
        "https://wellfound.com/company/{slug}",
    ),
    (
        "linkedin",
        re.compile(
            r'href=["\'](?:https?://(?:[\w.]+)?linkedin\.com)?/company/([a-zA-Z0-9][-a-zA-Z0-9%]*)/?["\']',
            re.I,
        ),
        "https://www.linkedin.com/company/{slug}",
    ),
    (
        "crunchbase",
        re.compile(
            r'href=["\'](?:https?://(?:www\.)?crunchbase\.com)?/organization/([a-zA-Z0-9][-a-zA-Z0-9]*)/?["\']',
            re.I,
        ),
        "https://www.crunchbase.com/organization/{slug}",
    ),
    (
        "clutch",
        re.compile(
            r'href=["\'](?:https?://(?:www\.)?clutch\.co)?/profile/([a-zA-Z0-9][-a-zA-Z0-9]*)/?["\']',
            re.I,
        ),
        "https://clutch.co/profile/{slug}",
    ),
    (
        "g2",
        re.compile(
            r'href=["\'](?:https?://(?:www\.)?g2\.com)?/products/([a-zA-Z0-9][-a-zA-Z0-9]*)/?["\']',
            re.I,
        ),
        "https://www.g2.com/products/{slug}",
    ),
    (
        "producthunt",
        re.compile(
            r'href=["\'](?:https?://(?:www\.)?producthunt\.com)?/products/([a-zA-Z0-9][-a-zA-Z0-9]*)/?["\']',
            re.I,
        ),
        "https://www.producthunt.com/products/{slug}",
    ),
]

# Metadatos en tarjetas (best-effort)
_LOCATION_RE = re.compile(
    r'(?:location|headquarters|hq|based in)[":\s]+([^"<,\n]{2,80})',
    re.I,
)
_TAGS_RE = re.compile(r'(?:tags|industries)[":\s]+\[([^\]]+)\]', re.I)


def detect_platform(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if "wellfound" in host or "angel.co" in host:
        return "wellfound"
    if "linkedin.com" in host:
        return "linkedin"
    if "crunchbase.com" in host:
        return "crunchbase"
    if "clutch.co" in host:
        return "clutch"
    if "g2.com" in host:
        return "g2"
    if "producthunt.com" in host:
        return "producthunt"
    if "capterra.com" in host:
        return "capterra"
    if "getapp.com" in host:
        return "getapp"
    return "generic"


def extract_from_directory(
    directory_url: str,
    *,
    platform: str | None = None,
    max_companies: int = 30,
    max_pages: int = 3,
) -> tuple[list[ExtractedCompanyRow], ExtractionSourceResult]:
    plat = platform or detect_platform(directory_url)
    result = ExtractionSourceResult(directory_url=directory_url, platform=plat)
    found: dict[str, ExtractedCompanyRow] = {}

    page_urls = build_page_urls(directory_url, max_pages)
    for page_url in page_urls:
        if len(found) >= max_companies:
            break
        try:
            html = fetch_html(page_url)
            result.pages_fetched += 1
        except DirectoryFetchError as e:
            if result.pages_fetched == 0:
                result.error = str(e)
            break

        rows = _extract_from_html(html, page_url, plat)
        rows.extend(_extract_from_next_data(html, page_url, plat))
        for row in rows:
            key = row.profile_url.lower()
            if key not in found:
                found[key] = row
            if len(found) >= max_companies:
                break

    companies = list(found.values())[:max_companies]
    result.companies_found = len(companies)
    return companies, result


def _extract_from_html(html: str, page_url: str, platform: str) -> list[ExtractedCompanyRow]:
    out: list[ExtractedCompanyRow] = []
    patterns = [p for p in _LINK_PATTERNS if p[0] == platform or platform == "generic"]
    if platform == "generic":
        patterns = _LINK_PATTERNS

    for plat, pattern, url_tpl in patterns:
        for match in pattern.finditer(html):
            slug = unescape(match.group(1)).strip().rstrip("/")
            if not slug or slug.lower() in ("search", "jobs", "login", "signup"):
                continue
            profile = url_tpl.format(slug=slug)
            name = _slug_to_name(slug)
            out.append(
                ExtractedCompanyRow(
                    name=name,
                    profile_url=profile,
                    source_platform=plat,
                    source_directory_url=page_url,
                )
            )
    return out


def _extract_from_next_data(html: str, page_url: str, platform: str) -> list[ExtractedCompanyRow]:
    """Parsea __NEXT_DATA__ / JSON embebido (SPAs como Wellfound)."""
    out: list[ExtractedCompanyRow] = []
    for m in re.finditer(
        r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>({.*?})</script>',
        html,
        re.DOTALL | re.I,
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        _walk_json_for_companies(data, page_url, platform, out)
    return out


def _walk_json_for_companies(
    node: object,
    page_url: str,
    platform: str,
    out: list[ExtractedCompanyRow],
    depth: int = 0,
) -> None:
    if depth > 12:
        return
    if isinstance(node, dict):
        slug = node.get("slug") or node.get("company_slug")
        name = node.get("name") or node.get("company_name")
        if slug and name and isinstance(name, str):
            profile = _profile_from_slug(platform, str(slug))
            if profile:
                loc = node.get("location") or node.get("city")
                tags_raw = node.get("tags") or node.get("markets") or []
                tags: list[str] = []
                if isinstance(tags_raw, list):
                    tags = [str(t) for t in tags_raw[:8] if t]
                out.append(
                    ExtractedCompanyRow(
                        name=str(name).strip()[:255],
                        profile_url=profile,
                        location=str(loc)[:120] if loc else None,
                        tags=tags,
                        source_platform=platform,
                        source_directory_url=page_url,
                    )
                )
        for v in node.values():
            _walk_json_for_companies(v, page_url, platform, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:200]:
            _walk_json_for_companies(item, page_url, platform, out, depth + 1)


def _profile_from_slug(platform: str, slug: str) -> str | None:
    slug = slug.strip().strip("/")
    if not slug:
        return None
    templates = {
        "wellfound": f"https://wellfound.com/company/{slug}",
        "linkedin": f"https://www.linkedin.com/company/{slug}",
        "crunchbase": f"https://www.crunchbase.com/organization/{slug}",
        "clutch": f"https://clutch.co/profile/{slug}",
        "g2": f"https://www.g2.com/products/{slug}",
        "producthunt": f"https://www.producthunt.com/products/{slug}",
    }
    return templates.get(platform)


def _slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()[:255]
