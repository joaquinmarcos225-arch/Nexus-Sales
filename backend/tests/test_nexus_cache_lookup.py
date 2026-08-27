"""Lookup base propia antes de Prospeo."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.nexus_contact_cache import NexusContactCache, NexusContactDelivery
from app.services.nexus_contact_cache import (
    find_cached_leads_for_campaign,
    upsert_contact_from_import,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_find_cached_leads_matches_role_and_skips_delivered():
    db = _session()
    tenant_a = Company(name="Tenant A", plan="starter", employee_count=2)
    tenant_b = Company(name="Tenant B", plan="starter", employee_count=2)
    db.add_all([tenant_a, tenant_b])
    db.flush()

    upsert_contact_from_import(
        db,
        tenant_company_id=tenant_b.id,
        campaign_id=None,
        name="Ana CEO",
        role="CEO",
        email="ana@acme.com",
        linkedin_url="https://www.linkedin.com/in/ana-ceo",
        company_name="Acme",
        country="Argentina",
        source_provider="prospeo",
        source_external_id="1",
    )
    db.commit()

    campaign_a = SimpleNamespace(
        id=1,
        company_id=tenant_a.id,
        target_role="CEO",
        target_country="Argentina",
        target_industry="SaaS",
    )
    leads, diag = find_cached_leads_for_campaign(db, campaign_a, limit=10)
    assert diag["kept"] == 1
    assert len(leads) == 1
    assert leads[0].provider == "nexus_cache"
    assert leads[0].email == "ana@acme.com"

    contact = db.scalars(select(NexusContactCache)).one()
    db.add(
        NexusContactDelivery(
            contact_cache_id=contact.id,
            tenant_company_id=tenant_a.id,
        )
    )
    db.commit()
    leads2, diag2 = find_cached_leads_for_campaign(db, campaign_a, limit=10)
    assert diag2["kept"] == 0
    assert leads2 == []


def test_find_cached_rejects_bad_role():
    db = _session()
    tenant = Company(name="T", plan="starter", employee_count=1)
    other = Company(name="Other", plan="starter", employee_count=1)
    db.add_all([tenant, other])
    db.flush()

    upsert_contact_from_import(
        db,
        tenant_company_id=other.id,
        campaign_id=None,
        name="Bob Recruiter",
        role="Talent Acquisition Recruiter",
        email="bob@x.com",
        linkedin_url="https://www.linkedin.com/in/bob-rec",
        company_name="X",
        country="Argentina",
    )
    db.commit()

    campaign = SimpleNamespace(
        id=2,
        company_id=tenant.id,
        target_role="CEO",
        target_country="Argentina",
        target_industry=None,
    )
    leads, diag = find_cached_leads_for_campaign(db, campaign, limit=10)
    assert leads == []
    assert diag["skipped_role"] >= 1
