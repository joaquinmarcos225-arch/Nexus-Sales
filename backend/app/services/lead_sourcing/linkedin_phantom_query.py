"""Phantom/LinkedIn — targeting por empresa o modo test rápido."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.lead_sourcing_company_targeting import (
    TargetCompany,
    build_company_search_plans,
    build_test_mode_search_plan,
    collect_target_companies,
    is_contaminated_person_name,
    phantom_role_fallback_order,
    score_lead_company_targeted,
)
from app.services.lead_sourcing.phantom_target_selection import get_phantom_target_selection_audit
from app.services.lead_sourcing.lead_sourcing_company_targeting import (  # noqa: F401
    fuzzy_match_target_company,
)
from app.services.lead_sourcing.phantom_runtime import (
    is_phantom_test_mode,
    phantom_max_companies,
    phantom_max_roles_per_company,
    phantom_skip_company_match_filter,
    phantom_test_query,
)

MIN_COMPANY_FILTER_SCORE = 60

__all__ = [
    "MIN_COMPANY_FILTER_SCORE",
    "PhantomSearchBundle",
    "build_phantom_search_bundle",
    "collect_target_companies",
    "fuzzy_match_target_company",
    "get_min_lead_display_score",
    "is_contaminated_person_name",
    "is_phantom_test_mode",
    "sanitize_icp_keywords",
    "score_lead_company_targeted",
]


def get_min_lead_display_score() -> int:
    raw = (getenv("LEAD_SOURCING_MIN_DISPLAY_SCORE") or "30").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 30
    return max(0, min(100, n))


def sanitize_icp_keywords(keywords: list[str]) -> list[str]:
    return []


@dataclass
class PhantomSearchBundle:
    linkedin_query_exact: str
    linkedin_search_url: str
    site_query: str
    argument_fields: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    company_searches: list[dict[str, Any]] = field(default_factory=list)
    target_companies: list[TargetCompany] = field(default_factory=list)


def build_phantom_search_bundle(
    campaign: Campaign,
    companies: list[CompanyCandidateRead],
    *,
    phantom_queue: dict | None,
) -> PhantomSearchBundle:
    test_mode = is_phantom_test_mode()
    targets = collect_target_companies(
        companies,
        phantom_queue,
        test_mode=test_mode,
        max_companies=phantom_max_companies(),
    )
    selection_audit = get_phantom_target_selection_audit()
    selected_debug = [
        {
            "company": row["name"],
            "reason": row.get("selection_note") or row.get("reason"),
            "brand_score": row.get("brand_score"),
            "url": row.get("url"),
        }
        for row in selection_audit
        if row.get("selected_for_phantom")
    ]

    if test_mode and targets:
        plans = build_company_search_plans(
            campaign,
            targets,
            max_companies=len(targets),
            queries_per_company=phantom_max_roles_per_company(),
        )
        search_strategy = "per_company_test"
    elif test_mode:
        plan = build_test_mode_search_plan(phantom_test_query())
        plans = [plan]
        search_strategy = "test_mode_fallback"
    else:
        plans = build_company_search_plans(
            campaign,
            targets,
            max_companies=phantom_max_companies(),
            queries_per_company=phantom_max_roles_per_company(),
        )
        search_strategy = "per_company"

    location = (campaign.target_country or "").strip()
    if not location and isinstance(phantom_queue, dict):
        location = (phantom_queue.get("location") or "").strip()

    company_search_debug = [p.to_debug_dict() for p in plans]
    linkedin_urls = [p.linkedin_url for p in plans]
    keywords_list = [p.linkedin_keywords for p in plans]

    if plans:
        first = plans[0]
        linkedin_query_exact = first.linkedin_keywords
        linkedin_search_url = first.linkedin_url
        site_query = first.linkedin_keywords
    else:
        linkedin_query_exact = ""
        linkedin_search_url = ""
        site_query = ""

    # Solo metadata Nexus — el launch real usa linkedInSearchUrl por búsqueda en phantombuster_people.
    argument: dict[str, Any] = {
        "nexus_searchStrategy": search_strategy,
        "nexus_companySearchPlans": company_search_debug,
        "nexus_phantom_test_mode": test_mode,
        "nexus_sample_linkedInSearchUrl": linkedin_search_url,
        "nexus_sample_keywords": linkedin_query_exact,
    }
    if plans:
        argument["nexus_planned_queries"] = keywords_list
        argument["nexus_planned_urls"] = linkedin_urls
    if location:
        argument["nexus_location"] = location

    meta = {
        "search_strategy": search_strategy,
        "phantom_test_mode": test_mode,
        "skip_company_match_filter": phantom_skip_company_match_filter(),
        "linkedin_query_exact": linkedin_query_exact,
        "linkedin_query_note": (
            f"Modo test: query fija «{linkedin_query_exact}» (1 launch, ≤30s)."
            if test_mode
            else (
                f"{len(plans)} empresa(s); hasta {phantom_max_roles_per_company()} rol(es)/empresa "
                f'(«"Empresa" Founder» → CEO → Co-Founder…).'
                if plans
                else "Sin empresas ICP reales — prepará Web Search primero."
            )
        ),
        "company_searches": company_search_debug,
        "target_companies": [t.to_dict() for t in targets],
        "linkedin_search_url_built": linkedin_search_url,
        "location": location or None,
        "min_company_filter_score": MIN_COMPANY_FILTER_SCORE,
        "min_lead_display_score": get_min_lead_display_score(),
        "global_search_disabled": True,
        "roles_fallback_order": phantom_role_fallback_order(),
        "max_roles_per_company": phantom_max_roles_per_company(),
        "phantom_target_selection": selection_audit,
        "phantom_companies_selected": selected_debug,
    }

    return PhantomSearchBundle(
        linkedin_query_exact=linkedin_query_exact,
        linkedin_search_url=linkedin_search_url,
        site_query=site_query,
        argument_fields=argument,
        meta=meta,
        company_searches=company_search_debug,
        target_companies=targets,
    )
