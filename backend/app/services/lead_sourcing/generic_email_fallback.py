"""Fallback: emails genéricos por dominio cuando Prospeo devuelve 0 personas."""

from __future__ import annotations

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.services.lead_sourcing.prospeo_contact_validation import is_directory_host
from app.services.lead_sourcing.prospeo_lead_fit import score_prospeo_contact_fit

GENERIC_LOCAL_PARTS: tuple[str, ...] = (
    "hello",
    "sales",
    "contact",
)

GENERIC_ROLE_LABELS: dict[str, str] = {
    "sales": "Ventas (genérico)",
    "hello": "Hola (genérico)",
    "contact": "Contacto (genérico)",
}


def build_generic_email_leads(
    *,
    company: CompanyCandidateRead,
    domain: str,
    campaign: Campaign,
    fit_score: int,
    fit_threshold: int,
) -> list[LeadCandidateRead]:
    """Crea un lead por patrón de email genérico (no persona real)."""
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain or is_directory_host(domain):
        return []

    key = (company.canonical_key or company.external_id or company.name or "").strip().lower()
    leads: list[LeadCandidateRead] = []
    for idx, local in enumerate(GENERIC_LOCAL_PARTS):
        email = f"{local}@{domain}"
        compat, breakdown = score_prospeo_contact_fit(
            email=email,
            company_domain=domain,
            company_icp_score=fit_score,
            role=GENERIC_ROLE_LABELS.get(local, "Email genérico"),
            fit_threshold=fit_threshold,
            is_generic=True,
            icp_target_role=campaign.target_role,
            icp_target_industry=campaign.target_industry,
            icp_target_country=campaign.target_country,
            icp_target_company_size=campaign.target_company_size,
            prospect_industry=company.industry,
            prospect_country=company.country,
            company_size=company.company_size,
        )
        leads.append(
            LeadCandidateRead(
                external_id=f"generic-{campaign.id}-{key[:16]}-{local}",
                provider="pattern_fallback",
                name=f"{local}@{domain}",
                company_name=(company.name or "Empresa")[:255],
                role=GENERIC_ROLE_LABELS.get(local, "Email genérico"),
                industry=company.industry,
                country=company.country,
                email=email,
                company_website=company.website_url or f"https://{domain}",
                company_domain=domain,
                linked_company_key=key,
                compatibility_score=compat,
                fit_tier="good" if compat >= fit_threshold else "low_fit",
                score_breakdown=f"Email genérico · {breakdown}",
                has_email=True,
                enrichment_source="generic_pattern",
                enrichment_confidence=25,
                contact_kind="generic_email",
            )
        )
    return leads
