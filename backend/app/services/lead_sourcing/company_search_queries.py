"""Queries site-specific enriquecidas con ICP completo (no keyword suelta)."""

from __future__ import annotations

from app.models.campaign import Campaign
from app.services.lead_sourcing.icp_intelligence import CompanyIcpProfile, parse_company_icp


def build_company_search_queries(
    campaign: Campaign,
    *,
    profile: CompanyIcpProfile | None = None,
    max_queries: int | None = None,
) -> list[str]:
    icp = profile or parse_company_icp(campaign)
    primary = icp.primary_target_phrase()
    secondary = icp.secondary_phrases()
    loc = f" {icp.country}" if icp.country else ""
    ind = icp.industry

    templates = [
        f'site:linkedin.com/company "{primary}"',
        f"site:crunchbase.com/organization {primary}{loc}",
        f"site:crunchbase.com/organization {ind}{loc}",
        f"site:wellfound.com/company {primary}",
        f'site:linkedin.com/company {ind}{loc}',
        f"site:clutch.co/profile {primary}",
        f"site:g2.com/products {primary}",
        f"site:producthunt.com {ind} software",
        f"site:capterra.com {ind} software{loc}",
        f'site:getapp.com {ind} software{loc}',
    ]
    for phrase in secondary:
        templates.append(f'site:linkedin.com/company "{phrase}"')

    seen: set[str] = set()
    out: list[str] = []
    for q in templates:
        key = q.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(q.strip())

    if max_queries is not None and max_queries > 0:
        return out[:max_queries]
    return out


def build_company_search_query(campaign: Campaign) -> str:
    icp = parse_company_icp(campaign)
    return icp.primary_target_phrase()
