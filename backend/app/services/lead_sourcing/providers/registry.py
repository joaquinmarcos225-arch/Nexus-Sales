"""Registro de proveedores — MVP: Web Search + Prospeo."""

from __future__ import annotations

from app.services.lead_sourcing.env_config import env_present
from app.services.lead_sourcing.providers.base import ProviderStatus
from app.services.lead_sourcing.providers.prospeo_enrichment import ProspeoEnrichmentProvider
from app.services.lead_sourcing.providers.web_search_company import WebSearchCompanyProvider

_company: WebSearchCompanyProvider | None = None
_enrich: ProspeoEnrichmentProvider | None = None


def get_company_search_provider() -> WebSearchCompanyProvider:
    global _company
    if _company is None:
        _company = WebSearchCompanyProvider()
    return _company


def get_contact_enrichment_provider() -> ProspeoEnrichmentProvider:
    global _enrich
    if _enrich is None:
        _enrich = ProspeoEnrichmentProvider()
    return _enrich


def get_providers_status() -> list[ProviderStatus]:
    """
    Estado local de proveedores — solo variables de entorno.
    Sin HTTP externo, sin reload de .env, sin instanciar proveedores.
    """
    from app.services.lead_sourcing.providers.web_search_backends import (
        configured_backend,
        legacy_google_search_env_present,
    )

    backend = configured_backend()
    web_configured = backend is not None
    if web_configured and backend:
        web_msg = f"Web Search listo ({backend.label})"
    elif legacy_google_search_env_present():
        web_msg = (
            "GOOGLE_SEARCH_* en .env ya no se usa. "
            "Agregá BRAVE_SEARCH_API_KEY (api.search.brave.com) o SERPAPI_API_KEY."
        )
    else:
        web_msg = "Falta BRAVE_SEARCH_API_KEY o SERPAPI_API_KEY"

    prospeo_configured = env_present("PROSPEO_API_KEY")

    return [
        ProviderStatus(
            name="web_search",
            configured=web_configured,
            message=web_msg,
        ),
        ProviderStatus(
            name="prospeo",
            configured=prospeo_configured,
            message="Prospeo listo (enriquecimiento selectivo)"
            if prospeo_configured
            else "Falta PROSPEO_API_KEY",
        ),
    ]


def pipeline_ready() -> bool:
    """MVP operativo B2B: Web Search + Prospeo."""
    return mvp_pipeline_ready()


def prospeo_ready() -> bool:
    by_name = {s.name: s.configured for s in get_providers_status()}
    return bool(by_name.get("prospeo"))


def mvp_pipeline_ready() -> bool:
    by_name = {s.name: s.configured for s in get_providers_status()}
    return bool(by_name.get("web_search") and by_name.get("prospeo"))


def pipeline_ready_for_campaign(campaign) -> bool:
    """B2C / rol-first: Prospeo. B2B con industria: Web Search + Prospeo."""
    from app.services.campaign_market import campaign_is_b2c
    from app.services.lead_sourcing.sourcing_route import campaign_uses_role_first_sourcing

    if campaign_is_b2c(campaign) or campaign_uses_role_first_sourcing(campaign):
        return prospeo_ready()
    return mvp_pipeline_ready()
