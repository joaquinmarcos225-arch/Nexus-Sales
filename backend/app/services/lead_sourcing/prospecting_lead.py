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
    Listo para Outreach: persona real + email corporativo + LinkedIn personal.
    No exige score ICP ni teléfono/WhatsApp (fit_threshold conservado por compat API).
    """
    del fit_threshold
    return len(prospecting_missing_fields(lead)) == 0
