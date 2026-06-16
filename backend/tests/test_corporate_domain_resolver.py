"""Tests resolución dominio corporativo."""

from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.services.lead_sourcing.contact_identity import filter_pipeline_people, is_pipeline_contact
from app.services.lead_sourcing.corporate_domain_resolver import (
    _build_domain_search_queries,
    _domain_slug_matches_company,
    _guess_domain_candidates,
    _is_reject_resolution_host,
    apply_corporate_domain_resolution,
    compute_domain_resolution_metrics,
    resolve_corporate_domain_for_company,
    CorporateDomainResolution,
)


def test_reject_crunchbase_host():
    assert _is_reject_resolution_host("crunchbase.com")
    assert not _is_reject_resolution_host("aidetic.com")


def test_slug_matches():
    assert _domain_slug_matches_company("aidetic.com", "Aidetic")
    assert _domain_slug_matches_company("saasstartupkit.com", "GO SaaS Startup Kit")


def test_guess_domains_from_name():
    guesses = _guess_domain_candidates("GO SaaS Startup Kit")
    assert "saasstartupkit.com" in guesses


def test_multi_search_queries():
    qs = _build_domain_search_queries("Cube Careers")
    assert len(qs) >= 3
    assert "crunchbase" in qs[0]


def test_resolution_metrics():
    companies = [
        CompanyCandidateRead(
            external_id="1",
            name="A",
            result_kind="company",
            company_domain="a.com",
            icp_relevance_score=80,
        ),
        CompanyCandidateRead(
            external_id="2",
            name="B",
            result_kind="company",
            icp_relevance_score=75,
        ),
    ]
    m = compute_domain_resolution_metrics(companies, fit_threshold=70)
    assert m["companies_found"] == 2
    assert m["domains_resolved"] == 1
    assert m["domain_resolution_rate_pct"] == 50


def test_own_website_from_company():
    c = CompanyCandidateRead(
        external_id="x",
        name="Aidetic",
        website_url="https://www.aidetic.com",
    )
    res = resolve_corporate_domain_for_company(
        c, try_web_search=False, try_prospeo=False
    )
    assert res.resolved
    assert res.domain == "aidetic.com"
    assert res.source == "own_website"


def test_filter_rejects_crunchbase_email():
    bad = LeadCandidateRead(
        external_id="b",
        name="Holly Barone",
        company_name="Cube Careers",
        email="holly@crunchbase.com",
        company_domain="cube.dev",
        role="CEO",
    )
    assert not is_pipeline_contact(bad)
    assert filter_pipeline_people([bad]) == []


def test_apply_moves_directory_url():
    c = CompanyCandidateRead(
        external_id="x",
        name="Aidetic",
        website_url="https://www.crunchbase.com/organization/aidetic",
    )
    updated = apply_corporate_domain_resolution(
        c,
        CorporateDomainResolution("aidetic.com", "https://aidetic.com", "web_search"),
    )
    assert updated.company_domain == "aidetic.com"
    assert "crunchbase" in (updated.source_directory_url or "")
