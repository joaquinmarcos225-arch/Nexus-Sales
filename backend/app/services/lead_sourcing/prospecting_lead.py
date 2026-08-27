"""Criterios de lead válido Nexus (prospección)."""

from __future__ import annotations

from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.lead_sourcing.contact_identity import is_real_person_lead
from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url
from app.services.lead_sourcing.prospeo_contact_validation import (
    domains_align,
    email_domain,
    is_consumer_email_domain,
    is_directory_host,
    is_forbidden_email,
)


def has_valid_corporate_email(lead: LeadCandidateRead) -> bool:
    """Email corporativo alineado con dominio de empresa (incluye subdominios)."""
    em = (lead.email or "").strip()
    if not em or "@" not in em:
        return False
    if is_forbidden_email(em):
        return False
    em_dom = email_domain(em)
    if not em_dom or is_directory_host(em_dom) or is_consumer_email_domain(em_dom):
        return False
    corp = (lead.company_domain or "").strip().lower().removeprefix("www.")
    if corp:
        return domains_align(corp, em_dom)
    return True


def prospecting_missing_fields(lead: LeadCandidateRead) -> list[str]:
    """Campos que bloquean Outreach Ready (teléfono/WhatsApp no bloquean)."""
    missing: list[str] = []
    if not is_real_person_lead(lead):
        missing.append("persona real")
    if not has_valid_corporate_email(lead):
        missing.append("email corporativo")
    if not is_personal_linkedin_url(lead.linkedin_url):
        missing.append("LinkedIn personal")
    return missing


def is_prospecting_outreach_ready(lead: LeadCandidateRead, *, fit_threshold: int = 70) -> bool:
    """
    Listo para Outreach: persona real + email corporativo + LinkedIn personal
    + score ICP ≥ umbral + sin ruido recruiter/careers.
    """
    if len(prospecting_missing_fields(lead)) > 0:
        return False
    from app.services.lead_sourcing.icp_import_gate import is_noisy_prospect

    if is_noisy_prospect(
        role=lead.role,
        company_name=lead.company_name,
        company_domain=getattr(lead, "company_domain", None),
        linkedin_url=lead.linkedin_url,
    ):
        return False

    score = int(lead.compatibility_score or 0)
    if score < int(fit_threshold):
        return False
    return True


def is_prospecting_outreach_ready_for_campaign(
    lead: LeadCandidateRead,
    campaign,
    *,
    fit_threshold: int = 70,
) -> bool:
    """Outreach-ready + rol / ICP de la campaña."""
    if not is_prospecting_outreach_ready(lead, fit_threshold=fit_threshold):
        return False
    from app.services.lead_sourcing.icp_import_gate import lead_passes_icp_import_gate

    return lead_passes_icp_import_gate(lead, campaign, fit_threshold=fit_threshold)


def is_prospecting_importable_for_campaign(
    lead: LeadCandidateRead,
    campaign,
    *,
    fit_threshold: int = 70,
) -> bool:
    """
    Importable para llenar cupo (perfecto o casi):
    persona real, email corporativo O LinkedIn, rol/identidad ICP (admite near), score >= 55.
    """
    from app.services.lead_sourcing.icp_import_gate import (
        is_noisy_prospect,
        lead_passes_icp_import_gate,
    )

    if not is_real_person_lead(lead):
        return False
    if is_noisy_prospect(
        role=lead.role,
        company_name=lead.company_name,
        company_domain=getattr(lead, "company_domain", None),
        linkedin_url=lead.linkedin_url,
    ):
        return False
    has_email = has_valid_corporate_email(lead)
    has_li = is_personal_linkedin_url(lead.linkedin_url)
    if not (has_email or has_li):
        return False
    floor = 55 if int(fit_threshold) >= 55 else int(fit_threshold)
    if int(lead.compatibility_score or 0) < floor:
        return False
    return lead_passes_icp_import_gate(lead, campaign, fit_threshold=fit_threshold)
