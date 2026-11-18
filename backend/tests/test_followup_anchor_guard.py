"""Regla 3: follow-ups no pueden re-anclar con «Vi que sos…»."""

from app.services.followup_anchor_guard import (
    enforce_no_role_anchor_on_followup,
    followup_has_forbidden_role_anchor,
    strip_followup_role_anchors,
)


def test_detects_vi_que_sos():
    assert followup_has_forbidden_role_anchor(
        "Hola.\n\nVi que sos Gerente de Andes Retail.\n\n¿Hablamos?"
    )


def test_strip_removes_anchor_keeps_rest():
    raw = (
        "Hola Laura,\n\n"
        "Soy Javier de Uber. Vi que sos Gerente de Operaciones de Andes Retail.\n\n"
        "Te había escrito por mail.\n\n¿Hablamos un rato?"
    )
    out = strip_followup_role_anchors(raw)
    assert "Vi que sos" not in out
    assert "Te había escrito" in out
    assert "¿Hablamos" in out


def test_enforce_keeps_first_touch():
    body = "Soy Javier de Uber. Vi que sos Gerente de Andes Retail."
    assert "Vi que sos" in enforce_no_role_anchor_on_followup(
        body=body, sequence_first_touch=True
    )


def test_enforce_strips_followup():
    body = "Soy Javier de Uber. Vi que sos Gerente de Andes Retail.\n\nRetomo el hilo."
    out = enforce_no_role_anchor_on_followup(body=body, sequence_first_touch=False)
    assert "Vi que sos" not in out
    assert "Retomo" in out
