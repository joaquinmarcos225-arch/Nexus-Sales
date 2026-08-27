"""Scoring de relevancia ICP + normalización de empresas candidatas."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_search_classifier import CompanyResultKind
from app.services.lead_sourcing.icp_intelligence import CompanyIcpProfile
from app.services.lead_sourcing.icp_region import text_has_conflicting_country

MIN_COMPANY_RELEVANCE = 55
MIN_COMPANY_RELEVANCE_STRICT = 70  # cuando hay industria ICP configurada
MIN_DIRECTORY_RELEVANCE = 42

_NOISE_NAME = re.compile(
    r"^(saas|b2b|software|cloud|tech)\s*$|"
    r"^(recruit|staffing|agency|consultant|university|college|school)\b|"
    r"\b(university|college|berkeley|recruiting|recruitment|staffing|consulting firm)\b",
    re.I,
)


def score_company_relevance(
    profile: CompanyIcpProfile,
    *,
    name: str,
    url: str,
    title: str = "",
    snippet: str = "",
    result_kind: str = "company",
) -> int:
    blob = f"{name} {title} {snippet} {url}".lower()

    if company_is_noisy_for_sourcing(name, url):
        return 0

    if profile.country and text_has_conflicting_country(blob, profile.country):
        return 0

    for neg in profile.all_negatives():
        if neg in blob:
            return 0

    if _NOISE_NAME.search(name.strip()):
        return max(0, 25)

    score = 38

    industry = profile.industry.lower().strip()
    industry_required = bool(getattr(profile, "industry_user_set", True))
    if industry:
        if industry in blob:
            score += 28
        else:
            tokens = [t for t in re.split(r"[\s,/\-]+", industry) if len(t) > 2]
            hits = sum(1 for t in tokens if t in blob)
            if tokens:
                ratio = hits / len(tokens)
                if ratio >= 0.8:
                    score += 22
                elif ratio >= 0.5:
                    score += 12
                elif hits == 1 and len(tokens) > 1:
                    score += 4
                elif industry_required:
                    # Sin evidencia de industria en el snippet: penalizar fuerte solo si el ICP la pidió.
                    score -= 22
                else:
                    # Industria soft/default: no castigar; bonus leve si huele a software.
                    if any(s in blob for s in ("software", "saas", "platform", "b2b", "startup")):
                        score += 8

    geo_hit = False
    if profile.country and profile.country.lower() in blob:
        geo_hit = True
    if not geo_hit:
        for name in getattr(profile, "region_country_names", None) or []:
            n = (name or "").lower().strip()
            if len(n) >= 4 and n in blob:
                geo_hit = True
                break
    if geo_hit:
        score += 14

    if profile.company_stage and profile.company_stage.lower() in blob:
        score += 10

    for kw in profile.positive_keywords:
        if len(kw) > 3 and kw.lower() in blob:
            score += 3

    if "b2b" in industry and "b2b" in blob:
        score += 6
    if any(s in blob for s in ("software", "platform", "cloud", "technology", "startup")):
        score += 5

    if result_kind == CompanyResultKind.directory_source.value:
        score += 8

    if _looks_like_wrong_saas_match(name, profile):
        score -= 30

    return max(0, min(100, score))


def _looks_like_wrong_saas_match(name: str, profile: CompanyIcpProfile) -> bool:
    n = name.lower().strip()
    ind = profile.industry.lower()
    if "saas" not in ind:
        return False
    if re.search(r"saas\s+(group|berkeley|university|college|school|institute|recruit|talent)", n):
        return True
    if re.search(r"\b(agency|recruiter|staffing|consultant|careers|talent)\b", n):
        return True
    return False


def company_is_noisy_for_sourcing(name: str, url: str = "") -> bool:
    """Bloqueo duro de empresas talent/careers/recruiting antes de Prospeo."""
    from app.services.lead_sourcing.icp_import_gate import is_noisy_company

    return is_noisy_company(name, company_domain=url)

def canonical_company_key(url: str, name: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    if "linkedin.com" in host and "/company/" in path:
        slug = _path_slug(path, "company")
        if slug:
            return f"name:{_normalize_name(slug)}"

    if "crunchbase.com" in host and "/organization/" in path:
        slug = _path_slug(path, "organization")
        if slug:
            return f"name:{_normalize_name(slug)}"

    if "clutch.co" in host and "/profile/" in path:
        slug = _path_slug(path, "profile")
        if slug:
            return f"name:{_normalize_name(slug)}"

    if "g2.com" in host and "/products/" in path:
        slug = _path_slug(path, "products")
        if slug:
            return f"g2:{slug}"

    return f"name:{_normalize_name(name)}"


def _path_slug(path: str, anchor: str) -> str | None:
    parts = [p for p in path.split("/") if p]
    try:
        idx = parts.index(anchor)
        if idx + 1 < len(parts):
            return parts[idx + 1].split("?")[0].strip() or None
    except ValueError:
        pass
    return None


def _normalize_name(name: str) -> str:
    n = re.sub(r"\|.*$", "", name.lower())
    n = re.sub(r"\b(inc|llc|ltd|corp|corporation|company|co)\b", "", n)
    n = re.sub(r"[^a-z0-9]", "", n)
    return n or name.lower()[:32]


def merge_company_candidates(
    candidates: list[CompanyCandidateRead],
) -> list[CompanyCandidateRead]:
    """Fusiona LinkedIn + Crunchbase + mismo nombre como una empresa."""
    merged: dict[str, CompanyCandidateRead] = {}

    ordered = sorted(
        candidates,
        key=lambda c: ((c.icp_relevance_score or 0), (c.quality_score or 0)),
        reverse=True,
    )

    for c in ordered:
        key = c.canonical_key or canonical_company_key(c.website_url or "", c.name)
        if key not in merged:
            merged[key] = c.model_copy(update={"canonical_key": key})
            continue

        prev = merged[key]
        best_url = _prefer_url(prev.website_url, c.website_url)
        best_name = _prefer_display_name(prev, c)
        merged[key] = prev.model_copy(
            update={
                "name": best_name,
                "website_url": best_url,
                "icp_relevance_score": max(prev.icp_relevance_score or 0, c.icp_relevance_score or 0),
                "quality_score": max(prev.quality_score or 0, c.quality_score or 0),
                "confidence": max(prev.confidence or 0, c.confidence or 0),
                "normalized_company_name": prev.normalized_company_name
                or c.normalized_company_name,
                "source_type": _prefer_source_type(prev.source_type, c.source_type),
                "description": prev.description or c.description,
            }
        )

    return sorted(
        merged.values(),
        key=lambda c: (
            (c.confidence or 0),
            (c.icp_relevance_score or 0),
            (c.quality_score or 0),
        ),
        reverse=True,
    )


_SOURCE_PRIORITY = (
    "linkedin_company",
    "crunchbase_company",
    "startup_card",
    "own_domain",
    "clutch_profile",
    "g2_product",
    "producthunt_product",
    "directory_listing",
)


def _prefer_source_type(a: str | None, b: str | None) -> str | None:
    for st in _SOURCE_PRIORITY:
        if a == st:
            return a
        if b == st:
            return b
    return a or b


def _prefer_display_name(prev: CompanyCandidateRead, c: CompanyCandidateRead) -> str:
    for row in (prev, c):
        if row.normalized_company_name:
            return row.normalized_company_name
    return prev.name or c.name


def _prefer_url(a: str | None, b: str | None) -> str | None:
    for u in (a, b):
        if u and "linkedin.com/company/" in u:
            return u
    for u in (a, b):
        if u and "crunchbase.com/organization/" in u:
            return u
    return a or b


def passes_relevance_threshold(
    candidate: CompanyCandidateRead,
    *,
    strict_industry: bool = False,
) -> bool:
    rel = candidate.icp_relevance_score or 0
    if candidate.result_kind == CompanyResultKind.directory_source.value:
        return rel >= MIN_DIRECTORY_RELEVANCE
    floor = MIN_COMPANY_RELEVANCE_STRICT if strict_industry else MIN_COMPANY_RELEVANCE
    return rel >= floor
