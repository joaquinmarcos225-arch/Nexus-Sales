"""Tests mapeo región ICP → búsqueda."""

from app.services.lead_sourcing.company_search_queries import build_company_search_queries
from app.services.lead_sourcing.icp_industry_search import industry_search_terms
from app.services.lead_sourcing.icp_region import (
    brave_country_for_query,
    resolve_region_search_context,
    score_region_alignment,
)
from app.models.campaign import Campaign


def test_latam_sin_brasil_brave_codes():
    ctx = resolve_region_search_context("LATAM - Brasil")
    assert ctx is not None
    assert "BR" not in ctx.brave_country_codes
    assert "MX" in ctx.brave_country_codes
    assert "Latin America" in ctx.query_phrase


def test_latam_con_brasil_includes_br():
    ctx = resolve_region_search_context("LATAM + Brasil")
    assert ctx is not None
    assert "BR" in ctx.brave_country_codes


def test_brave_country_rotates():
    ctx = resolve_region_search_context("EMEA")
    assert brave_country_for_query(ctx, 0) == "GB"
    assert brave_country_for_query(ctx, 1) == "DE"


def test_score_region_mexico_in_latam_sin_brasil():
    score, _ = score_region_alignment("LATAM - Brasil", "Mexico")
    assert score >= 85


def test_score_region_brazil_excluded_from_latam_sin_brasil():
    score, _ = score_region_alignment("LATAM - Brasil", "Brazil")
    assert score == 0


def test_infer_country_from_snippet():
    from app.services.lead_sourcing.icp_region import infer_country_from_text

    assert infer_country_from_text("RBA Inmobiliaria Buenos Aires Argentina") == "Argentina"
    assert infer_country_from_text("Empresa en Santiago de Chile") == "Chile"


def test_conflicting_country_in_latam_sin_brasil():
    from app.services.lead_sourcing.icp_region import text_has_conflicting_country

    assert text_has_conflicting_country(
        "Inmobiliaria São Paulo Brazil", "LATAM - Brasil"
    )
    assert not text_has_conflicting_country(
        "Inmobiliaria Buenos Aires Argentina", "LATAM - Brasil"
    )


def test_industry_search_terms_saas():
    terms = industry_search_terms("B2B SaaS — Sales Enablement")
    assert any("saas" in t.lower() for t in terms)
    assert terms[0] == "B2B SaaS — Sales Enablement"


def test_company_queries_use_search_phrase_not_raw_region_label():
    campaign = Campaign(
        name="Test",
        target_industry="SaaS",
        target_country="LATAM - Brasil",
        target_role="Head of Sales",
        target_company_size="Startup",
    )
    queries = build_company_search_queries(campaign, max_queries=3)
    assert queries
    joined = " ".join(queries).lower()
    assert "latin america" in joined or "mexico" in joined
    assert "latam - brasil" not in joined
