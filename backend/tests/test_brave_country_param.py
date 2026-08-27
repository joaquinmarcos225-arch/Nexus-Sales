"""Brave country param — evita 422 con códigos no soportados (ej. CO)."""

from app.services.lead_sourcing.providers.web_search_backends import brave_country_param


def test_brave_country_accepts_supported_iso():
    assert brave_country_param("mx") == "MX"
    assert brave_country_param("AR") == "AR"
    assert brave_country_param("US") == "US"


def test_brave_country_rejects_unsupported_latam_codes():
    # Colombia / Peru / etc. no están en el enum de Brave Web Search.
    assert brave_country_param("CO") is None
    assert brave_country_param("PE") is None
    assert brave_country_param("UY") is None
    assert brave_country_param("Colombia") is None
    assert brave_country_param("") is None
    assert brave_country_param(None) is None
