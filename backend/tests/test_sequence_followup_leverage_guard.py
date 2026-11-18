"""Todo FU debe apalancarse del mensaje/canal anterior."""

from app.services.sequence_followup_leverage_guard import (
    enforce_followup_prior_leverage,
    has_prior_leverage,
)
from app.services.sequence_cta_guard import (
    enforce_one_meeting_cta_per_message,
    extract_meeting_ctas,
)


def test_injects_retomo_when_missing():
    prior = [{"channel": "linkedin", "body": "Hola previa"}]
    body = (
        "Hola Laura,\n\n"
        "La entrega puede salir el mismo día.\n\n"
        "¿Esta semana o la próxima para una llamada breve?"
    )
    assert not has_prior_leverage(body)
    out = enforce_followup_prior_leverage(
        body=body,
        prior_touches=prior,
        sequence_first_touch=False,
    )
    assert "te había escrito por linkedin" in out.lower()
    assert "hola laura" in out.lower()


def test_keeps_existing_leverage():
    prior = [{"channel": "email", "body": "x"}]
    body = "Hola.\n\nTe había escrito por mail.\n\n¿Agendamos 10 minutos?"
    out = enforce_followup_prior_leverage(
        body=body,
        prior_touches=prior,
        sequence_first_touch=False,
    )
    assert out == body.strip()


def test_never_two_meeting_ctas():
    body = (
        "Hola.\n\n"
        "Te había escrito por LinkedIn.\n\n"
        "¿Te viene bien una conversación breve esta semana o la próxima?\n\n"
        "¿Te viene mejor esta semana o la próxima para una llamada breve?"
    )
    assert len(extract_meeting_ctas(body)) == 2
    out = enforce_one_meeting_cta_per_message(body)
    assert len(extract_meeting_ctas(out)) == 1
