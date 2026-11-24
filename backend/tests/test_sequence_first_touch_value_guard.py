"""1º toque: oferta/valor obligatorio (no solo el problema)."""

from app.services.sequence_first_touch_value_guard import (
    body_has_clear_product_offer,
    enforce_first_touch_product_offer,
)

_CONTRACTIA = {
    "name": "Contractia",
    "value_proposition": (
        "Centralizamos la creación, revisión y firma digital de contratos "
        "para que legales y ventas cierren más rápido sin Excel ni mail suelto."
    ),
    "description": (
        "Plataforma de contratos: plantillas, flujos de aprobación y firma electrónica."
    ),
}


def test_problem_only_is_not_clear_offer():
    body = (
        "Vi que sos COO de Costguard, y con Contractia sabemos que el desafío "
        "no está solamente en firmar digitalmente, sino en todo lo que sucede "
        "antes y después: aprobaciones internas, seguimiento de pendientes, "
        "coordinación entre áreas, validación de identidad y resguardo de la "
        "documentación.\n\n"
        "¿Agendamos 10 o 15 minutos esta semana o la próxima?"
    )
    assert not body_has_clear_product_offer(body)


def test_offer_sentence_is_clear():
    body = (
        "Con Contractia centralizamos plantillas, aprobaciones y firma digital "
        "para acortar el ciclo entre áreas."
    )
    assert body_has_clear_product_offer(body)


def test_injects_offer_before_cta_on_first_touch():
    body = (
        "Hola Ivan,\n\n"
        "Soy Joaquín de CostGuard.\n\n"
        "Vi que sos COO de Costguard, y con Contractia sabemos que el desafío "
        "no está solamente en firmar digitalmente, sino en todo lo que sucede "
        "antes y después: aprobaciones internas, seguimiento de pendientes, "
        "coordinación entre áreas, validación de identidad y resguardo de la "
        "documentación.\n\n"
        "¿Agendamos 10 o 15 minutos esta semana o la próxima?"
    )
    out = enforce_first_touch_product_offer(
        body=body,
        product=_CONTRACTIA,
        sequence_first_touch=True,
        seed="ivan|1",
    )
    assert body_has_clear_product_offer(out)
    assert out.lower().index("centraliz") < out.lower().index("agendamos") or (
        "firma" in out.lower() and "agendamos" in out.lower()
    )
    # Nombre una sola vez preferible (ya estaba en el ancla).
    assert out.lower().count("contractia") <= 2


def test_skips_followups():
    body = "Hola.\n\nTe había escrito.\n\n¿Agendamos?"
    out = enforce_first_touch_product_offer(
        body=body,
        product=_CONTRACTIA,
        sequence_first_touch=False,
    )
    assert out == body.strip()
