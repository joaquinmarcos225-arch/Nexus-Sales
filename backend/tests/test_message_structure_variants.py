"""Variantes de estructura outbound (selección + copy fallback)."""

from app.services.message_structure_variants import (
    FIRST_TOUCH_VARIANTS,
    FOLLOW_UP_VARIANTS,
    assemble_first_touch_body_from_sections,
    build_first_touch_sections,
    build_follow_up_body,
    pick_first_touch_variant,
    pick_follow_up_variant,
)


def test_first_touch_variant_stable_per_prospect_channel():
    a = pick_first_touch_variant(channel="linkedin", prospect_id=48, campaign_id=5)
    b = pick_first_touch_variant(channel="linkedin", prospect_id=48, campaign_id=5)
    assert a == b
    assert a in FIRST_TOUCH_VARIANTS


def test_first_touch_variants_differ_across_channels_sometimes():
    # No exigimos diferencia siempre, pero sí cobertura de las 3 variantes en un rango.
    seen = {
        pick_first_touch_variant(channel="email", prospect_id=i, campaign_id=1)
        for i in range(1, 60)
    }
    assert seen == set(FIRST_TOUCH_VARIANTS)


def test_follow_up_variants_are_channel_distinct_prompts():
    from app.services.message_structure_variants import follow_up_structure_prompt

    emails = {
        follow_up_structure_prompt(channel="email", variant=v)
        for v in FOLLOW_UP_VARIANTS
    }
    linkedins = {
        follow_up_structure_prompt(channel="linkedin", variant=v)
        for v in FOLLOW_UP_VARIANTS
    }
    whatsapps = {
        follow_up_structure_prompt(channel="whatsapp", variant=v)
        for v in FOLLOW_UP_VARIANTS
    }
    assert len(emails) == 2
    assert len(linkedins) == 2
    assert len(whatsapps) == 2
    # Los 6 textos deben ser distintos entre sí.
    assert len(emails | linkedins | whatsapps) == 6
    for text in emails | linkedins | whatsapps:
        assert "Quedo atento" in text
        assert "equipos comerciales a {" not in text.lower()


def test_first_touch_crm_bodies_no_fixed_commercial_team_slot():
    prospect = {
        "id": "11",
        "name": "Ada Lovelace",
        "company_name": "Analytical Engines",
        "role": "CTO",
    }
    campaign = {
        "id": "1",
        "sender_name": "Joaquin",
        "brand_name": "CostGuard",
    }
    product = {
        "name": "Nexus Sales",
        "description": (
            "plataforma B2B de ventas outbound que automatiza tareas operativas, "
            "orquesta secuencias multicanal con IA y centraliza prospectos, "
            "campañas y reportes para equipos de ventas."
        ),
        "value_proposition": (
            "Automatiza entre 60% y 90% de tareas manuales de prospección outbound "
            "integrando email, LinkedIn y WhatsApp en un flujo único con IA, "
            "permitiendo que el SDR solo intervenga cuando el prospecto muestra interés real."
        ),
    }
    for variant in FIRST_TOUCH_VARIANTS:
        sections = build_first_touch_sections(
            channel="linkedin",
            variant=variant,
            prospect=prospect,
            campaign=campaign,
            product=product,
        )
        body = assemble_first_touch_body_from_sections(sections)
        low = body.lower()
        assert "hola ada" in low
        assert "joaquin" in low
        assert "costguard" in low
        assert "equipos comerciales a consolidar" not in low
        # No pegar ficha completa (VP + description concatenadas).
        assert "plataforma b2b de ventas outbound que automatiza tareas operativas" not in low
        assert "orquesta secuencias multicanal" not in low or len(body) < 420
        assert len(body) < 520
        assert "reunión" in low or "llamada" in low or "charla" in low or "meet" in low or "videollamada" in low or "call" in low
        assert "?" in body
        # Valor de ficha presente; no exigir reescritura «Con Nexus…» (banco usa {valor} literal).
        assert "automatiza entre 60%" in low or "nexus" in low
        assert "equipos comerciales a" not in low


def test_follow_up_bodies_end_with_quedo_atento():
    prospect = {"id": "9", "name": "Mia Alvarez", "company_name": "Acme", "role": "Head of Sales"}
    campaign = {"id": "2", "sender_name": "Joaquin", "brand_name": "CostGuard"}
    product = {"value_proposition": "Automatiza tareas manuales de prospección outbound."}
    for channel in ("email", "linkedin", "whatsapp"):
        for variant in FOLLOW_UP_VARIANTS:
            body = build_follow_up_body(
                channel=channel,
                variant=variant,
                prospect=prospect,
                campaign=campaign,
                product=product,
                step_day=10,
            )
            assert "Quedo atento" in body
            assert "no te molesto" not in body.lower()
