"""Tests for LinkedIn inbound reply composition."""

from unittest.mock import MagicMock, patch

from app.services.linkedin_reply_compose import (
    compose_linkedin_inbound_reply,
    linkedin_inbound_offline_draft,
    linkedin_reply_fallback_draft,
    sanitize_company_display,
)


def _mia_prospect():
    prospect = MagicMock()
    prospect.name = "Mia Álvarez"
    prospect.company_name = "Prueba LinkedIn Nexus"
    prospect.id = 10
    prospect.interest_level = "high"
    return prospect


def _campaign():
    campaign = MagicMock()
    campaign.sender_name = ""
    campaign.seller_id = None
    campaign.product = MagicMock(
        name="Plataforma Nexus",
        value_proposition="Consolidá ventas y campañas en un solo lugar.",
        description="",
        target_notes="",
    )
    campaign.product.name = "Plataforma Nexus"
    campaign.seller = MagicMock(first_name="Joaquin", last_name="Marcos", name="Joaquin Marcos")
    return campaign


def test_sanitize_company_display_hides_test_names():
    assert sanitize_company_display("Prueba LinkedIn Nexus") is None
    assert sanitize_company_display("SquadS Ventures") == "SquadS Ventures"


def test_linkedin_inbound_offline_draft_references_inbound_not_generic_template():
    prospect = _mia_prospect()
    campaign = _campaign()
    inbound = (
        "Hola Joaquin, sí, me ocupo de ventas. "
        "Contame qué hace Nexus y cómo se diferencia de lo que usamos hoy."
    )
    draft = linkedin_inbound_offline_draft(prospect, campaign, inbound_text=inbound)

    assert "Prueba LinkedIn Nexus" not in draft
    assert "consolidar prospectos, campañas y reportes" not in draft.lower()
    assert "Nexus" in draft
    assert "Joaquin" in draft
    assert "«" not in draft
    assert "mencionaste" not in draft.lower()
    assert "diferencia" in draft.lower() or "CRM" in draft or "secuenciador" in draft


def test_offline_draft_strips_test_verify_and_uses_interest_angle():
    prospect = _mia_prospect()
    campaign = _campaign()
    inbound = "Hola, me interesa Nexus. Test verify 2026-07-05T21:48:51.114116+00:00"
    draft = linkedin_inbound_offline_draft(prospect, campaign, inbound_text=inbound)

    assert "Test verify" not in draft
    assert "2026-07-05" not in draft
    assert "«" not in draft
    assert "mencionaste" not in draft.lower()
    assert "Mia" in draft
    # Interés simple: CTA a reunión, sin pitch de producto.
    assert "agendar" in draft.lower() or "reunión" in draft.lower() or "reunion" in draft.lower()
    assert "90%" not in draft
    assert "trabajo manual" not in draft.lower()


def test_linkedin_reply_fallback_delegates_to_offline():
    prospect = MagicMock()
    prospect.name = "Mia Álvarez"
    prospect.company_name = "Acme"
    campaign = _campaign()
    campaign.product = MagicMock(name="Nexus", value_proposition="Outbound", description="")

    inbound = "Me interesa saber más sobre automatización"
    draft = linkedin_reply_fallback_draft(prospect, campaign, inbound_text=inbound)

    assert "Nexus" in draft
    assert "«" not in draft


@patch("app.services.linkedin_reply_compose._ensure_seller_sign", side_effect=lambda d, c, db=None: d)
@patch("app.services.openai_service.openai_configured", return_value=True)
@patch("app.services.openai_service.generate_linkedin_inbound_reply")
@patch("app.services.ai_instruction_context.campaign_education_blob", return_value="")
def test_compose_uses_dedicated_linkedin_generator(
    mock_edu,
    mock_gen,
    mock_configured,
    mock_sign,
):
    mock_gen.return_value = (
        "Hola Mia, Nexus automatiza prospección sin perder el toque humano. ¿Charlamos 15 min?"
    )

    draft = compose_linkedin_inbound_reply(
        MagicMock(),
        prospect=_mia_prospect(),
        campaign=_campaign(),
        inbound_text="¿Cómo funciona Nexus?",
        history=[],
    )

    mock_gen.assert_called_once()
    assert "Mia" in draft or "Nexus" in draft
    assert "consolidar prospectos" not in draft.lower()
