"""Validación de copy SDR: personalización, sin culpa en follow-ups."""

from app.services.lead_sourcing.sdr_playbook_outreach import (
    _GUILT_FOLLOWUP_BANNED,
    _PAIN_ASSUMPTION_BANNED,
    _assemble_first_touch_body,
    _first_touch_value_paragraph,
    _has_followup_bridge,
    _validate_follow_up_body,
)


def test_guilt_phrases_banned():
    assert _GUILT_FOLLOWUP_BANNED.search("¿Pudiste leer mi mensaje anterior?")
    assert _GUILT_FOLLOWUP_BANNED.search("si pudiste revisar lo que te envié")
    assert not _GUILT_FOLLOWUP_BANNED.search("Paso rápido por aquí para dejar esto arriba")


def test_sectoral_problem_not_banned_as_guilt():
    text = (
        "Habitualmente, los directores de ventas en tu sector pierden horas buscando contactos. "
        "Ayudamos a empresas como la tuya a automatizar ese proceso."
    )
    assert not _PAIN_ASSUMPTION_BANNED.search(text)
    assert _PAIN_ASSUMPTION_BANNED.search("seguramente te pasa esto todos los días")


def test_assemble_first_touch_includes_problem_and_solution():
    body = _assemble_first_touch_body(
        {
            "greeting": "Hola Ana,",
            "presentation": "Soy Joaquin. Vi que Acme sigue creciendo.",
            "problem": "Habitualmente los equipos pierden horas en prospección manual",
            "solution": "Ayudamos a empresas como Acme a automatizar ese proceso",
            "benefits": "",
            "cta": "¿Tendrías 10 minutos esta semana para una llamada corta?",
        }
    )
    assert "Vi que Acme" in body
    assert "Habitualmente" in body
    assert "Ayudamos" in body
    assert "10 minutos" in body


def test_value_paragraph_joins_problem_solution():
    para = _first_touch_value_paragraph(
        {
            "problem": "Habitualmente pierden tiempo",
            "solution": "Ayudamos a recortar ese tiempo",
            "benefits": "",
        }
    )
    assert "Habitualmente" in para
    assert "Ayudamos" in para


def test_followup_bridge_accepts_new_phrases():
    assert _has_followup_bridge("Te escribo brevemente para dejar esto arriba en tu bandeja.")
    assert _has_followup_bridge("Paso rápido por aquí.")
    assert _has_followup_bridge("Olvidé mencionarte que ayudamos a equipos similares.")


def test_li_and_wa_first_touch_prefer_paragraphs():
    """LinkedIn y WhatsApp: párrafos; WA más corto / LI puede desarrollar más."""
    from app.services.cold_message_bank import render_cold_bank_touch

    prospect = {
        "id": 501,
        "name": "Ana Lopez",
        "company_name": "Acme SA",
        "role": "CEO",
    }
    campaign = {
        "id": 9,
        "sender_name": "Joaquin",
        "brand_name": "CostGuard",
        "outreach_mode": "b2b",
    }
    product = {
        "name": "Nexus Sales",
        "value_proposition": (
            "Automatizamos el outreach multicanal para agendar más reuniones "
            "con menos trabajo manual"
        ),
    }
    li = render_cold_bank_touch(
        channel="linkedin",
        prospect=prospect,
        campaign=campaign,
        product=product,
        first_touch=True,
    )
    wa = render_cold_bank_touch(
        channel="whatsapp",
        prospect=prospect,
        campaign=campaign,
        product=product,
        first_touch=True,
    )
    assert "\n\n" in li.body
    assert "\n\n" in wa.body
    assert len(wa.body) < len(li.body) or len(wa.body.split()) <= len(li.body.split())


def test_validate_day13_rejects_guilt_accepts_bridge():
    bad = (
        "Hola Ana, ¿cómo estás?\n"
        "Retomo para saber si pudiste revisar lo que te envié sobre automatizar.\n"
        "¿Coordinamos esta semana?\nSaludos."
    )
    acc_bad = _validate_follow_up_body(bad, step_day=13, channel="linkedin")
    assert any("reproche" in i.lower() or "prohibido" in i.lower() for i in acc_bad.issues)

    good = (
        "Hola Ana,\n"
        "Paso rápido por aquí para dejar esto arriba.\n"
        "Olvidé comentarte que con equipos similares logramos un 30% más de reuniones.\n"
        "¿Tendrías 10 minutos esta semana para ver si aplica?\n"
        "Saludos."
    )
    acc_good = _validate_follow_up_body(good, step_day=13, channel="linkedin")
    assert not any("reproche" in i.lower() for i in acc_good.issues)
