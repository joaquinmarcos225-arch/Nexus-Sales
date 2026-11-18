"""Orden fijo: inicio+apalancamiento → info → CTA → cierre."""

from app.services.sequence_structure_order_guard import enforce_outbound_structure_order
from app.services.sequence_cta_guard import (
    enforce_one_meeting_cta_per_message,
    extract_meeting_ctas,
    extract_soft_meeting_ctas,
)


def test_reorders_and_drops_soft_cta_duplicate():
    body = (
        "Hola Laura,\n\n"
        "Retomo lo que te decía por WhatsApp.\n"
        "Si te sirve, avisame y armamos un espacio corto.\n\n"
        "Quedo atento.\n\n"
        "Suma capacidad en picos sin depender de una flota propia.\n\n"
        "¿Te viene mejor esta semana o la próxima para una llamada breve?"
    )
    out = enforce_outbound_structure_order(body)
    low = out.lower()
    assert "si te sirve" not in low
    assert "armamos un espacio corto" not in low or "¿" in out
    i_hola = low.find("hola laura")
    i_retomo = low.find("retomo")
    i_valor = low.find("suma capacidad")
    i_cta = low.find("¿te viene mejor")
    i_close = low.find("quedo atento")
    assert i_hola < i_retomo < i_valor < i_cta < i_close
    assert len(extract_meeting_ctas(out)) == 1
    assert not extract_soft_meeting_ctas(out)


def test_mail_day10_no_double_cta():
    body = (
        "Hola Laura,\n\n"
        "Retomo lo que te decía por LinkedIn. No quiero saturar. "
        "Si en algún momento querés explorarlo, coordinamos un espacio.\n\n"
        "La red de repartidores cubre la última milla por vos.\n\n"
        "¿Te viene mejor esta semana o la próxima para una llamada breve?"
    )
    out = enforce_outbound_structure_order(body)
    low = out.lower()
    assert "no quiero saturar" not in low
    assert "querés explorarlo" not in low and "queres explorarlo" not in low
    assert "coordinamos un espacio" not in low
    assert low.find("retomo") < low.find("red de repartidores")
    assert low.find("red de repartidores") < low.find("¿te viene mejor")
    assert len(extract_meeting_ctas(out)) == 1


def test_keeps_already_ordered():
    body = (
        "Hola Laura,\n\n"
        "Te había escrito por mail.\n\n"
        "La entrega puede salir el mismo día.\n\n"
        "¿Esta semana o la próxima para una llamada breve?\n\n"
        "Quedo atento."
    )
    out = enforce_outbound_structure_order(body)
    assert "te había escrito por mail" in out.lower()
    assert out.index("La entrega") < out.index("¿Esta semana")
    assert out.index("¿Esta semana") < out.index("Quedo atento")


def test_soft_plus_hard_becomes_one():
    body = (
        "Hola.\n\n"
        "Si te sirve, avisame y armamos un espacio corto.\n\n"
        "¿Agendamos 10 minutos para ver si encaja?"
    )
    out = enforce_one_meeting_cta_per_message(body)
    assert "si te sirve" not in out.lower()
    assert len(extract_meeting_ctas(out)) == 1
