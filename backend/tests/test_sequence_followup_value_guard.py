"""FU intermedios deben traer ángulo de valor nuevo (salvo último/breakup)."""

from app.services.sequence_followup_value_guard import (
    body_has_fresh_value,
    enforce_followup_value_angle,
    is_last_followup,
)

_PRODUCT = {
    "name": "Uber Direct",
    "value_proposition": (
        "Habilitamos entregas locales el mismo día con la red de repartidores de Uber, "
        "vendiendo desde el canal propio del negocio, sin flota propia."
    ),
    "description": (
        "Uber Direct permite entregas same-day / última milla usando la red de Uber "
        "mientras el comercio vende desde su propio canal."
    ),
}


def test_last_followup_by_day():
    assert is_last_followup(step_day=16)
    assert not is_last_followup(step_day=4)


def test_injects_value_into_empty_fu():
    prior = [
        {
            "body": (
                "Hola.\n\nCon Uber Direct habilitamos entregas same-day sin flota propia "
                "manteniendo la venta en tu canal.\n\n¿Hablamos?"
            )
        }
    ]
    body = "Hola Laura,\n\nTe había escrito por LinkedIn.\n\n¿Agendamos 10 minutos?"
    assert not body_has_fresh_value(body, prior_touches=prior)
    out = enforce_followup_value_angle(
        body=body,
        product=_PRODUCT,
        prior_touches=prior,
        sequence_first_touch=False,
        step_day=4,
    )
    assert body_has_fresh_value(out, prior_touches=prior)
    assert "te había escrito" in out.lower()
    assert "?" in out


def test_skips_last_touch():
    body = "Hola.\n\nRetomo.\n\n¿Agendamos 10 minutos?"
    out = enforce_followup_value_angle(
        body=body,
        product=_PRODUCT,
        prior_touches=[{"body": "x"}],
        sequence_first_touch=False,
        step_day=16,
    )
    assert out == body.strip()
