"""Criterios de lead válido Nexus (prospección)."""

from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.lead_sourcing.linkedin_identity import (
    is_personal_linkedin_url,
    normalize_linkedin_url,
)
from app.services.lead_sourcing.prospecting_lead import (
    is_prospecting_outreach_ready,
    prospecting_missing_fields,
)


def _person(**kwargs) -> LeadCandidateRead:
    base = dict(
        external_id="p1",
        provider="prospeo",
        name="Jane Doe",
        company_name="Acme Inc",
        role="CEO",
        email="jane@acme.com",
        company_domain="acme.com",
        linkedin_url="https://www.linkedin.com/in/janedoe",
        compatibility_score=80,
        contact_kind="person",
    )
    base.update(kwargs)
    return LeadCandidateRead(**base)


def test_normalize_linkedin_personal():
    assert (
        normalize_linkedin_url("linkedin.com/in/janedoe")
        == "https://linkedin.com/in/janedoe"
    )
    assert normalize_linkedin_url("https://www.linkedin.com/company/acme") is None


def test_is_personal_linkedin_rejects_company():
    assert is_personal_linkedin_url("https://www.linkedin.com/in/janedoe") is True
    assert is_personal_linkedin_url("https://www.linkedin.com/company/acme") is False


def test_outreach_ready_requires_email_and_linkedin():
    lead = _person()
    assert is_prospecting_outreach_ready(lead, fit_threshold=70) is True
    assert prospecting_missing_fields(lead) == []

    low_score = _person(compatibility_score=0, fit_tier="low_fit")
    assert is_prospecting_outreach_ready(low_score, fit_threshold=70) is True

    no_li = _person(linkedin_url=None, has_linkedin=False)
    assert "LinkedIn personal" in prospecting_missing_fields(no_li)
    assert is_prospecting_outreach_ready(no_li, fit_threshold=70) is False

    wrong_domain = _person(email="jane@gmail.com")
    assert "email corporativo" in prospecting_missing_fields(wrong_domain)

    subdomain = _person(email="jane@mail.acme.com", company_domain="acme.com")
    assert is_prospecting_outreach_ready(subdomain, fit_threshold=70) is True
