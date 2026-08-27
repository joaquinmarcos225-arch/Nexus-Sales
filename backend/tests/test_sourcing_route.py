"""Ruta empresa→rol vs rol-first."""

from types import SimpleNamespace

from app.services.lead_sourcing.role_person_search import build_role_first_filter_variants
from app.services.lead_sourcing.sourcing_route import (
    campaign_uses_role_first_sourcing,
)


def test_role_first_when_role_without_industry():
    c = SimpleNamespace(
        outreach_mode="b2b",
        target_industry=None,
        target_role="Head of Sales",
    )
    assert campaign_uses_role_first_sourcing(c) is True


def test_company_first_when_industry_set():
    c = SimpleNamespace(
        outreach_mode="b2b",
        target_industry="SaaS",
        target_role="Head of Sales",
    )
    assert campaign_uses_role_first_sourcing(c) is False


def test_not_role_first_without_role():
    c = SimpleNamespace(
        outreach_mode="b2b",
        target_industry=None,
        target_role=None,
    )
    assert campaign_uses_role_first_sourcing(c) is False


def test_b2c_never_uses_b2b_role_first_flag():
    c = SimpleNamespace(
        outreach_mode="b2c",
        target_industry=None,
        target_role="Coach",
    )
    assert campaign_uses_role_first_sourcing(c) is False


def test_role_first_filters_include_titles(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.role_person_search.resolve_canonical_locations",
        lambda *_a, **_k: ["Argentina", "Mexico"],
    )
    monkeypatch.setattr(
        "app.services.lead_sourcing.role_person_search.prospeo_role_title_includes",
        lambda *_a, **_k: ["Head of Sales", "Director Comercial"],
    )
    c = SimpleNamespace(target_role="Head of Sales", target_country="LATAM - Brasil")
    variants, meta = build_role_first_filter_variants(c)
    assert variants
    assert "Head of Sales" in meta["titles_resolved"]
    assert any("person_job_title" in f for _, f in variants)
