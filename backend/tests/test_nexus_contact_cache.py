"""Base propia Nexus v1: upsert al importar no rompe y dedupea por email/LI."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.nexus_contact_cache import (
    NexusCompanyCache,
    NexusContactCache,
    NexusContactDelivery,
)
from app.services.nexus_contact_cache import (
    normalize_domain,
    safe_upsert_from_prospect,
    upsert_contact_from_import,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.COM/path") == "acme.com"
    assert normalize_domain("acme.com") == "acme.com"


def test_upsert_contact_and_delivery():
    db = _session()
    co = Company(name="Tenant A", plan="starter", employee_count=3)
    db.add(co)
    db.flush()

    row = upsert_contact_from_import(
        db,
        tenant_company_id=co.id,
        campaign_id=None,
        name="Ana CEO",
        role="CEO",
        email="ana@acme.com",
        linkedin_url="https://www.linkedin.com/in/ana-ceo",
        phone="+5491112345678",
        company_name="Acme SA",
        company_website="https://www.acme.com",
        source_provider="prospeo",
        source_external_id="px1",
    )
    db.commit()
    assert row is not None
    assert row.email == "ana@acme.com"
    assert row.linkedin_slug == "ana-ceo"
    assert row.company_domain == "acme.com"

    employers = db.scalars(select(NexusCompanyCache)).all()
    assert len(employers) == 1
    assert employers[0].domain == "acme.com"

    deliveries = db.scalars(select(NexusContactDelivery)).all()
    assert len(deliveries) == 1
    assert deliveries[0].tenant_company_id == co.id

    # Second upsert same email: no duplicate contact / delivery
    upsert_contact_from_import(
        db,
        tenant_company_id=co.id,
        campaign_id=None,
        name="Ana CEO",
        email="ana@acme.com",
        linkedin_url="https://www.linkedin.com/in/ana-ceo",
        company_name="Acme SA",
        company_website="https://www.acme.com",
        source_provider="prospeo",
        source_external_id="px1",
    )
    db.commit()
    assert len(db.scalars(select(NexusContactCache)).all()) == 1
    assert len(db.scalars(select(NexusContactDelivery)).all()) == 1


def test_safe_upsert_swallows_errors():
    db = _session()
    from types import SimpleNamespace

    p = SimpleNamespace(
        id=None,
        company_id=1,
        campaign_id=1,
        name="X",
        company_name="Y",
        role=None,
        industry=None,
        country=None,
        email=None,
        linkedin_url=None,
        phone=None,
        whatsapp=None,
        company_website=None,
        source_provider=None,
        source_external_id=None,
    )
    safe_upsert_from_prospect(db, p, tenant_company_id=999)
