"""Etapa 4C: clasificador central de industria/tamaño en Explorar."""

from __future__ import annotations

from app.services import explorar as svc
from app.services.lead_sourcing.firmographics import assess_company_firmographics


def _filters(*, industries=None, headcounts=None, location=None):
    out: dict = {}
    if industries is not None:
        out["company_industry"] = {"include": list(industries)}
    if headcounts is not None:
        out["company_headcount_range"] = list(headcounts)
    if location is not None:
        out["person_location_search"] = {"include": [location]}
    return out


def test_exact_industry_equivalent_size():
    hit = {
        "industry": "Software Development",
        "industry_origin": "prospeo",
        "company_size": "51-200",
        "size_origin": "prospeo",
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "exact"
    assert firm.size.status == "equivalent"
    assert firm.combined == "confirmed"
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["industry_status_label"] == "coincide"
    assert annotated["size_status_label"] == "equivalente"
    assert svc.hit_matches_filters(hit, filters)


def test_equivalent_industry_exact_size():
    hit = {
        "industry": "SaaS",
        "industry_origin": "prospeo",
        "employee_count": 80,
        "size_origin": "prospeo",
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "equivalent"
    assert firm.size.status == "exact"
    assert firm.combined == "confirmed"
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["industry_status_label"] == "equivalente"
    assert annotated["size_status_label"] == "coincide"
    assert svc.hit_matches_filters(hit, filters)


def test_exact_industry_contradictory_size_is_not_compatible():
    hit = {
        "industry": "Software Development",
        "industry_origin": "prospeo",
        "employee_count": 5000,
        "size_origin": "prospeo",
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "exact"
    assert firm.size.status == "contradictory"
    assert firm.combined == "contradictory"
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["firmographic_status_label"] == "no compatible"
    assert annotated["size_status_label"] == "no coincide"
    assert not svc.hit_matches_filters(hit, filters)


def test_exact_size_contradictory_industry_is_not_compatible():
    hit = {
        "industry": "Construction",
        "industry_origin": "prospeo",
        "employee_count": 80,
        "size_origin": "prospeo",
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "contradictory"
    assert firm.size.status == "exact"
    assert firm.combined == "contradictory"
    assert not svc.hit_matches_filters(hit, filters)


def test_incomplete_is_kept_and_not_confirmed():
    hit = {
        "industry": None,
        "industry_origin": "prospeo",
        "employee_count": 80,
        "size_origin": "prospeo",
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "incomplete"
    assert firm.size.status == "exact"
    assert firm.combined == "incomplete"
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["industry_status_label"] == "información incompleta"
    assert annotated["firmographic_status"] != "confirmed"
    assert svc.hit_matches_filters(hit, filters)


def test_industry_origin_icp_is_not_evidence():
    hit = {
        "industry": "Software Development",
        "industry_origin": "icp",
        "employee_count": 80,
        "size_origin": "prospeo",
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "incomplete"
    assert firm.confirmed is False
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["industry_status"] == "incomplete"
    assert svc.hit_matches_filters(hit, filters)


def test_historical_without_origin_is_not_auto_confirmed():
    hit = {
        "industry": "Software Development",
        "industry_origin": None,
        "employee_count": 80,
        "size_origin": None,
    }
    filters = _filters(industries=["SaaS"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "review"
    assert firm.size.status == "review"
    assert firm.confirmed is False
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["industry_origin"] is None
    assert annotated["size_origin"] is None
    assert annotated["industry_status_label"] == "revisar"
    assert svc.hit_matches_filters(hit, filters)


def test_1000_plus_accepts_5000():
    hit = {"employee_count": 5000, "size_origin": "prospeo"}
    filters = _filters(headcounts=["1000+"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.size.status == "exact"
    assert firm.combined == "confirmed"
    assert svc.hit_matches_filters(hit, filters)


def test_adjacent_ranges_are_similar_not_confirmed():
    hit = {"company_size": "21-50", "size_origin": "prospeo"}
    filters = _filters(headcounts=["11-20"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.size.status == "similar"
    assert firm.confirmed is False
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["size_status_label"] == "similar"
    assert svc.hit_matches_filters(hit, filters)


def test_unrequested_industry_or_size_does_not_penalize():
    hit = {
        "role": "CEO",
        "industry": "Oil & Gas",
        "industry_origin": "prospeo",
        "employee_count": 8,
        "size_origin": "prospeo",
    }
    only_role = {"person_job_title": {"include": ["CEO"]}}
    firm = svc.assess_explorar_firmographics(hit, only_role)
    assert firm.industry.status is None
    assert firm.size.status is None
    assert firm.combined is None
    annotated = svc.annotate_explorar_firmographics(hit, only_role)
    assert annotated["industry_status"] is None
    assert annotated["size_status"] is None
    assert annotated["industry"] == "Oil & Gas"
    assert svc.hit_matches_filters(hit, only_role)


def test_does_not_use_campaign_target_industry_as_evidence():
    hit = {
        "industry": "SaaS",
        "industry_origin": "icp",
        "campaign_target_industry": "SaaS",
        "size_origin": "prospeo",
        "employee_count": 80,
    }
    filters = _filters(industries=["Software Development"], headcounts=["51-100"])
    firm = svc.assess_explorar_firmographics(hit, filters)
    assert firm.industry.status == "incomplete"
    central = assess_company_firmographics(
        campaign_industry="Software Development",
        industry="SaaS",
        industry_origin="icp",
        campaign_company_size="51-100",
        employee_count=80,
        size_origin="prospeo",
    )
    assert firm.industry.status == central.industry.status
    assert firm.combined == central.combined


def test_classifier_and_frontend_labels_stay_aligned():
    """El frontend muestra industry_status_label / size_status_label del backend."""
    assert svc.FIRMO_STATUS_LABELS == {
        "exact": "coincide",
        "equivalent": "equivalente",
        "similar": "similar",
        "incomplete": "información incompleta",
        "review": "revisar",
        "contradictory": "no coincide",
    }
    hit = {
        "industry": "Software Development",
        "industry_origin": "prospeo",
        "employee_count": 80,
        "size_origin": "prospeo",
    }
    annotated = svc.annotate_explorar_firmographics(
        hit, _filters(industries=["SaaS"], headcounts=["51-100"])
    )
    assert annotated["industry_status_label"] == svc.FIRMO_STATUS_LABELS[annotated["industry_status"]]
    assert annotated["size_status_label"] == svc.FIRMO_STATUS_LABELS[annotated["size_status"]]
    assert annotated["firmographic_status_label"] == svc.COMBINED_STATUS_LABELS[annotated["firmographic_status"]]


def test_historical_favorites_keep_raw_fields_without_invented_origin():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.base import Base
    from app.models.company import Company
    from app.models.enums import UserRole
    from app.models.user import User

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    co = Company(name="Co", employee_count=3, plan="starter")
    db.add(co)
    db.flush()
    user = User(
        company_id=co.id,
        email="a@t.com",
        first_name="A",
        last_name="T",
        name="A T",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add(user)
    db.flush()
    svc.add_favorite(
        db,
        company_id=co.id,
        user_id=user.id,
        snapshot={
            "person_id": "old-1",
            "name": "Carla Hist",
            "industry": "Software Development",
            "company_size": "51-100",
            "industry_status": "exact",
            "firmographic_status": "confirmed",
        },
    )
    db.commit()
    items = svc.list_favorites(db, company_id=co.id, user_id=user.id, include_team=False)
    assert len(items) == 1
    assert items[0]["industry"] == "Software Development"
    assert items[0]["company_size"] == "51-100"
    assert "industry_origin" not in items[0]
    assert items[0].get("industry_status") is None


def test_search_cache_without_origin_is_treated_as_prospeo_observed():
    hit = {"industry": "Software Development", "company_size": "51-100"}
    filters = _filters(industries=["SaaS"], headcounts=["51-100"])
    annotated = svc.annotate_explorar_firmographics(hit, filters)
    assert annotated["industry_origin"] == "prospeo"
    assert annotated["size_origin"] == "prospeo"
    assert annotated["industry_status"] == "equivalent"
    assert annotated["firmographic_status"] == "confirmed"


def test_geo_and_role_filters_unchanged():
    f = {
        "person_job_title": {"include": ["CEO"], "match_mode": "CONTAINS"},
        "person_location_search": {"include": ["Argentina"]},
        "company_industry": {"include": ["Software Development"]},
        "company_headcount_range": ["11-20", "21-50"],
    }
    ok = {"role": "CEO", "country": "Argentina", "industry": "Software Development", "company_size": "11-20"}
    assert svc.hit_matches_filters(ok, f)
    assert not svc.hit_matches_filters({**ok, "country": "Uruguay"}, f)
    assert not svc.hit_matches_filters({**ok, "role": "Recruiter"}, f)
