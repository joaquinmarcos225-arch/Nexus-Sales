"""Banco cold B2B/B2C: slots, canales, sin inventar industria."""

import re

from app.services.cold_message_bank import (
    bank_counts,
    first_touch_on_channel,
    render_cold_bank_touch,
)
from app.services.lead_sourcing.sdr_playbook_outreach import generate_sdr_playbook_touch


def _product_autoparts():
    return {
        "name": "Kit pastillas freno XYZ",
        "value_proposition": (
            "Pastillas cerámicas con mayor vida útil y menos ruido para flotas y talleres"
        ),
        "description": "Repuestos de freno para uso intensivo.",
    }


def _product_saas():
    return {
        "name": "Nexus Sales",
        "value_proposition": (
            "Automatizamos el outreach multicanal para agendar más reuniones "
            "con menos trabajo manual"
        ),
    }


def test_bank_counts_match_plan():
    counts = bank_counts()
    assert counts == {
        "b2b-email-cold": 20,
        "b2b-email-fu": 8,
        "b2b-linkedin-cold": 15,
        "b2b-linkedin-fu": 8,
        "b2b-whatsapp-cold": 15,
        "b2b-whatsapp-fu": 8,
        "b2c-email-cold": 20,
        "b2c-email-fu": 8,
        "b2c-linkedin-cold": 15,
        "b2c-linkedin-fu": 8,
        "b2c-whatsapp-cold": 15,
        "b2c-whatsapp-fu": 8,
    }


def test_bank_forbids_truncated_cta_questions():
    from app.services.cold_message_bank import _BANKS

    forbidden = ("¿charla corta?", "¿agendamos?", "¿15 min?")
    for key, templates in _BANKS.items():
        blob = "\n".join(templates).lower()
        for bad in forbidden:
            assert bad not in blob, f"{key} contains {bad!r}"


def test_autoparts_message_uses_product_valor_not_saas_pain():
    r = render_cold_bank_touch(
        channel="email",
        prospect={
            "id": 101,
            "name": "Ana Lopez",
            "company_name": "Taller Sur",
            "role": "Dueña",
        },
        campaign={
            "id": 9,
            "sender_name": "Joaquin",
            "brand_name": "AutoParts Sur",
            "outreach_mode": "b2b",
        },
        product=_product_autoparts(),
        prior_touches=[],
        first_touch=True,
    )
    low = r.body.lower()
    assert "pastillas" in low
    assert "contacto manual" not in low
    assert "prospección" not in low
    assert "pipeline" not in low
    assert "Saludos," in r.body
    assert r.subject


def test_whatsapp_has_no_email_signature():
    r = render_cold_bank_touch(
        channel="whatsapp",
        prospect={"id": 7, "name": "Luis", "company_name": "Acme"},
        campaign={
            "id": 1,
            "sender_name": "Joaquin",
            "brand_name": "Marca",
            "outreach_mode": "b2b",
        },
        product=_product_autoparts(),
        first_touch=True,
    )
    assert "Saludos," not in r.body
    assert "¿" in r.body


def test_b2c_differs_from_b2b_tone():
    prospect = {"id": 3, "name": "Sofia"}
    product = _product_autoparts()
    base_campaign = {"id": 2, "sender_name": "Ana", "brand_name": "Marca"}
    b2b = render_cold_bank_touch(
        channel="linkedin",
        prospect=prospect,
        campaign={**base_campaign, "outreach_mode": "b2b"},
        product=product,
        first_touch=True,
    )
    b2c = render_cold_bank_touch(
        channel="linkedin",
        prospect=prospect,
        campaign={**base_campaign, "outreach_mode": "b2c"},
        product=product,
        first_touch=True,
    )
    assert b2b.market == "b2b"
    assert b2c.market == "b2c"
    assert b2b.template_id.startswith("b2b-")
    assert b2c.template_id.startswith("b2c-")


def test_follow_up_on_same_channel_uses_fu_bank():
    r = render_cold_bank_touch(
        channel="email",
        prospect={"id": 4, "name": "Pedro", "company_name": "Beta"},
        campaign={
            "id": 8,
            "sender_name": "Joaquin",
            "brand_name": "Marca",
            "outreach_mode": "b2b",
        },
        product=_product_saas(),
        prior_touches=[{"channel": "email", "body": "hola previa"}],
        first_touch=False,
        step_day=10,
    )
    assert r.kind == "fu"
    assert "Quedo atento" in r.body
    assert "Saludos," not in r.body


def test_first_touch_on_channel_ignores_other_channels():
    prior = [{"channel": "email", "body": "mail previo largo suficiente"}]
    assert first_touch_on_channel(prior, "linkedin") is True
    assert first_touch_on_channel(prior, "email") is False


def test_generate_sdr_playbook_touch_uses_bank():
    subject, body, reason = generate_sdr_playbook_touch(
        channel="linkedin",
        prospect={
            "id": "12",
            "name": "Carla",
            "company_name": "Gamma SA",
            "role": "Compras",
        },
        campaign={
            "id": "5",
            "sender_name": "Joaquin",
            "brand_name": "AutoParts",
            "outreach_mode": "b2b",
        },
        product=_product_autoparts(),
        education="",
        step_day=4,
        step_objective="linkedin",
        prior_touches=[{"channel": "email", "body": "mail day1 previo con suficiente texto"}],
    )
    assert subject is None
    assert "Carla" in body
    assert "pastillas" in body.lower()
    assert "Saludos," not in body
    assert reason.hypothesis


def test_valor_molded_for_automatiza_vp():
    r = render_cold_bank_touch(
        channel="whatsapp",
        prospect={"id": 50, "name": "Mia", "company_name": "Acme"},
        campaign={
            "id": 3,
            "sender_name": "Joaquin",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
        },
        product={
            "name": "Nexus Sales",
            "value_proposition": (
                "Automatiza entre 60% y 90% de tareas manuales de prospección outbound "
                "integrando email, LinkedIn y WhatsApp en un flujo único con IA"
            ),
        },
        first_touch=True,
    )
    low = r.body.lower()
    assert "automatiza entre" not in low or "con nexus" in low or "automatizamos" in low
    # No pegar el verbo en infinitivo/imperativo suelto tras otra oración.
    assert ". automatiza entre" not in low


def test_first_message_hooks_before_product_and_asks_how_are_you():
    """Nunca pitch inmediato; siempre engancha y pregunta cómo está al menos 1 vez."""
    r = render_cold_bank_touch(
        channel="whatsapp",
        prospect={
            "id": 77,
            "name": "Zuly Huamán",
            "company_name": "Sharwinn",
            "role": "CEO",
        },
        campaign={
            "id": 2,
            "sender_name": "Joaquin",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
        },
        product=_product_saas(),
        prior_touches=[],
        first_touch=True,
    )
    low = r.body.lower()
    assert "cómo estás" in low or "como estas" in low
    # No debe ser: Hola X, soy Y. Con Nexus… (pitch pegado)
    assert not re.search(
        r"soy joaquin\.?\s+con nexus",
        low,
    )
    assert "sharwinn" in low or "ceo" in low or "te escribo" in low or "pensé" in low or "contacto" in low
    assert "nexus" in low


def test_second_touch_leverages_prior_channel():
    r = render_cold_bank_touch(
        channel="whatsapp",
        prospect={"id": 88, "name": "Zuly", "company_name": "Sharwinn", "role": "CEO"},
        campaign={
            "id": 2,
            "sender_name": "Joaquin",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
        },
        product=_product_saas(),
        prior_touches=[
            {
                "channel": "linkedin",
                "body": "Hola Zuly, soy Joaquin. Te escribo por Sharwinn. Nexus Sales…",
            }
        ],
        first_touch=True,
        step_day=4,
    )
    low = r.body.lower()
    assert "linkedin" in low or "retomo" in low or "había escrito" in low or "te decía" in low


def test_third_touch_uses_strong_retomo():
    r = render_cold_bank_touch(
        channel="email",
        prospect={"id": 99, "name": "Zuly", "company_name": "Sharwinn"},
        campaign={
            "id": 2,
            "sender_name": "Joaquin",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
        },
        product=_product_saas(),
        prior_touches=[
            {"channel": "linkedin", "body": "msg1 con cómo estás incluido"},
            {"channel": "whatsapp", "body": "msg2 follow"},
        ],
        first_touch=True,
        step_day=7,
    )
    low = r.body.lower()
    assert "retomo lo que te decía por whatsapp" in low or "retomo" in low
