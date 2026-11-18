"""Sin pricing en ficha: no inventar; llevar a call (o insist → honestidad)."""

from app.services.linkedin_reply_compose import (
    build_price_unknown_email_reply,
    inbound_insists_price_before_meeting,
    looks_like_invented_pricing,
    price_asked,
    product_has_pricing_facts,
)
from app.services.outbound_text_normalize import normalize_outbound_email_body


def test_price_asked_and_no_facts():
    q = "Dale, y el pricing? Cómo se cobra con un equipo chico de 3–4 SDRs?"
    assert price_asked(q) is True
    assert product_has_pricing_facts("Automatiza 60% a 90% de tareas") is False


def test_unknown_price_reply_defers_to_call():
    body = build_price_unknown_email_reply(insist=False, team_hint="3–4 SDRs")
    out = normalize_outbound_email_body(body)
    assert "buena pregunta" not in out.lower()
    assert out.lower().startswith("el pricing")
    assert "usuario activo" not in out.lower()
    assert "call" in out.lower() or "números" in out.lower()
    assert "\n\n" in out


def test_insist_still_no_invent():
    q = "Necesito el precio primero antes de la reunión"
    assert inbound_insists_price_before_meeting(q) is True
    body = build_price_unknown_email_reply(insist=True, team_hint="3–4 SDRs")
    assert "invent" in body.lower() or "no te paso un precio cerrado" in body.lower()
    assert "$" not in body
    assert "por usuario" not in body.lower()


def test_detects_invented_pricing_copy():
    assert looks_like_invented_pricing(
        "el cobro se realiza por usuario activo mensual. Cada licencia incluye…"
    )
    assert not looks_like_invented_pricing(
        "El pricing lo armamos según tamaño del equipo. ¿Lo vemos en una call?"
    )
