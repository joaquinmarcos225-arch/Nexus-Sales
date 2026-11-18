"""Regla 7: cada dato/ángulo de valor una sola vez con esa persona."""

from app.services.sequence_value_once_guard import (
    enforce_value_said_once,
    extract_value_sentences,
    is_value_sentence,
)


def test_detects_value_with_metric():
    sent = (
        "Con esto automatizamos entre 60% y 90% de las tareas manuales "
        "de prospección outbound."
    )
    assert is_value_sentence(sent)


def test_greeting_is_not_value():
    assert not is_value_sentence("Hola Laura, ¿cómo estás?")


def test_strips_repeated_value_keeps_new_and_cta():
    prior_value = (
        "Automatizamos entre 60% y 90% de las tareas manuales de prospección "
        "integrando email, LinkedIn y WhatsApp."
    )
    prior = [
        {
            "body": (
                f"Hola Ana.\n\nSoy Joaquin de CostGuard. {prior_value}\n\n"
                "¿Agendamos 10 minutos?"
            )
        }
    ]
    body = (
        f"Hola Ana,\n\nte había escrito la semana pasada.\n\n{prior_value}\n\n"
        "Además centralizamos prospectos y campañas en un solo flujo.\n\n"
        "¿Te viene bien una videollamada breve?"
    )
    out = enforce_value_said_once(
        body=body,
        prior_touches=prior,
        sequence_first_touch=False,
    )
    low = out.lower()
    assert "60%" not in out and "90%" not in out
    assert "centralizamos prospectos" in low
    assert "videollamada" in low or "?" in out
    assert "te había escrito" in low


def test_keeps_first_touch_unchanged():
    body = "Hola.\n\nAutomatizamos el 80% del outreach manual.\n\n¿Hablamos?"
    out = enforce_value_said_once(
        body=body,
        prior_touches=[{"body": "x"}],
        sequence_first_touch=True,
    )
    assert out == body.strip()


def test_extract_value_from_prior():
    body = (
        "Hola.\n\n"
        "Permitimos orquestar secuencias multicanal con IA.\n\n"
        "¿Coordinamos una llamada corta esta semana?\n\n"
        "Quedo atento."
    )
    vals = extract_value_sentences(body)
    assert vals
    assert any("orquestar" in v.lower() for v in vals)
