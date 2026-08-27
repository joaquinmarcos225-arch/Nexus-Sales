"""Paso E: caché TTL de snippets de investigación + dominio en company cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.nexus_contact_cache import NexusCompanyCache
from app.models.nexus_research_cache import NexusResearchCache
from app.services.nexus_contact_cache import (
    find_company_domain_by_name,
    remember_company_domain,
)
from app.services.nexus_research_cache import (
    KIND_OUTREACH_SNIPPETS,
    get_research_payload,
    outreach_snippets_cache_key,
    set_research_payload,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_outreach_snippets_cache_key_normalizes():
    a = outreach_snippets_cache_key(mode="B2B", company_name="  Acme SA ", country="AR")
    b = outreach_snippets_cache_key(mode="b2b", company_name="Acme SA", country="ar")
    assert a == b
    assert a and a.startswith("outreach_snippets:v1:")
    assert outreach_snippets_cache_key(mode="b2b", company_name="—", country=None) is None


def test_research_cache_hit_miss_and_ttl():
    db = _session()
    key = outreach_snippets_cache_key(mode="b2b", company_name="Acme", country="AR")
    assert key

    assert get_research_payload(db, key) is None

    set_research_payload(
        db,
        cache_key=key,
        kind=KIND_OUTREACH_SNIPPETS,
        payload=["snippet one", "snippet two"],
        ttl_hours=168,
    )
    db.commit()

    hit = get_research_payload(db, key)
    assert hit == ["snippet one", "snippet two"]

    row = db.scalars(
        select(NexusResearchCache).where(NexusResearchCache.cache_key == key)
    ).first()
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()

    assert get_research_payload(db, key) is None


def test_company_domain_remember_and_lookup():
    db = _session()
    remember_company_domain(
        db,
        name="Acme SA",
        domain="acme.com",
        website_url="https://www.acme.com",
        source_provider="web_search",
    )
    db.commit()

    hit = find_company_domain_by_name(db, "Acme SA")
    assert hit == ("acme.com", "https://www.acme.com")

    # case-insensitive name
    hit2 = find_company_domain_by_name(db, "acme sa")
    assert hit2 is not None and hit2[0] == "acme.com"

    employers = db.scalars(select(NexusCompanyCache)).all()
    assert len(employers) == 1
