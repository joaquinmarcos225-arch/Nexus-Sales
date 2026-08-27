"""Queries site-specific enriquecidas con ICP (por país, sin mega-frases entre comillas)."""

from __future__ import annotations

from app.models.campaign import Campaign
from app.services.lead_sourcing.icp_industry_search import industry_search_terms
from app.services.lead_sourcing.icp_intelligence import CompanyIcpProfile, parse_company_icp
from app.services.lead_sourcing.icp_region import resolve_region_search_context


def _location_labels(icp: CompanyIcpProfile, campaign: Campaign) -> list[str]:
    region = resolve_region_search_context(campaign.target_country)
    if region and region.query_country_labels:
        return list(region.query_country_labels)
    if region and region.country_names:
        # Fallback: nombres cortos ya en country_names (sin sinónimos de 2 letras).
        return [n.title() for n in region.country_names if len(n) >= 4][:6]
    loc = (icp.search_location_phrase or icp.country or "").strip()
    return [loc] if loc else [""]


def build_company_search_queries(
    campaign: Campaign,
    *,
    profile: CompanyIcpProfile | None = None,
    max_queries: int | None = None,
    query_offset: int = 0,
) -> list[str]:
    icp = profile or parse_company_icp(campaign)
    # Si el usuario no eligió industria, usamos términos soft (no fingimos industria dura).
    search_industry = icp.industry if icp.industry_user_set else "B2B SaaS"
    ind_terms = industry_search_terms(search_industry)
    if not ind_terms:
        ind_terms = ["B2B SaaS", "B2B software"]
    ind_primary = ind_terms[0]
    ind_alt = ind_terms[1] if len(ind_terms) > 1 else ind_primary

    locations = _location_labels(icp, campaign)
    templates: list[str] = []

    # Primero: web abierta (dominios reales). LinkedIn/directorios después (suelen venir sin website).
    for loc in locations:
        loc_bit = f" {loc}" if loc else ""
        templates.extend(
            [
                f'"{ind_primary}"{loc_bit} (empresa OR company OR agencia) -site:linkedin.com -site:crunchbase.com',
                f'"{ind_primary}"{loc_bit} (oficial OR website OR "sitio web") -site:linkedin.com',
                f"{ind_alt}{loc_bit} empresa -linkedin -crunchbase",
            ]
        )

    for loc in locations:
        loc_bit = f" {loc}" if loc else ""
        templates.extend(
            [
                f'site:linkedin.com/company "{ind_primary}"{loc_bit}',
                f"site:crunchbase.com/organization {ind_primary}{loc_bit}",
                f"site:wellfound.com/company {ind_primary}{loc_bit}",
                f"site:linkedin.com/company {ind_alt}{loc_bit}",
            ]
        )

    # Diversidad global (sin forzar país en el string).
    templates.extend(
        [
            f"site:g2.com/products {ind_alt}",
            f"site:producthunt.com {ind_primary} startup",
            f"site:clutch.co/profile {ind_primary}",
            f"site:capterra.com {ind_primary}",
        ]
    )

    if icp.buyer_persona:
        persona = icp.buyer_persona.strip()
        for loc in locations[:3]:
            loc_bit = f" {loc}" if loc else ""
            templates.append(f'site:linkedin.com/company "{ind_primary}" {persona}{loc_bit}')

    seen: set[str] = set()
    out: list[str] = []
    for q in templates:
        key = q.lower().strip()
        if key in seen or len(key) < 12:
            continue
        seen.add(key)
        out.append(q.strip())

    if query_offset and out:
        # Rotar el orden entre pasadas de bootstrap.
        off = abs(int(query_offset)) % len(out)
        out = out[off:] + out[:off]

    if max_queries is not None and max_queries > 0:
        return out[:max_queries]
    return out


def build_company_search_query(campaign: Campaign) -> str:
    icp = parse_company_icp(campaign)
    return icp.primary_target_phrase()
