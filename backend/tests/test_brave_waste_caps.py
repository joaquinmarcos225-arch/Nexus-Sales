"""Paso 4: menos queries Brave (early-stop / caps)."""

from __future__ import annotations

from unittest.mock import patch

from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.corporate_domain_resolver import (
    CorporateDomainResolution,
    _resolve_via_web_search,
)
from app.services.lead_sourcing.web_executive_fallback import _build_queries


def test_domain_web_search_stops_after_first_good_hit():
    calls = {"n": 0}

    def fake_search(query, limit=6, country=None, provider=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [
                (
                    "https://acme-real-estate.com",
                    "Acme Real Estate — Home",
                    "Inmobiliaria Acme",
                )
            ]
        return [("https://other.com", "Other", "x")]

    with (
        patch(
            "app.services.lead_sourcing.corporate_domain_resolver.configured_backend",
            return_value=object(),
        ),
        patch(
            "app.services.lead_sourcing.corporate_domain_resolver.search_web",
            side_effect=fake_search,
        ),
        patch(
            "app.services.lead_sourcing.corporate_domain_resolver._pick_best_from_hits",
            side_effect=lambda hits, name, min_score=0: (
                CorporateDomainResolution(
                    "acme-real-estate.com",
                    "https://acme-real-estate.com",
                    "web_search",
                    message="ok",
                )
                if hits
                else None
            ),
        ),
    ):
        res = _resolve_via_web_search("Acme Real Estate", max_queries=5)

    assert res.resolved
    assert calls["n"] == 1


def test_web_executive_fallback_one_query_max():
    company = CompanyCandidateRead(
        external_id="c1",
        name="Acme SA",
        company_domain="acme.test",
    )
    qs = _build_queries(company, "CEO")
    assert len(qs) == 1
