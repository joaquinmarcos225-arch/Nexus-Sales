"""Tests búsqueda B2C — filtros canónicos Prospeo + scoring."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.lead_sourcing.b2c_person_search import (
    build_b2c_filter_variants,
    interest_match_score,
    resolve_canonical_locations,
    score_b2c_person,
)


def _campaign(**kwargs):
    base = dict(
        id=1,
        target_country="Argentina",
        target_role="Personal Trainer",
        target_area="",
        target_interests="fitness, running",
        target_language="Español",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_resolve_locations_uses_suggestions():
    with patch(
        "app.services.lead_sourcing.b2c_person_search.suggest_locations",
        return_value=["Argentina", "Buenos Aires, Argentina"],
    ) as mocked:
        locs = resolve_canonical_locations("Argentina")
        assert "Argentina" in locs
        mocked.assert_called()


def test_b2c_filters_use_person_location_search_not_legacy():
    with (
        patch(
            "app.services.lead_sourcing.b2c_person_search.suggest_locations",
            return_value=["Argentina"],
        ),
        patch(
            "app.services.lead_sourcing.b2c_person_search.suggest_job_titles",
            side_effect=lambda q, limit=8: [q, f"Senior {q}"],
        ),
    ):
        variants, meta = build_b2c_filter_variants(_campaign())
        assert meta["locations_resolved"] == ["Argentina"]
        assert variants
        # Ningún filtro legacy incorrecto
        for label, filt in variants:
            assert "person_location" not in filt
            assert "person_headline" not in filt
            if "person_location_search" in filt:
                assert filt["person_location_search"]["include"] == ["Argentina"]
            if "person_job_title" in filt:
                assert filt["person_job_title"].get("match_mode") == "CONTAINS"


def test_b2c_filters_include_waterfall():
    with (
        patch(
            "app.services.lead_sourcing.b2c_person_search.suggest_locations",
            return_value=["Argentina"],
        ),
        patch(
            "app.services.lead_sourcing.b2c_person_search.suggest_job_titles",
            side_effect=lambda q, limit=8: [q],
        ),
    ):
        variants, _ = build_b2c_filter_variants(_campaign())
        labels = [lab for lab, _ in variants]
        assert any(lab.startswith("loc+titles") for lab in labels)
        assert any(lab.startswith("quick:") for lab in labels)
        assert "loc_only" in labels


def test_interest_match_score():
    person = {"current_job_title": "Running Coach", "headline": "Marathon & fitness"}
    assert interest_match_score(person, ["running", "fitness"]) >= 18
    assert interest_match_score(person, ["blockchain"]) == 0


def test_score_b2c_person_rewards_contactability():
    person = {
        "first_name": "Ana",
        "last_name": "Pérez",
        "current_job_title": "Fitness Coach",
        "email": {"email": "ana@example.com", "status": "VERIFIED"},
        "linkedin_url": "https://www.linkedin.com/in/ana-perez",
        "location": "Buenos Aires, Argentina",
    }
    # extract_email_phone may need specific shape — patch if needed
    with patch(
        "app.services.lead_sourcing.b2c_person_search.extract_email_phone",
        return_value=("ana@example.com", None),
    ):
        score, breakdown = score_b2c_person(
            person,
            interests=["fitness"],
            locations=["Argentina"],
            country_hint="Argentina",
        )
    assert score >= 55
    assert "email" in breakdown or "interés" in breakdown or "B2C" in breakdown
