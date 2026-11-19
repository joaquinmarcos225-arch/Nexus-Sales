"""Filtro HQ: company_location_search + hard-reject local (sin Prospeo live)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.explorar import build_filters, company_hq_hard_ok, hit_matches_core_filters
from app.services.lead_sourcing.location_check import (
    company_hq_matches,
    company_hq_search_filter,
)
from app.services.lead_sourcing.role_person_search import build_role_first_filter_variants


def test_company_hq_search_filter_empty_when_blank():
    assert company_hq_search_filter(None) == {}
    assert company_hq_search_filter("  ") == {}
    assert company_hq_search_filter("cualquiera") == {}


def test_company_hq_search_filter_builds_prospeo_key(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Argentina"],
    )
    assert company_hq_search_filter("Argentina") == {
        "company_location_search": {"include": ["Argentina"]}
    }


def test_company_hq_matches_incomplete_passes():
    assert company_hq_matches(company_country=None, campaign_hq="Argentina") is True
    assert company_hq_matches(company_country="", campaign_hq="Argentina") is True


def test_company_hq_matches_contradictory_rejects(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Argentina"],
    )
    assert company_hq_matches(company_country="United States", campaign_hq="Argentina") is False
    assert company_hq_matches(company_country="Argentina", campaign_hq="Argentina") is True


def test_explorar_build_filters_includes_hq(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Chile"],
    )
    monkeypatch.setattr(
        "app.services.lead_sourcing.b2c_person_search.resolve_canonical_locations",
        lambda *a, **k: ["Argentina"],
    )
    f = build_filters(titles=["CEO"], location="Argentina", company_hq="Chile")
    assert f["person_location_search"]["include"] == ["Argentina"]
    assert f["company_location_search"]["include"] == ["Chile"]


def test_explorar_hard_ok_rejects_wrong_company_country(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Argentina"],
    )
    filters = {
        "person_job_title": {"include": ["CEO"], "match_mode": "CONTAINS"},
        "company_location_search": {"include": ["Argentina"]},
    }
    ok = {"role": "CEO", "country": "Chile", "company_country": "Argentina"}
    bad = {"role": "CEO", "country": "Argentina", "company_country": "United States"}
    incomplete = {"role": "CEO", "country": "Chile", "company_country": ""}
    assert company_hq_hard_ok(ok, filters) is True
    assert company_hq_hard_ok(bad, filters) is False
    assert company_hq_hard_ok(incomplete, filters) is True
    assert hit_matches_core_filters(ok, filters) is True
    assert hit_matches_core_filters(bad, filters) is False


def test_role_first_variants_include_company_location_search(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.role_person_search.resolve_canonical_locations",
        lambda *a, **k: ["Argentina"],
    )
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Argentina"],
    )
    campaign = SimpleNamespace(
        target_role="CEO",
        target_country="Argentina",
        target_company_hq="Argentina",
        target_industry=None,
        target_company_size=None,
    )
    variants, _meta = build_role_first_filter_variants(campaign)
    assert variants
    assert all("company_location_search" in f for _, f in variants)
    assert all(f["company_location_search"]["include"] == ["Argentina"] for _, f in variants)


def test_b2c_variants_include_company_location_search(monkeypatch):
    from app.services.lead_sourcing.b2c_person_search import build_b2c_filter_variants

    monkeypatch.setattr(
        "app.services.lead_sourcing.b2c_person_search.resolve_canonical_locations",
        lambda *a, **k: ["Argentina"],
    )
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Chile"],
    )
    campaign = SimpleNamespace(
        target_role="Founder",
        target_country="Argentina",
        target_company_hq="Chile",
        target_interests="running",
        target_area=None,
        target_language=None,
    )
    variants, _meta = build_b2c_filter_variants(campaign)
    assert variants
    assert all("company_location_search" in f for _, f in variants)
    assert all(f["company_location_search"]["include"] == ["Chile"] for _, f in variants)


@pytest.mark.parametrize(
    ("company_country", "ok"),
    [
        (None, True),
        ("", True),
        ("Argentina", True),
        ("Brazil", False),
    ],
)
def test_company_hq_matches_parametrized(monkeypatch, company_country, ok):
    monkeypatch.setattr(
        "app.services.lead_sourcing.location_check.resolve_company_hq_locations",
        lambda hq, max_locations=6: ["Argentina"],
    )
    assert company_hq_matches(company_country=company_country, campaign_hq="Argentina") is ok
