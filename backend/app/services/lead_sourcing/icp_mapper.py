"""Transforma ICP de campaña en consultas de búsqueda web."""

from __future__ import annotations

import re
from typing import Any

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import LeadSourcingFilters
from app.services import campaign_icp

_EMPLOYEE_RANGE_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b1[\s\-–]*10\b|micro|startup", re.I), "1,10"),
    (re.compile(r"\b11[\s\-–]*20\b", re.I), "11,20"),
    (re.compile(r"\b21[\s\-–]*50\b|pequeñ", re.I), "21,50"),
    (re.compile(r"\b51[\s\-–]*100\b|mediana", re.I), "51,100"),
    (re.compile(r"\b101[\s\-–]*200\b", re.I), "101,200"),
    (re.compile(r"\b201[\s\-–]*500\b", re.I), "201,500"),
    (re.compile(r"\b501[\s\-–]*1000\b", re.I), "501,1000"),
    (re.compile(r"\b1001|1k\+|enterprise|grande", re.I), "1001,5000"),
]


def _clean(value: str | None) -> str | None:
    if value is None or campaign_icp.is_icp_token_empty(value):
        return None
    return value.strip()


def _split_titles(role: str | None) -> list[str]:
    if not role:
        return []
    parts = re.split(r"[,;/|]+", role)
    return [p.strip() for p in parts if p.strip()]


def _location_tokens(country: str | None) -> list[str]:
    c = _clean(country)
    if not c:
        return []
    # Apollo acepta "Argentina", "Buenos Aires, Argentina", etc.
    return [c]


def _employee_ranges(size: str | None) -> list[str]:
    s = _clean(size)
    if not s:
        return []
    for pattern, apollo_range in _EMPLOYEE_RANGE_MAP:
        if pattern.search(s):
            return [apollo_range]
    return []


def build_people_filters(
    campaign: Campaign,
    overrides: LeadSourcingFilters | None = None,
) -> dict[str, Any]:
    """Query params para mixed_people/api_search."""
    o = overrides or LeadSourcingFilters()
    filters: dict[str, Any] = {}

    titles = o.person_titles or _split_titles(campaign.target_role)
    if titles:
        filters["person_titles[]"] = titles

    locs = o.person_locations or o.organization_locations or _location_tokens(campaign.target_country)
    if locs:
        filters["organization_locations[]"] = locs

    if o.person_seniorities:
        filters["person_seniorities[]"] = o.person_seniorities

    keywords = o.q_keywords or _clean(campaign.target_industry)
    if keywords:
        filters["q_keywords"] = keywords

    ranges = o.organization_num_employees_ranges or _employee_ranges(campaign.target_company_size)
    if ranges:
        filters["organization_num_employees_ranges[]"] = ranges

    if o.organization_ids:
        filters["organization_ids[]"] = o.organization_ids

    return filters


def build_company_filters(
    campaign: Campaign,
    overrides: LeadSourcingFilters | None = None,
) -> dict[str, Any]:
    o = overrides or LeadSourcingFilters()
    filters: dict[str, Any] = {}

    locs = o.organization_locations or _location_tokens(campaign.target_country)
    if locs:
        filters["organization_locations[]"] = locs

    keywords = o.q_keywords or _clean(campaign.target_industry)
    if keywords:
        filters["q_organization_keyword_tags[]"] = [keywords]

    ranges = o.organization_num_employees_ranges or _employee_ranges(campaign.target_company_size)
    if ranges:
        filters["organization_num_employees_ranges[]"] = ranges

    return filters


def build_google_company_query(campaign: Campaign) -> str:
    """Alias legacy (nombre histórico) → build_company_search_query."""
    from app.services.lead_sourcing.company_search_queries import build_company_search_query

    return build_company_search_query(campaign)


def filters_summary(filters: dict[str, Any]) -> dict[str, Any]:
    """Versión legible para la UI."""
    out: dict[str, Any] = {}
    for k, v in filters.items():
        key = k.replace("[]", "")
        out[key] = v
    return out
