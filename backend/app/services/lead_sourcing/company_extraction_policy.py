"""Política de calidad para empresas ICP → Phantom."""

from __future__ import annotations

from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_name_normalizer import is_seo_listing_title


def _is_generic_name(name: str) -> bool:
    from app.services.lead_sourcing.lead_sourcing_company_targeting import (
        is_generic_company_name,
    )

    return is_generic_company_name(name)

MIN_EXTRACTION_CONFIDENCE = 70
MIN_WEB_SEARCH_COMPANY_CONFIDENCE = 50
MIN_PHANTOM_COMPANY_CONFIDENCE = 70

PHANTOM_ELIGIBLE_SOURCE_TYPES = frozenset(
    {
        "crunchbase_company",
        "linkedin_company",
        "startup_card",
    }
)

SOURCE_CONFIDENCE_BASE: dict[str, int] = {
    "crunchbase_company": 92,
    "linkedin_company": 90,
    "startup_card": 85,
    "own_domain": 80,
    "clutch_profile": 55,
    "g2_product": 50,
    "producthunt_product": 52,
    "directory_listing": 40,
}


def compute_extraction_confidence(
    *,
    source_type: str,
    icp_relevance_score: int,
    quality_score: int,
    normalized_name: str | None,
    raw_title: str = "",
) -> int:
    if is_seo_listing_title(raw_title):
        return 0
    name = (normalized_name or "").strip()
    if not name or _is_generic_name(name):
        return min(35, icp_relevance_score)

    base = SOURCE_CONFIDENCE_BASE.get(source_type, 45)
    blended = int(round(base * 0.55 + icp_relevance_score * 0.35 + quality_score * 0.1))
    if source_type in PHANTOM_ELIGIBLE_SOURCE_TYPES:
        blended = max(blended, base - 5)
    else:
        blended = min(blended, 65)
    return max(0, min(100, blended))


def passes_web_search_company_row(
    candidate: CompanyCandidateRead,
    *,
    min_relevance: int | None = None,
) -> bool:
    if candidate.result_kind != "company":
        return False
    conf = candidate.confidence or 0
    if conf < MIN_WEB_SEARCH_COMPANY_CONFIDENCE:
        return False
    from app.services.lead_sourcing.company_relevance import (
        MIN_COMPANY_RELEVANCE,
        MIN_COMPANY_RELEVANCE_STRICT,
    )

    floor = (
        int(min_relevance)
        if min_relevance is not None
        else MIN_COMPANY_RELEVANCE_STRICT
    )
    floor = max(MIN_COMPANY_RELEVANCE, min(MIN_COMPANY_RELEVANCE_STRICT, floor))
    if (candidate.icp_relevance_score or 0) < floor:
        return False
    name = candidate.normalized_company_name or candidate.name
    if not name or _is_generic_name(name):
        return False
    if is_seo_listing_title(candidate.name):
        return False
    # Listicles / directorios SEO: no sirven como empleador ICP.
    from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

    blob = " ".join(
        filter(
            None,
            [
                (candidate.company_domain or "").lower(),
                (_website_domain(candidate.website_url) or "").lower(),
                (candidate.website_url or "").lower(),
            ],
        )
    )
    for frag in (
        "growthlist.",
        "saasworthy.",
        "saashub.",
        "craft.co",
        "cbinsights.com",
    ):
        if frag in blob:
            return False
    return True


def passes_phantom_company_gate(candidate: CompanyCandidateRead) -> bool:
    if candidate.result_kind != "company":
        return False
    st = (candidate.source_type or "").strip()
    if st not in PHANTOM_ELIGIBLE_SOURCE_TYPES:
        return False
    conf = candidate.confidence or 0
    if conf < MIN_PHANTOM_COMPANY_CONFIDENCE:
        return False
    name = candidate.normalized_company_name or candidate.name
    if not name or _is_generic_name(name):
        return False
    return True


def display_company_name(candidate: CompanyCandidateRead) -> str:
    return (candidate.normalized_company_name or candidate.name or "").strip()
