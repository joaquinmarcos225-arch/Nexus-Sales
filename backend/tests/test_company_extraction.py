"""Tests extracción ICP: normalizador, política Phantom, clasificador."""

from __future__ import annotations

from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_extraction_policy import (
    compute_extraction_confidence,
    passes_phantom_company_gate,
    passes_web_search_company_row,
)
from app.services.lead_sourcing.company_name_normalizer import (
    is_seo_listing_title,
    normalize_company_name,
)
from app.services.lead_sourcing.company_search_classifier import classify_company_hit
from app.services.lead_sourcing.lead_sourcing_company_targeting import is_generic_company_name


def test_normalize_strips_seo_title():
    raw = "Logiciel Solutions Reviews (5), Pricing, Services & ..."
    assert normalize_company_name(raw) == "Logiciel Solutions"


def test_seo_titles_rejected():
    assert is_generic_company_name("SaaS Development")
    assert is_seo_listing_title("Top 10 Best SaaS Companies 2024")
    assert not is_seo_listing_title("Stripe")


def test_linkedin_company_classified():
    hit = classify_company_hit(
        "https://www.linkedin.com/company/stripe",
        "Stripe | LinkedIn",
    )
    assert hit is not None
    assert hit.source_type == "linkedin_company"
    assert hit.normalized_name == "Stripe"
    assert hit.confidence >= 70


def test_g2_uses_slug_not_seo_title():
    hit = classify_company_hit(
        "https://www.g2.com/products/logiciel-solutions",
        "Logiciel Solutions Reviews (5), Pricing, Services & ...",
    )
    assert hit is not None
    assert hit.source_type == "g2_product"
    assert "Reviews" not in hit.normalized_name
    assert hit.confidence < 70


def test_phantom_gate_only_high_confidence_profiles():
    good = CompanyCandidateRead(
        external_id="1",
        provider="web_search",
        name="Stripe",
        website_url="https://www.linkedin.com/company/stripe",
        result_kind="company",
        normalized_company_name="Stripe",
        source_type="linkedin_company",
        confidence=88,
        icp_relevance_score=70,
    )
    bad_seo = good.model_copy(
        update={
            "name": "SaaS Development",
            "normalized_company_name": "SaaS Development",
            "source_type": "linkedin_company",
            "confidence": 88,
        }
    )
    bad_g2 = good.model_copy(
        update={
            "source_type": "g2_product",
            "confidence": 55,
        }
    )
    assert passes_phantom_company_gate(good)
    assert not passes_phantom_company_gate(bad_g2)
    assert not passes_phantom_company_gate(bad_seo)
    assert passes_web_search_company_row(good)
    assert compute_extraction_confidence(
        source_type="g2_product",
        icp_relevance_score=50,
        quality_score=90,
        normalized_name="Acme",
        raw_title="Acme Reviews Pricing",
    ) == 0
