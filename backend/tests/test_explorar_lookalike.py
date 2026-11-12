"""Explorar: empresas parecidas a partir del link de LinkedIn de la empresa."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.services import explorar as svc
from app.services.explorar_lookalike import (
    classify_linkedin_url,
    lookalike_filter,
    parse_lookalike_inputs,
    resolve_lookalike_seeds,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_classify_company_url_canonical():
    kind, url = classify_linkedin_url("https://ar.linkedin.com/company/hair-recovery/about?trk=x")
    assert kind == "company"
    assert url == "https://www.linkedin.com/company/hair-recovery"


def test_classify_rejects_person_and_name():
    assert classify_linkedin_url("https://www.linkedin.com/in/joaquin-fernandez")[0] == "person"
    assert classify_linkedin_url("Hair Recovery")[0] == "invalid"
    assert classify_linkedin_url("https://hairrecovery.com")[0] == "invalid"


def test_parse_lookalike_inputs_splits_and_caps():
    got = parse_lookalike_inputs(
        [
            "https://www.linkedin.com/company/a, https://www.linkedin.com/company/b\nhttps://www.linkedin.com/company/c",
            "https://www.linkedin.com/company/d",
        ]
    )
    assert len(got) == 3
    assert got[0].endswith("/a")


def test_lookalike_filter_uses_oids_t2():
    f = lookalike_filter(["oid-1", "oid-1", "oid-2"])
    assert f == {
        "company_oids": ["oid-1", "oid-2"],
        "minimum_tier": "T2",
        "match_all": False,
        "same_language": False,
    }
    assert lookalike_filter(["", None]) is None


def test_build_filters_lookalike_is_targeting():
    f = svc.build_filters(lookalike_company_oids=["abc123"])
    assert f["company_lookalike"]["company_oids"] == ["abc123"]
    assert svc.has_targeting(f)


def test_resolve_uses_enrich_and_cache():
    db = _session()
    calls = []

    def enrich(url: str):
        calls.append(url)
        return {"company_id": "co-hr", "name": "Hair Recovery", "domain": "hairrecovery.com"}

    first = resolve_lookalike_seeds(
        db, ["https://www.linkedin.com/company/hair-recovery/"], enrich_fn=enrich
    )
    second = resolve_lookalike_seeds(
        db, ["linkedin.com/company/hair-recovery"], enrich_fn=enrich
    )
    assert [s["company_id"] for s in first.seeds] == ["co-hr"]
    assert first.seeds[0]["name"] == "Hair Recovery"
    assert [s["company_id"] for s in second.seeds] == ["co-hr"]
    assert calls == ["https://www.linkedin.com/company/hair-recovery"]


def test_resolve_marks_person_and_unresolved():
    db = _session()

    def enrich(_url: str):
        return {}

    out = resolve_lookalike_seeds(
        db,
        [
            "https://www.linkedin.com/in/alguien",
            "https://www.linkedin.com/company/no-existe",
        ],
        enrich_fn=enrich,
    )
    assert out.person_urls
    assert out.unresolved
    assert out.seeds == []
