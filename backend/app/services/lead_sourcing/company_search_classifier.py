"""Clasificación estricta — solo plataformas B2B de confianza (whitelist)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from app.services.lead_sourcing.company_extraction_policy import compute_extraction_confidence
from app.services.lead_sourcing.company_name_normalizer import (
    is_seo_listing_title,
    name_from_slug,
    normalize_company_name,
)


class CompanyResultKind(str, Enum):
    company = "company"
    directory_source = "directory_source"


@dataclass(frozen=True)
class ClassifiedCompanyHit:
    url: str
    title: str
    name: str
    kind: CompanyResultKind
    quality_score: int
    dedupe_key: str
    source_type: str = ""
    normalized_name: str = ""
    confidence: int = 0


# --- Rechazo obligatorio (URL) ---
_MANDATORY_URL_FRAGMENTS = (
    "/blog",
    "/blogs",
    "reddit.com",
    "/reddit",
    "/jobs",
    "/job/",
    "/job-",
    "jobs.",
    "/article",
    "/articles",
    "/help",
    "/docs",
    "/documentation",
    "/learn",
    "/guide",
    "/guides",
    "glossary",
    "/news/",
    "/post/",
    "/posts/",
    "medium.com",
    "quora.com",
    "wikipedia.org",
    "stackoverflow.com",
    "youtube.com",
    "/careers",
    "/salary",
    "/tutorial",
    "/courses/",
    "/wiki/",
)

# --- Rechazo obligatorio (título) ---
_MANDATORY_TITLE_REJECT = re.compile(
    r"("
    r"what is|what are|definition|definitions|examples|how to|how-to|"
    r"\bguide\b|guides|jobs?|salary|salaries|"
    r"o que é|o que e|como funciona|cómo funciona|qué es|que es|"
    r"meaning of|tutorial|learn about|explained|"
    r"best practices|vs\.|versus|comparison|compared"
    r")",
    re.I,
)

# --- Solo estos dominios pueden pasar el filtro ---
_TRUSTED_PLATFORM_HOSTS = (
    "linkedin.com",
    "crunchbase.com",
    "clutch.co",
    "g2.com",
    "wellfound.com",
    "angel.co",
    "producthunt.com",
    "capterra.com",
    "getapp.com",
    "softwareadvice.com",
    "trustradius.com",
)

_LIST_TITLE = re.compile(
    r"\b(top \d+|best \d+|best |leading |list of|directory|directorio|"
    r"startup directory|companies to watch|startups to watch|ranking)\b",
    re.I,
)


def classify_company_hit(url: str, title: str) -> ClassifiedCompanyHit | None:
    """
    Whitelist estricta: solo perfiles de empresa o directorios en plataformas B2B.
    Todo lo demás (blogs, Reddit, sitios SEO aleatorios) se descarta.
    """
    link = (url or "").strip()
    if not link:
        return None

    url_lower = link.lower()
    if _mandatory_url_reject(url_lower):
        return None

    title_clean = (title or "").strip()
    parsed = urlparse(link if "://" in link else f"https://{link}")
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "/").lower()

    if title_clean and _MANDATORY_TITLE_REJECT.search(title_clean):
        if not _is_company_profile_path(host, path):
            return None
    if title_clean and is_seo_listing_title(title_clean):
        if not _is_company_profile_path(host, path):
            return None

    if not host or not _is_trusted_platform(host):
        return None

    # LinkedIn: solo /company/ (no jobs, pulse, posts)
    if "linkedin.com" in host:
        if "/company/" not in path:
            return None
        return _match_linkedin_company(host, path, _clean_title(title_clean), link)

    if "crunchbase.com" in host:
        if "/organization/" in path:
            return _match_crunchbase_org(host, path, _clean_title(title_clean), link)
        return _match_directory(host, path, title_clean, link, label="Listado Crunchbase", score=58)

    if "clutch.co" in host:
        if "/profile/" in path:
            return _match_clutch_profile(host, path, _clean_title(title_clean))
        return _match_directory(host, path, title_clean, link, label="Listado Clutch", score=60)

    if "g2.com" in host:
        if "/products/" in path:
            return _match_g2_product(host, path, _clean_title(title_clean))
        return _match_directory(host, path, title_clean, link, label="Listado G2", score=59)

    if "wellfound.com" in host or "angel.co" in host:
        if "/company/" in path:
            return _match_wellfound_company(host, path, _clean_title(title_clean))
        return _match_directory(host, path, title_clean, link, label="Listado Wellfound", score=57)

    if "producthunt.com" in host:
        if "/products/" in path:
            return _match_producthunt_product(host, path, _clean_title(title_clean))
        return _match_directory(host, path, title_clean, link, label="Listado Product Hunt", score=56)

    if any(d in host for d in ("capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com")):
        return _match_directory(
            host,
            path,
            title_clean,
            link,
            label=f"Directorio {_platform_label(host)}",
            score=55,
        )

    return None


def _is_company_profile_path(host: str, path: str) -> bool:
    if "linkedin.com" in host and "/company/" in path:
        return True
    if "crunchbase.com" in host and "/organization/" in path:
        return True
    if "clutch.co" in host and "/profile/" in path:
        return True
    if "g2.com" in host and "/products/" in path:
        return True
    if ("wellfound.com" in host or "angel.co" in host) and "/company/" in path:
        return True
    if "producthunt.com" in host and "/products/" in path:
        return True
    return False


def _mandatory_url_reject(url_lower: str) -> bool:
    return any(frag in url_lower for frag in _MANDATORY_URL_FRAGMENTS)


def _is_trusted_platform(host: str) -> bool:
    return any(p in host for p in _TRUSTED_PLATFORM_HOSTS)


def _match_directory(
    host: str,
    path: str,
    title: str,
    link: str,
    *,
    label: str,
    score: int,
) -> ClassifiedCompanyHit | None:
    if _mandatory_url_reject(link.lower()):
        return None
    if title and _MANDATORY_TITLE_REJECT.search(title):
        return None
    name = label
    return ClassifiedCompanyHit(
        url=link,
        title=title or name,
        name=name[:255],
        kind=CompanyResultKind.directory_source,
        quality_score=score,
        dedupe_key=f"dir:{host}{path.rstrip('/')[:120]}",
        source_type="directory_listing",
        normalized_name=name[:255],
        confidence=compute_extraction_confidence(
            source_type="directory_listing",
            icp_relevance_score=42,
            quality_score=score,
            normalized_name=name,
            raw_title=title,
        ),
    )


def _match_linkedin_company(
    host: str, path: str, title: str, link: str
) -> ClassifiedCompanyHit | None:
    slug = _path_segment(path, "company")
    if not slug or slug in ("search", "jobs", "posts", "feed"):
        return None
    resolved = _resolve_company_name(title, slug, prefer_slug=False)
    if not resolved:
        return None
    display, normalized = resolved
    st = "linkedin_company"
    return ClassifiedCompanyHit(
        url=f"https://www.linkedin.com/company/{slug}",
        title=title or display,
        name=display[:255],
        kind=CompanyResultKind.company,
        quality_score=100,
        dedupe_key=f"li:{slug}",
        source_type=st,
        normalized_name=normalized[:255],
        confidence=compute_extraction_confidence(
            source_type=st,
            icp_relevance_score=75,
            quality_score=100,
            normalized_name=normalized,
            raw_title=title,
        ),
    )


def _match_crunchbase_org(
    host: str, path: str, title: str, link: str
) -> ClassifiedCompanyHit | None:
    slug = _path_segment(path, "organization")
    if not slug:
        return None
    resolved = _resolve_company_name(title, slug, prefer_slug=False)
    if not resolved:
        return None
    display, normalized = resolved
    st = "crunchbase_company"
    return ClassifiedCompanyHit(
        url=f"https://www.crunchbase.com/organization/{slug}",
        title=title or display,
        name=display[:255],
        kind=CompanyResultKind.company,
        quality_score=98,
        dedupe_key=f"cb:{slug}",
        source_type=st,
        normalized_name=normalized[:255],
        confidence=compute_extraction_confidence(
            source_type=st,
            icp_relevance_score=78,
            quality_score=98,
            normalized_name=normalized,
            raw_title=title,
        ),
    )


def _match_clutch_profile(host: str, path: str, title: str) -> ClassifiedCompanyHit | None:
    slug = _path_segment(path, "profile")
    if not slug:
        return None
    resolved = _resolve_company_name(title, slug, prefer_slug=True)
    if not resolved:
        return None
    display, normalized = resolved
    st = "clutch_profile"
    return ClassifiedCompanyHit(
        url=f"https://clutch.co/profile/{slug}",
        title=title or display,
        name=display[:255],
        kind=CompanyResultKind.company,
        quality_score=92,
        dedupe_key=f"clutch:{slug}",
        source_type=st,
        normalized_name=normalized[:255],
        confidence=compute_extraction_confidence(
            source_type=st,
            icp_relevance_score=55,
            quality_score=92,
            normalized_name=normalized,
            raw_title=title,
        ),
    )


def _match_g2_product(host: str, path: str, title: str) -> ClassifiedCompanyHit | None:
    slug = _path_segment(path, "products")
    if not slug:
        return None
    resolved = _resolve_company_name(title, slug, prefer_slug=True)
    if not resolved:
        return None
    display, normalized = resolved
    st = "g2_product"
    return ClassifiedCompanyHit(
        url=f"https://www.g2.com/products/{slug}",
        title=title or display,
        name=display[:255],
        kind=CompanyResultKind.company,
        quality_score=90,
        dedupe_key=f"g2:{slug}",
        source_type=st,
        normalized_name=normalized[:255],
        confidence=compute_extraction_confidence(
            source_type=st,
            icp_relevance_score=50,
            quality_score=90,
            normalized_name=normalized,
            raw_title=title,
        ),
    )


def _match_wellfound_company(host: str, path: str, title: str) -> ClassifiedCompanyHit | None:
    slug = _path_segment(path, "company")
    if not slug:
        return None
    resolved = _resolve_company_name(title, slug, prefer_slug=False)
    if not resolved:
        return None
    display, normalized = resolved
    base = "wellfound.com" if "wellfound" in host else "angel.co"
    st = "startup_card"
    return ClassifiedCompanyHit(
        url=f"https://{base}/company/{slug}",
        title=title or display,
        name=display[:255],
        kind=CompanyResultKind.company,
        quality_score=88,
        dedupe_key=f"wf:{slug}",
        source_type=st,
        normalized_name=normalized[:255],
        confidence=compute_extraction_confidence(
            source_type=st,
            icp_relevance_score=72,
            quality_score=88,
            normalized_name=normalized,
            raw_title=title,
        ),
    )


def _match_producthunt_product(host: str, path: str, title: str) -> ClassifiedCompanyHit | None:
    slug = _path_segment(path, "products")
    if not slug:
        return None
    resolved = _resolve_company_name(title, slug, prefer_slug=True)
    if not resolved:
        return None
    display, normalized = resolved
    st = "producthunt_product"
    return ClassifiedCompanyHit(
        url=f"https://www.producthunt.com/products/{slug}",
        title=title or display,
        name=display[:255],
        kind=CompanyResultKind.company,
        quality_score=85,
        dedupe_key=f"ph:{slug}",
        source_type=st,
        normalized_name=normalized[:255],
        confidence=compute_extraction_confidence(
            source_type=st,
            icp_relevance_score=52,
            quality_score=85,
            normalized_name=normalized,
            raw_title=title,
        ),
    )


def _resolve_company_name(
    title: str,
    slug: str | None,
    *,
    prefer_slug: bool,
) -> tuple[str, str] | None:
    """(display_name, normalized_name) — Clutch/G2 usan slug, no título SEO."""
    slug_name = name_from_slug(slug or "")
    title_norm = normalize_company_name(title) if not prefer_slug else None
    if prefer_slug:
        if not slug_name:
            return None
        return slug_name, slug_name
    pick = title_norm or slug_name
    if not pick:
        return None
    normalized = normalize_company_name(pick) or pick
    return normalized, normalized


def _path_segment(path: str, anchor: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    try:
        idx = parts.index(anchor)
        if idx + 1 < len(parts):
            slug = parts[idx + 1].split("?")[0].strip()
            if slug and slug not in ("search", "jobs", "login", "signup"):
                return slug
    except ValueError:
        pass
    return None


def _clean_title(title: str) -> str:
    normalized = normalize_company_name(title)
    if normalized:
        return normalized
    t = re.sub(r"\s*[\|\-–—:].*$", "", (title or "").strip(), count=1)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:255]


def _platform_label(host: str) -> str:
    if "capterra" in host:
        return "Capterra"
    if "getapp" in host:
        return "GetApp"
    if "g2.com" in host:
        return "G2"
    return host.split(".")[0].title()
