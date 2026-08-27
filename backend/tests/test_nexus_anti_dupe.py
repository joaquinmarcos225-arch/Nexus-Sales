"""Paso C: anti-dupe — mismo tenant no vuelve a recibir el contacto."""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.nexus_contact_cache import NexusContactCache
from app.services.nexus_contact_cache import (
    contact_delivered_to_tenant,
    tenant_delivered_exclusion_sets,
    upsert_contact_from_import,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_tenant_delivered_exclusion_and_check():
    db = _session()
    a = Company(name="A", plan="starter", employee_count=1)
    b = Company(name="B", plan="starter", employee_count=1)
    db.add_all([a, b])
    db.flush()

    upsert_contact_from_import(
        db,
        tenant_company_id=a.id,
        campaign_id=None,
        name="Ana",
        role="CEO",
        email="ana@acme.com",
        linkedin_url="https://www.linkedin.com/in/ana-ceo",
        phone="+5491112345678",
        company_name="Acme",
    )
    db.commit()

    em, li, ph = tenant_delivered_exclusion_sets(db, a.id)
    assert "ana@acme.com" in em
    assert "ana-ceo" in li
    assert ph  # dígitos normalizados

    assert contact_delivered_to_tenant(
        db, a.id, email="ana@acme.com", linkedin_url=None, phone=None
    )
    assert contact_delivered_to_tenant(
        db,
        a.id,
        email=None,
        linkedin_url="https://www.linkedin.com/in/ana-ceo",
        phone=None,
    )
    # Otro tenant: aún no entregado
    assert not contact_delivered_to_tenant(db, b.id, email="ana@acme.com")
    em_b, _, _ = tenant_delivered_exclusion_sets(db, b.id)
    assert "ana@acme.com" not in em_b

    assert db.scalars(select(NexusContactCache)).one().email == "ana@acme.com"
