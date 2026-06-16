"""Tests validación semántica dominio vs empresa."""

from app.services.lead_sourcing.domain_semantic_validation import (
    classify_domain_trust,
    domain_semantically_matches_company,
)


def test_kinde_not_kickstarter():
    ok, _ = domain_semantically_matches_company("Kinde", "kickstarter.com")
    assert not ok
    assert classify_domain_trust("Kinde", "kickstarter.com") == "doubtful"


def test_nanonets_not_tracxn():
    ok, _ = domain_semantically_matches_company("Nanonets", "tracxn.com")
    assert not ok


def test_saas_co_not_getlatka():
    ok, _ = domain_semantically_matches_company("The SaaS Co", "getlatka.com")
    assert not ok


def test_cube_matches():
    ok, _ = domain_semantically_matches_company("Cube Careers", "cube.dev")
    assert ok
    assert classify_domain_trust("Cube Careers", "cube.dev") == "verified"


def test_saasstartupkit_matches():
    ok, _ = domain_semantically_matches_company("GO SaaS Startup Kit", "saasstartupkit.com")
    assert ok
