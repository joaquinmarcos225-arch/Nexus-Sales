"""Company-first: cache de empresas y contactos antes de Brave/Prospeo."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.nexus_contact_cache import NexusContactCache
from app.services.nexus_contact_cache import (
    find_cached_companies_for_campaign,
    find_cached_contacts_for_company,
    upsert_contact_from_import,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_find_cached_companies_groups_by_domain():
    db = _session()
    tenant = Company(name="Tenant", plan="starter", employee_count=1)
    other = Company(name="Other", plan="starter", employee_count=1)
    db.add_all([tenant, other])
    db.flush()

    upsert_contact_from_import(
        db,
        tenant_company_id=other.id,
        campaign_id=None,
        name="Ana CEO",
        role="CEO",
        email="ana@acme.com",
        company_name="Acme SA",
        company_website="https://www.acme.com",
        country="Argentina",
        source_provider="prospeo",
        source_external_id="1",
    )
    upsert_contact_from_import(
        db,
        tenant_company_id=other.id,
        campaign_id=None,
        name="Bob CTO",
        role="CTO",
        email="bob@acme.com",
        company_name="Acme SA",
        company_website="https://www.acme.com",
        country="Argentina",
        source_provider="prospeo",
        source_external_id="2",
    )
    db.commit()

    campaign = SimpleNamespace(
        id=1,
        company_id=tenant.id,
        target_role="CEO",
        target_country="Argentina",
        target_industry="SaaS",
    )
    companies, diag = find_cached_companies_for_campaign(db, campaign, limit=5)
    assert diag["kept"] == 1
    assert len(companies) == 1
    assert companies[0].provider == "nexus_cache"
    assert companies[0].company_domain == "acme.com"
    assert companies[0].domain_trust == "verified"


def test_find_cached_contacts_for_company_skips_prospeo_path():
    db = _session()
    tenant = Company(name="Tenant", plan="starter", employee_count=1)
    db.add(tenant)
    db.flush()

    upsert_contact_from_import(
        db,
        tenant_company_id=999,
        campaign_id=None,
        name="Ana CEO",
        role="CEO",
        email="ana@acme.com",
        linkedin_url="https://www.linkedin.com/in/ana-ceo",
        company_name="Acme SA",
        company_website="https://www.acme.com",
        country="Argentina",
        source_provider="prospeo",
        source_external_id="1",
    )
    db.commit()

    campaign = SimpleNamespace(
        id=1,
        company_id=tenant.id,
        target_role="CEO",
        target_country="Argentina",
        target_industry="SaaS",
    )
    leads, diag = find_cached_contacts_for_company(
        db,
        campaign,
        company_domain="acme.com",
        company_name="Acme SA",
        limit=3,
    )
    assert diag["kept"] == 1
    assert leads[0].email == "ana@acme.com"

    # Wrong company → empty
    leads2, diag2 = find_cached_contacts_for_company(
        db,
        campaign,
        company_domain="other.com",
        company_name="Other Co",
        limit=3,
    )
    assert diag2["kept"] == 0
    assert leads2 == []


def test_mvp_enrichment_uses_cache_before_prospeo():
    from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
    from app.services.lead_sourcing.mvp_enrichment import run_mvp_company_enrichment

    db = _session()
    tenant = Company(name="Tenant", plan="starter", employee_count=1)
    db.add(tenant)
    db.flush()
    upsert_contact_from_import(
        db,
        tenant_company_id=999,
        campaign_id=None,
        name="Ana CEO",
        role="CEO",
        email="ana@acme.com",
        company_name="Acme SA",
        company_website="https://www.acme.com",
        country="Argentina",
        source_provider="prospeo",
        source_external_id="x1",
    )
    db.commit()

    campaign = MagicMock()
    campaign.id = 1
    campaign.company_id = tenant.id
    campaign.target_role = "CEO"
    campaign.target_country = "Argentina"
    campaign.target_industry = "SaaS"
    campaign.target_company_size = None

    companies = [
        CompanyCandidateRead(
            external_id="co1",
            provider="web_search",
            name="Acme SA",
            company_domain="acme.com",
            website_url="https://acme.com",
            domain_trust="verified",
            icp_relevance_score=80,
            result_kind="company",
        )
    ]

    mock_prospeo = MagicMock()
    mock_prospeo.is_configured.return_value = True

    with patch(
        "app.services.lead_sourcing.mvp_enrichment.get_contact_enrichment_provider",
        return_value=mock_prospeo,
    ):
        with patch(
            "app.services.lead_sourcing.prospeo_api_health.fetch_prospeo_account_health"
        ) as mock_health:
            from app.services.lead_sourcing.prospeo_api_health import ProspeoHealth

            mock_health.return_value = ProspeoHealth(configured=True)
            with patch(
                "app.services.lead_sourcing.mvp_enrichment.search_people_at_company_with_diagnostic"
            ) as mock_search:
                _, people, stats = run_mvp_company_enrichment(
                    companies=companies,
                    people=[],
                    campaign=campaign,
                    fit_threshold=70,
                    db=db,
                    skip_company_firmographics=True,
                )
                mock_search.assert_not_called()

    assert int(stats.get("nexus_cache_contacts") or 0) >= 1
    assert any(p.email == "ana@acme.com" for p in people)
