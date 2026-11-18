"""Tests for LinkedIn inbound reply composition."""

import re
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
    assert "Saludos" not in draft
    assert "Joaquin" not in draft  # chat LI: sin firma de mail
    assert "«" not in draft
    assert "mencionaste" not in draft.lower()
    assert "…" not in draft and "..." not in draft
    assert (
        "diferencia" in draft.lower()
        or "CRM" in draft
        or "secuenciador" in draft
        or "consolidá" in draft.lower()
        or "consolid" in draft.lower()
    )


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
    prospect.id = 3
    campaign = _campaign()
    campaign.product = MagicMock()
    campaign.product.name = "Nexus"
    campaign.product.value_proposition = "Outbound multicanal con IA"
    campaign.product.description = ""
    campaign.product.target_notes = ""

    inbound = "Me interesa saber más sobre automatización"
    draft = linkedin_reply_fallback_draft(prospect, campaign, inbound_text=inbound)

    assert "Nexus" in draft
    assert "«" not in draft
    assert "Saludos" not in draft


def test_offline_how_it_works_keeps_full_value_any_product():
    """Cualquier producto: no partir listas/cláusulas a medias al explicar cómo funciona."""
    prospect = _mia_prospect()
    prospect.id = 10
    campaign = _campaign()
    campaign.product = MagicMock(
        name="Nexus Sales",
        value_proposition=(
            "Automatiza entre 60% y 90% de tareas manuales de prospección outbound "
            "integrando email, LinkedIn y WhatsApp en un flujo único con IA, "
            "permitiendo que el SDR solo intervenga cuando el prospecto muestra interés real."
        ),
        description="",
        target_notes="Beneficios principales\nReducción del 60-90% en carga manual.",
    )
    draft = linkedin_inbound_offline_draft(
        prospect,
        campaign,
        inbound_text="¿Qué hace exactamente Nexus / cómo funciona?",
    )
    low = draft.lower()
    assert "linkedin" in low and "whatsapp" in low
    assert "email" in low
    assert "ia" in low or "flujo" in low
    assert "interés" in low or "interes" in low
    assert "sdr" in low
    assert "saludos" not in low
    assert not re.search(r"prospecto\.\s*¿", draft, re.I)


def test_clip_keeps_list_for_non_nexus_product():
    from app.services.linkedin_reply_compose import _clip_complete_thought

    vp = (
        "Reduce el gasto cloud entre 20% y 40% unificando AWS, Azure y GCP "
        "en un solo panel con alertas, permitiendo que FinOps actúe antes del overage."
    )
    out = _clip_complete_thought(vp, max_chars=110)
    low = out.lower()
    assert "aws" in low and "azure" in low and "gcp" in low
    assert not out.rstrip(".").endswith("AWS")


def test_product_value_pitch_has_variety_by_seed():
    from app.services.linkedin_reply_compose import (
        product_value_pitch,
        product_value_pitch_variants,
    )

    campaign = _campaign()
    campaign.product = MagicMock(
        name="Nexus Sales",
        value_proposition=(
            "Automatiza entre 60% y 90% de tareas manuales de prospección outbound "
            "integrando email, LinkedIn y WhatsApp en un flujo único con IA."
        ),
        description="",
        target_notes="",
    )
    variants = product_value_pitch_variants(campaign, max_chars=260)
    assert len(variants) >= 2
    a = product_value_pitch(campaign, max_chars=260, seed=0)
    b = product_value_pitch(campaign, max_chars=260, seed=1)
    c = product_value_pitch(campaign, max_chars=260, seed=2)
    # Al menos dos seeds distintos no deben ser idénticos.
    assert len({a, b, c}) >= 2


def test_trim_words_strips_dangling_cuando_multi_pass():
    from app.services.linkedin_reply_compose import (
        _trim_words,
        draft_wrong_contrast_for_integration,
        utterance_looks_truncated,
    )

    long = (
        "Vi que sos SDR Manager de Empresa Test3. "
        "Nosotros con Nexus Sales automatizamos entre 60% y 90% de tareas manuales "
        "de prospección outbound integrando email, LinkedIn y WhatsApp en un flujo "
        "único con IA, permitiendo que el SDR solo intervenga cuando el prospecto "
        "muestra interés real y avanza en el proceso comercial con claridad."
    )
    out = _trim_words(long, max_words=40)
    assert not utterance_looks_truncated(out)
    low = out.lower().rstrip(".")
    assert not low.endswith("cuando")
    assert not low.endswith("que")
    assert not low.endswith("permitiendo")
    assert out.endswith((".", "?", "!"))
    assert utterance_looks_truncated(
        "… permitiendo que el SDR solo intervenga cuando."
    )
    assert draft_wrong_contrast_for_integration(
        "¿Integran con HubSpot?",
        "Mirá la diferencia: Nexus vs HubSpot…",
    )
    assert not draft_wrong_contrast_for_integration(
        "¿Integran con HubSpot?",
        "Hoy no tenemos integración nativa con HubSpot listada.",
    )


def test_trim_words_keeps_complete_sentence_no_ellipsis():
    from app.services.linkedin_reply_compose import _trim_words

    long = (
        "Hola Mia, Nexus automatiza entre 60% y 90% de las tareas manuales de prospección "
        "outbound, integrando email, LinkedIn y WhatsApp en un solo flujo con IA. "
        "El SDR solo interviene cuando el prospecto muestra interés real. "
        "¿Te parece si coordinamos una llamada breve de 15 minutos para ver tu caso?"
    )
    out = _trim_words(long, max_words=40)
    assert "…" not in out and "..." not in out
    assert out.endswith((".", "?", "!"))
    assert "Saludos" not in out


def test_ensure_seller_sign_strips_email_signoff():
    from app.services.linkedin_reply_compose import _ensure_seller_sign

    raw = "¿Te sirve una charla breve?\n\nSaludos,\nJoaquin"
    assert _ensure_seller_sign(raw, _campaign()) == "¿Te sirve una charla breve?"
    raw2 = "¿Te sirve una charla breve?\n\nQuedo atento."
    assert _ensure_seller_sign(raw2, _campaign()) == "¿Te sirve una charla breve?"


def test_compose_rejects_cold_email_template_for_how_it_works():
    with patch(
        "app.services.openai_service.openai_configured", return_value=True
    ), patch(
        "app.services.openai_service.generate_linkedin_inbound_reply",
        return_value=(
            "Hola Mia, ¿cómo estás?\n\n"
            "Vuelvo sobre el mensaje anterior.\n"
            "Si te sirve, avisame y armamos un espacio corto.\n\n"
            "Quedo atento."
        ),
    ), patch(
        "app.services.ai_instruction_context.campaign_education_blob", return_value=""
    ), patch(
        "app.services.linkedin_reply_compose.generate_linkedin_inbound_reply_for_prospect",
        side_effect=RuntimeError("skip playbook"),
    ):
        draft = compose_linkedin_inbound_reply(
            MagicMock(),
            prospect=_mia_prospect(),
            campaign=_campaign(),
            inbound_text="¿Qué hace exactamente Nexus / cómo funciona?",
            history=[],
        )
    assert "Vuelvo sobre" not in draft
    assert "Saludos" not in draft
    assert "Quedo atento" not in draft
    assert "Nexus" in draft or "buena pregunta" in draft.lower()


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

def test_unsolicited_product_pitch_detected():
    from app.services.linkedin_reply_compose import _looks_like_unsolicited_product_pitch

    pitch = (
        "Con Nexus automatizamos entre 60% y 90% de las tareas de prospeccion outbound "
        "unificando email, LinkedIn y WhatsApp."
    )
    assert _looks_like_unsolicited_product_pitch(pitch) is True
    assert _looks_like_unsolicited_product_pitch("Te parece agendar 15 min esta semana?") is False


def test_offline_interest_has_no_product_pitch():
    draft = linkedin_inbound_offline_draft(
        _mia_prospect(),
        _campaign(),
        inbound_text="Ok dale, me interesa",
        allow_opening_greeting=True,
    )
    low = draft.lower()
    assert "60%" not in draft and "90%" not in draft
    assert "automatiz" not in low
    assert "agendar" in low or "reunion" in low or "reunión" in low or "llamada" in low
