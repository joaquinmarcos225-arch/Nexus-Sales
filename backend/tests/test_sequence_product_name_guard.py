"""Regla 10: no martillar el nombre del producto."""

from app.services.sequence_product_name_guard import (
    enforce_product_name_limit,
    limit_product_name_in_body,
    mention_count,
)


def test_limits_to_one_mention_in_first_touch():
    body = (
        "Hola Edgardo.\n\n"
        "Soy Javier de Uber. Con Uber Direct habilitamos entregas same-day.\n\n"
        "Te había escrito sobre Uber Direct. ¿Agendamos 10 minutos?"
    )
    out = enforce_product_name_limit(
        body=body,
        product_name="Uber Direct",
        prior_touches=[],
        sequence_first_touch=True,
        brand="Uber",
    )
    assert mention_count(out, ["Uber Direct"]) == 1
    assert "uber" in out.lower()  # marca en presentación OK


def test_followup_strips_when_prior_already_named():
    prior = [
        {
            "body": (
                "Hola.\n\nCon Uber Direct habilitamos entregas same-day "
                "sin flota propia.\n\n¿Hablamos?"
            )
        }
    ]
    body = (
        "Te había escrito por LinkedIn sobre Uber Direct.\n\n"
        "Si te sirve una llamada corta sobre Uber Direct, avisame."
    )
    out = enforce_product_name_limit(
        body=body,
        product_name="Uber Direct",
        prior_touches=prior,
        sequence_first_touch=False,
        brand="Uber",
    )
    assert mention_count(out, ["Uber Direct"]) == 0
    assert "te había escrito" in out.lower() or "llamada" in out.lower()


def test_limit_helper_keeps_first():
    body = "Con Nexus Sales automatizamos. Luego Nexus Sales otra vez."
    out = limit_product_name_in_body(body, product_name="Nexus Sales", max_mentions=1)
    assert mention_count(out, ["Nexus Sales"]) == 1
    assert out.lower().startswith("con nexus sales")
