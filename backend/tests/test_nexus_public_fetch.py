"""Paso G: fetch propio de sitios corporativos antes de Brave."""

from __future__ import annotations

from unittest.mock import patch

from app.services.lead_sourcing import cogs_runtime_metrics as m
from app.services.lead_sourcing.nexus_public_fetch import (
    CompanyPageSignals,
    parse_page_meta,
    signals_to_search_hits,
    signals_to_snippet_lines,
)
from app.services.lead_sourcing.nexus_web_search import search_web_tiered, try_own_fetch_hits


def test_parse_page_meta_extracts_title_and_description():
    html = """
    <html><head>
    <title>Acme Corp — Sales Platform</title>
    <meta name="description" content="Automatizamos prospección B2B para equipos SaaS." />
    <meta property="og:site_name" content="Acme" />
    </head><body></body></html>
    """
    sig = parse_page_meta(html, "https://www.acme.com/")
    assert sig.title == "Acme Corp — Sales Platform"
    assert "prospección B2B" in sig.description
    assert sig.domain == "acme.com"


def test_signals_to_search_hits_and_snippets():
    sig = CompanyPageSignals(
        domain="acme.com",
        url="https://acme.com",
        title="Acme",
        description="Software de ventas",
        site_name="Acme",
        industry_hint="saas, crm",
    )
    hits = signals_to_search_hits(sig)
    assert len(hits) == 1
    assert hits[0][0] == "https://acme.com"
    lines = signals_to_snippet_lines(sig, company_name="Acme SA")
    assert any("Software de ventas" in ln for ln in lines)


def test_search_web_tiered_skips_brave_when_own_fetch_hits():
    m.reset_for_tests()
    own = [("https://acme.com", "Acme", "Desc")]
    with patch(
        "app.services.lead_sourcing.nexus_web_search.try_own_fetch_hits",
        return_value=own,
    ) as mock_own:
        with patch(
            "app.services.lead_sourcing.nexus_web_search.search_web"
        ) as mock_brave:
            hits = search_web_tiered(
                "Acme empresa qué hace",
                company_domain="acme.com",
            )
    mock_own.assert_called_once()
    mock_brave.assert_not_called()
    assert hits == own
    assert m.snapshot()["nexus_fetch_calls"] == 0


def test_try_own_fetch_records_metric(monkeypatch):
    m.reset_for_tests()
    sig = CompanyPageSignals(
        domain="acme.com",
        url="https://acme.com",
        title="Acme",
        description="Desc",
        site_name="Acme",
        industry_hint="",
    )

    monkeypatch.setattr(
        "app.services.lead_sourcing.nexus_public_fetch.fetch_html",
        lambda url: "<title>Acme</title>",
    )

    from app.services.lead_sourcing.nexus_public_fetch import fetch_company_page_signals

    out = fetch_company_page_signals("acme.com")
    assert out is not None
    assert out.title == "Acme"
    assert m.snapshot()["nexus_fetch_calls"] == 1
