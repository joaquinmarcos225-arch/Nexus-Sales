"""Sin pricing en ficha: no inventar; call. Si insiste: honestidad + agenda."""

from app.services.linkedin_reply_compose import (
    build_price_unknown_email_reply,
    inbound_insists_price_before_meeting,
    looks_like_invented_pricing,
    price_asked,
    product_has_pricing_facts,
)
from app.services.openai_service import generate_gmail_draft_email


def test_price_asked_and_no_facts():
    q = "Dale, y el pricing? Cómo se cobra con un equipo chico de 3–4 SDRs?"
    assert price_asked(q) is True
    assert product_has_pricing_facts("Automatiza 60% a 90% de tareas") is False
    assert inbound_insists_price_before_meeting(q) is False
    assert inbound_insists_price_before_meeting(
        "Necesito el precio primero, sin eso no agendo"
    )


def test_insist_numero_antes_de_agendar():
    q = "Necesito el numero antes de agendar"
    assert price_asked(q) is True
    assert inbound_insists_price_before_meeting(q) is True
    from app.services.competitor_contrast import (
        extract_competitor_name,
        sanitize_competitor_hint,
    )
    from app.services.nexus_brain.router import plan_specialists

    assert extract_competitor_name(q) == ""
    assert sanitize_competitor_hint("precio Nexus Sales") == ""
    assert sanitize_competitor_hint("Agendar") == ""
    plan = plan_specialists(q)
    assert plan.intent == "pricing"
    assert plan.need_research is False


def test_invented_pricing_heuristic():
    assert looks_like_invented_pricing(
        "el cobro se realiza por usuario activo mensual. Cada licencia incluye…"
    )
    assert not looks_like_invented_pricing(
        "El pricing lo armamos según tamaño; ¿lo vemos en una call?"
    )


def test_generate_gmail_uses_deterministic_price_reply():
    subj, body = generate_gmail_draft_email(
        prospect={"name": "Test 1", "company_name": "Empresa Test1", "role": "SDR"},
        campaign={"name": "Camp", "tone": "profesional"},
        product={
            "name": "Nexus Sales",
            "value_proposition": "Automatiza prospección outbound",
            "description": "Plataforma B2B",
            "target_notes": "SDRs",
        },
        tone="profesional",
        education="Sin datos de precio",
        conversation_history=[
            {"direction": "outbound", "message": "Hola"},
            {"direction": "inbound", "message": "pricing?"},
        ],
        last_prospect_inbound=(
            "Dale, y el pricing? Cómo se cobra con un equipo chico de 3–4 SDRs y qué incluye?"
        ),
        prospect_substantive_questions=True,
    )
    assert subj == "Pricing"
    low = body.lower()
    assert "usuario activo mensual" not in low
    assert "licencia" not in low
    assert "call" in low or "números" in low or "numeros" in low
    assert "Buena pregunta.," not in body
    insist = build_price_unknown_email_reply(insist=True, team_hint="3–4 SDRs")
    assert "antes de agendar" in insist.lower()
    assert "invent" in insist.lower() or "cifra" in insist.lower()
