"""Tests de copy del fallback SDR (sin OpenAI)."""

import json

from app.services.message_structure_variants import pick_first_touch_variant
from app.services.openai_fallback import (
    _build_day1_sections,
    _build_problem_line,
    _value_proposition_benefit,
    build_sdr_playbook_fallback_json,
)


def test_value_proposition_converts_consolida_to_infinitive():
    product = {"value_proposition": "Consolida prospectos, campañas y reporting en un solo lugar."}
    assert _value_proposition_benefit(product) == (
        "consolidar prospectos, campañas y reporting en un solo lugar"
    )


def test_problem_line_no_duplicate_ayudamos_and_no_fixed_audience():
    product = {"value_proposition": "Consolida prospectos, campañas y reporting en un solo lugar."}
    line = _build_problem_line(product)
    assert "ayudamos a ayudamos" not in line.lower()
    assert "equipos comerciales" not in line.lower()
    assert "consolida" in line.lower() or "consolidar" in line.lower()


def test_day1_uses_sender_and_company_not_campaign_name():
    prospect = {
        "id": "21",
        "name": "Ana López",
        "company_name": "Acme Corp",
        "role": "directores de ventas",
    }
    campaign = {
        "id": "3",
        "sender_name": "Joaquin",
        "brand_name": "CostGuard",
        "name": "Outband LATAM Q2",
    }
    product = {
        "name": "Plataforma Nexus",
        "description": (
            "Automatiza la búsqueda y el contacto por Mail, WhatsApp y LinkedIn "
            "desde un solo lugar."
        ),
        "value_proposition": (
            "Consolida prospectos, campañas y reporting en un solo lugar."
        ),
    }
    sections = _build_day1_sections(
        prospect=prospect,
        campaign=campaign,
        product=product,
        channel="linkedin",
    )
    bank_body = (sections.get("_bank_body") or "").strip()
    assert bank_body.startswith("Hola Ana")
    assert "Joaquin" in bank_body
    # Marca del vendedor (no nombre de campaña).
    assert "Outband LATAM Q2" not in bank_body
    assert "equipos comerciales a consolidar" not in bank_body.lower()
    assert "?" in bank_body
    joined = bank_body.lower()
    assert "consolida" in joined or "plataforma nexus" in joined or "automatiza" in joined or "costguard" in joined


def test_day1_fallback_body_has_core_blocks():
    prospect = {"id": "22", "name": "Ana López", "company_name": "Acme", "role": "VP Sales"}
    campaign = {
        "id": "4",
        "sender_name": "Joaquin",
        "brand_name": "CostGuard",
        "name": "Campaña X",
    }
    raw = build_sdr_playbook_fallback_json(
        channel="linkedin",
        prospect=prospect,
        product={
            "name": "Plataforma Nexus",
            "description": (
                "Automatiza la búsqueda y el contacto por Mail, WhatsApp y LinkedIn "
                "desde un solo lugar."
            ),
            "value_proposition": "Consolida prospectos, campañas y reporting en un solo lugar.",
        },
        step_day=1,
        step_objective="primer contacto",
        campaign=campaign,
    )
    body = json.loads(raw)["body"]
    low = body.lower()
    assert "Hola Ana" in body
    assert "joaquin" in low
    assert "campaña x" not in low
    assert "equipos comerciales a consolidar" not in low
    assert "?" in body
    assert "consolida" in low or "plataforma nexus" in low or "automatiza" in low or "vp sales" in low


def test_fallback_json_first_touch_email():
    raw = build_sdr_playbook_fallback_json(
        channel="email",
        prospect={"id": "30", "name": "Ana López", "company_name": "Acme Corp"},
        campaign={"id": "5", "sender_name": "Joaquin", "brand_name": "CostGuard"},
        product={
            "name": "Plataforma Nexus",
            "value_proposition": (
                "Automatiza entre un 60% y un 90% de las tareas manuales de prospección outbound."
            ),
            "description": (
                "Automatiza la búsqueda y el contacto por Mail, WhatsApp y LinkedIn "
                "desde un solo lugar."
            ),
        },
        step_day=1,
        step_objective="primer contacto",
    )
    data = json.loads(raw)
    assert "Joaquin" in raw
    assert "CostGuard" in raw
    assert "Acme Corp" in raw
    assert "Demo" not in raw
    # Subject del banco (ya no hardcodea «Automatización de prospección»).
    assert data.get("subject")
    assert "automatización de prospección para" not in str(data.get("subject") or "").lower()
    assert "equipos comerciales a" not in raw.lower()
    assert "pudiste" not in raw.lower()
    assert "?" in raw


def test_fallback_followup_day13_no_guilt_quedo_atento():
    # Mismo canal LinkedIn previo → follow-up bank (reabre, no culpa).
    raw = build_sdr_playbook_fallback_json(
        channel="linkedin",
        prospect={"id": "31", "name": "Ana López", "company_name": "Acme Corp"},
        campaign={"id": "6", "sender_name": "Joaquin", "brand_name": "CostGuard"},
        product={"name": "Plataforma Nexus", "value_proposition": "Automatiza prospección."},
        step_day=13,
        step_objective="seguimiento",
        prior_touches=[
            {"day": 4, "channel": "linkedin", "body": "Hola Ana, soy Joaquin..."},
        ],
    )
    low = raw.lower()
    assert "pudiste" not in low
    assert "revisaste" not in low
    assert "quedo atento" in low
    assert "no te molesto" not in low
