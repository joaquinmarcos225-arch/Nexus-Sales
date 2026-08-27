"""Nombres reales en copy — sin Test/Demo/roles."""

from app.services.openai_fallback import _build_day1_sections, _first_name, _sender_label
from app.services.outreach_display_names import (
    outreach_company_display,
    prospect_greeting_name,
    sender_first_name,
)


def test_outreach_company_strips_demo_noise():
    assert outreach_company_display("CostGuard Demo Client") == "CostGuard"
    assert outreach_company_display("Acme SA") == "Acme SA"
    assert outreach_company_display("Demo Test") == ""


def test_company_name_from_corporate_email():
    from app.services.outreach_display_names import company_name_from_domain, resolve_prospect_company_name

    assert company_name_from_domain("juan@acme.com.ar") == "Acme"
    assert company_name_from_domain("https://www.mercado-libre.com") == "Mercado Libre"
    assert company_name_from_domain("ana@gmail.com") == ""
    assert resolve_prospect_company_name(company_name="-", email="lucia@patagonia.com.ar") == "Patagonia"
    assert resolve_prospect_company_name(company_name="Empresa", email="x@globant.com") == "Globant"


def test_prospect_company_rejects_generic_empresa():
    from app.services.outreach_display_names import prospect_company_display, scrub_generic_empresa_in_copy

    assert prospect_company_display("Empresa") == ""
    assert prospect_company_display("Acme SA") == "Acme SA"
    text = "Soy Joaquín de CostGuard.\n\nTe escribo por tu rol en Empresa."
    out = scrub_generic_empresa_in_copy(text, prospect_company="Empresa", brand="CostGuard")
    assert "en Empresa" not in out
    assert "CostGuard" in out or "tu equipo" in out
    filled = scrub_generic_empresa_in_copy(
        "Vi que en [Empresa] están creciendo.", prospect_company="Acme SA"
    )
    assert "Acme SA" in filled
    assert "[Empresa]" not in filled


def test_never_greet_with_test_token():
    assert prospect_greeting_name("Test Mail Nexus") == "Mail"
    assert prospect_greeting_name({"name": "Test"}) == ""
    assert _first_name({"name": "Joaquín Marcos"}) == "Joaquín"
    assert _first_name({"name": "Test Mail Nexus"}) == "Mail"


def test_sender_skips_director_test():
    class U:
        first_name = "Director"
        name = "Director Test"

    assert sender_first_name(user=U(), campaign_sender="Director Test", fallback="Ana") == "Ana"
    assert _sender_label({"sender_name": "Director Test"}) == "Ana"
    assert _sender_label({"sender_name": "Joaquín"}) == "Joaquín"


def test_day1_greeting_uses_real_names():
    sections = _build_day1_sections(
        prospect={"name": "María López", "company_name": "Acme SA"},
        campaign={
            "sender_name": "Joaquín",
            "brand_name": "CostGuard",
            "name": "Outband LATAM Q2",
        },
        product={
            "name": "Plataforma Nexus",
            "value_proposition": (
                "Automatiza entre un 60% y un 90% de las tareas manuales de prospección outbound."
            ),
        },
    )
    assert sections["greeting"] == "Hola María,"
    assert "Joaquín" in sections["presentation"]
    assert "CostGuard" in sections["presentation"]
    assert "Test" not in sections["greeting"]
    assert "Director" not in sections["presentation"]
    assert "Test" not in sections["presentation"]
    assert "Demo" not in sections["presentation"]


def test_day1_skips_placeholder_prospect_name():
    sections = _build_day1_sections(
        prospect={"name": "Test Mail Nexus", "company_name": "Acme"},
        campaign={"sender_name": "Joaquín", "brand_name": "CostGuard", "name": "X"},
        product={"name": "Nexus", "value_proposition": "Automatiza prospección outbound."},
    )
    assert sections["greeting"] == "Hola Mail,"
    assert "Test" not in sections["greeting"]
