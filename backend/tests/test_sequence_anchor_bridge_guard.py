"""Regla 3b: puente ancla → valor en primer toque."""

from app.services.sequence_anchor_bridge_guard import (
    enforce_anchor_value_bridge,
    has_disconnected_anchor_value,
)


def test_detects_dry_cut():
    body = (
        "Hola Laura, ¿cómo estás?\n\n"
        "Soy Javier de Uber.\n\n"
        "Vi que sos Gerente de Operaciones de Andes Retail.\n\n"
        "Con Uber Direct habilitamos entregas same-day sin flota propia.\n\n"
        "¿Agendamos 10 minutos?"
    )
    assert has_disconnected_anchor_value(body)


def test_bridges_disconnected_paras():
    body = (
        "Hola Laura, ¿cómo estás?\n\n"
        "Soy Javier de Uber.\n\n"
        "Vi que sos Gerente de Operaciones de Andes Retail.\n\n"
        "Con Uber Direct habilitamos entregas same-day sin flota propia.\n\n"
        "¿Agendamos 10 minutos?"
    )
    out = enforce_anchor_value_bridge(
        body=body,
        sequence_first_touch=True,
        seed="1",
    )
    assert not has_disconnected_anchor_value(out)
    low = out.lower()
    assert "vi que sos" in low
    assert "uber direct" in low or "habilitamos" in low
    # No debe quedar ancla sola como párrafo seguido de «Con …» suelto.
    assert "\n\nCon Uber Direct" not in out
    assert "\n\ncon uber direct" not in out.lower()


def test_skips_on_followup():
    body = (
        "Vi que sos Gerente de Andes.\n\n"
        "Con Uber Direct habilitamos entregas."
    )
    out = enforce_anchor_value_bridge(
        body=body,
        sequence_first_touch=False,
        seed="1",
    )
    assert out == body.strip()
