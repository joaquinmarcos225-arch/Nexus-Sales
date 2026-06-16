"""Tests validación contactos Prospeo vs empresa objetivo."""

from app.services.lead_sourcing.prospeo_contact_validation import (
    company_names_match,
    domains_align,
    is_directory_host,
    is_forbidden_email,
    resolve_target_company_domain,
    validate_prospeo_contact,
)


def test_directory_hosts_blocked():
    assert is_directory_host("www.crunchbase.com")
    assert is_directory_host("wellfound.com")
    assert not is_directory_host("aidetic.com")


def test_domains_align_subdomain():
    assert domains_align("aidetic.com", "mail.aidetic.com")
    assert not domains_align("aidetic.com", "crunchbase.com")


def test_company_names_match_variants():
    assert company_names_match("Aidetic", "Aidetic SAS")
    assert not company_names_match("Aidetic", "Crunchbase")


def test_resolve_skips_directory_website():
    dom = resolve_target_company_domain(
        website_url="https://www.crunchbase.com/organization/aidetic",
        firmo={"website": "https://www.aidetic.com"},
    )
    assert dom == "aidetic.com"


def test_forbidden_email_domains():
    assert is_forbidden_email("holly@crunchbase.com")
    assert is_forbidden_email("eric@wellfound.com")
    assert not is_forbidden_email("jane@cube.dev")


def test_reject_crunchbase_email():
    v = validate_prospeo_contact(
        target_company_name="Aidetic",
        target_domain="aidetic.com",
        person={
            "current_job": {"company": {"name": "Crunchbase", "website": "crunchbase.com"}},
            "first_name": "Holly",
            "last_name": "Barone",
        },
        email="holly@crunchbase.com",
    )
    assert not v.ok
    assert "directorio" in v.reason.lower() or "crunchbase" in v.reason.lower()


def test_accept_matching_employer():
    v = validate_prospeo_contact(
        target_company_name="Aidetic",
        target_domain="aidetic.com",
        person={
            "current_job": {
                "title": "CEO",
                "company": {"name": "Aidetic", "website": "https://aidetic.com"},
            },
            "first_name": "Jane",
            "last_name": "Doe",
        },
        email="jane@aidetic.com",
    )
    assert v.ok


def test_reject_wrong_employer_same_person_pattern():
    v = validate_prospeo_contact(
        target_company_name="Elioplus",
        target_domain="elioplus.com",
        person={
            "current_job": {
                "company": {"name": "Wellfound", "website": "https://wellfound.com"},
            },
            "first_name": "Eric",
            "last_name": "Ziegler",
        },
        email="eric@wellfound.com",
    )
    assert not v.ok
