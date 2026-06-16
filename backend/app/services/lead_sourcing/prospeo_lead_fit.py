"""Scoring ICP para contactos Prospeo (persona real o genérico)."""

from __future__ import annotations

from app.services.lead_sourcing.icp_score_audit import compute_icp_score_breakdown
from app.services.lead_sourcing.prospeo_contact_validation import (
    domains_align,
    email_domain,
    is_directory_host,
    is_forbidden_email,
)


def email_matches_company_domain(
    email: str | None,
    company_domain: str | None,
) -> bool:
    dom = (company_domain or "").strip().lower().removeprefix("www.")
    em_dom = email_domain(email)
    if not dom or not em_dom or is_directory_host(dom):
        return False
    if is_forbidden_email(email) or is_directory_host(em_dom):
        return False
    return domains_align(dom, em_dom)


def score_prospeo_contact_fit(
    *,
    email: str | None,
    company_domain: str | None,
    company_icp_score: int,
    role: str | None,
    fit_threshold: int,
    is_generic: bool = False,
    icp_target_role: str | None = None,
    icp_target_industry: str | None = None,
    icp_target_country: str | None = None,
    icp_target_company_size: str | None = None,
    prospect_industry: str | None = None,
    prospect_country: str | None = None,
    company_size: str | None = None,
    linkedin_url: str | None = None,
) -> tuple[int, str]:
    """Devuelve (compatibility_score, score_breakdown) usando auditoría ICP unificada."""
    del fit_threshold
    breakdown = compute_icp_score_breakdown(
        campaign_industry=icp_target_industry,
        campaign_country=icp_target_country,
        campaign_role=icp_target_role,
        campaign_company_size=icp_target_company_size,
        prospect_industry=prospect_industry,
        prospect_country=prospect_country,
        prospect_role=role,
        company_size=company_size,
        email=email,
        linkedin_url=linkedin_url,
        company_domain=company_domain,
        company_icp_relevance_score=company_icp_score,
    )
    if is_generic and email_matches_company_domain(email, company_domain):
        score = max(breakdown.final_score, 72)
        text = "email genérico en dominio · " + " · ".join(breakdown.notes[:3])
        return min(100, score), f"{text} · {score}%"
    parts = [
        f"industria {breakdown.industry_score}%",
        f"cargo {breakdown.role_score}%",
        f"país {breakdown.country_score}%",
        f"tamaño {breakdown.company_size_score}%",
        f"señales {breakdown.additional_signals_score}%",
        f"final {breakdown.final_score}%",
    ]
    if breakdown.role_mismatch_cap_applied:
        parts.append("tope mismatch rol")
    return breakdown.final_score, " · ".join(parts)
