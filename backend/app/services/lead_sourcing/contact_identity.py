"""Identidad de contacto real vs placeholder vs email genérico."""

from __future__ import annotations

from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.lead_sourcing.prospeo_contact_validation import (
    domains_align,
    email_domain,
    is_directory_host,
    is_forbidden_email,
)

ICP_TARGET_ROLES: tuple[str, ...] = (
    "Founder",
    "Co-Founder",
    "CEO",
    "Head of Sales",
    "VP Sales",
    "Vice President Sales",
    "Chief Revenue Officer",
    "Revenue",
    "Business Development",
)


def is_generic_email_contact(lead: LeadCandidateRead) -> bool:
    return (lead.contact_kind or "") == "generic_email"


def is_company_placeholder(lead: LeadCandidateRead) -> bool:
    if (lead.contact_kind or "") == "company_placeholder":
        return True
    if (lead.external_id or "").startswith("icp-account-"):
        return True
    name = (lead.name or "").strip().lower()
    company = (lead.company_name or "").strip().lower()
    if name and company and name == company:
        return True
    if name in ("contacto", "empresa"):
        return True
    return False


def is_real_person_lead(lead: LeadCandidateRead) -> bool:
    if is_generic_email_contact(lead):
        return False
    if is_forbidden_email(lead.email):
        return False
    if is_company_placeholder(lead):
        return False
    name = (lead.name or "").strip()
    if len(name) < 2:
        return False
    company = (lead.company_name or "").strip()
    if name.lower() == company.lower():
        return False
    has_email = bool(
        (lead.email or "").strip()
        and "@" in (lead.email or "")
        and not is_forbidden_email(lead.email)
    )
    has_linkedin = bool((lead.linkedin_url or "").strip())
    has_role = bool((lead.role or "").strip())
    return has_role or has_linkedin or has_email


def is_pipeline_contact(lead: LeadCandidateRead) -> bool:
    """Contacto persona real visible en tabla principal."""
    if not is_real_person_lead(lead):
        return False
    em_dom = email_domain(lead.email)
    corp = (lead.company_domain or "").strip().lower()
    if em_dom:
        if is_forbidden_email(lead.email) or is_directory_host(em_dom):
            return False
        if corp and not domains_align(corp, em_dom):
            return False
    return True


def is_pipeline_generic_contact(lead: LeadCandidateRead) -> bool:
    """Email genérico @dominio (fallback Prospeo)."""
    if not is_generic_email_contact(lead):
        return False
    if is_forbidden_email(lead.email):
        return False
    em = (lead.email or "").strip()
    if not em or "@" not in em:
        return False
    corp = (lead.company_domain or "").strip().lower()
    em_dom = email_domain(em)
    if corp and em_dom and not domains_align(corp, em_dom):
        return False
    return True


def filter_pipeline_people(people: list[LeadCandidateRead]) -> list[LeadCandidateRead]:
    return [p for p in people if is_pipeline_contact(p)]


def filter_generic_contacts(people: list[LeadCandidateRead]) -> list[LeadCandidateRead]:
    return [p for p in people if is_pipeline_generic_contact(p)]


def is_outreach_ready_person(lead: LeadCandidateRead, *, fit_threshold: int = 70) -> bool:
    from app.services.lead_sourcing.prospecting_lead import is_prospecting_outreach_ready

    return is_prospecting_outreach_ready(lead, fit_threshold=fit_threshold)


def is_outreach_ready_generic(lead: LeadCandidateRead, *, fit_threshold: int = 70) -> bool:
    """Genéricos no califican para Outreach Nexus (requiere LinkedIn personal)."""
    del fit_threshold
    return False


def is_outreach_ready_any(lead: LeadCandidateRead, *, fit_threshold: int = 70) -> bool:
    return is_outreach_ready_person(lead, fit_threshold=fit_threshold)
