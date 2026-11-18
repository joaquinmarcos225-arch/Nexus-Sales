"""Regla 5: «Quedo atento» máx. 2× por secuencia + CTA de reunión casi siempre."""

from app.services.sequence_close_guard import (
    enforce_close_and_cta,
    extract_close_family_hits,
    prior_close_count,
    strip_close_family,
)
from app.services.sequence_cta_guard import extract_meeting_ctas


def test_extract_quedo_atento_family():
    body = "Hola.\n\n¿Hablamos un rato?\n\nQuedo atento."
    hits = extract_close_family_hits(body)
    assert len(hits) == 1
    assert "quedo atento" in hits[0].lower()


def test_strips_close_when_prior_already_at_cap():
    prior = [
        {"body": "Primero.\n\nQuedo atento."},
        {"body": "Segundo.\n\nQuedo atento a lo que te quede mejor."},
    ]
    assert prior_close_count(prior) == 2
    body = "Cierro el hilo.\n\n¿Agendamos 10 minutos?\n\nQuedo atento."
    out = enforce_close_and_cta(body=body, prior_touches=prior)
    assert not extract_close_family_hits(out)
    assert extract_meeting_ctas(out)


def test_keeps_close_when_under_cap():
    prior = [{"body": "Hola.\n\nQuedo atento."}]
    body = "Retomo.\n\n¿Te viene bien una videollamada breve?\n\nQuedo atento."
    out = enforce_close_and_cta(body=body, prior_touches=prior)
    assert extract_close_family_hits(out)
    assert extract_meeting_ctas(out)


def test_adds_meeting_cta_when_missing():
    prior = [{"body": "Hola.\n\nQuedo atento."}]
    body = "Retomo el hilo sobre el producto."
    out = enforce_close_and_cta(body=body, prior_touches=prior)
    assert extract_meeting_ctas(out)
    assert "retomo el hilo" in out.lower()


def test_strip_close_family_keeps_cta():
    body = "¿Coordinamos una llamada corta esta semana?\n\nQuedo atento."
    out = strip_close_family(body)
    assert "quedo atento" not in out.lower()
    assert "llamada" in out.lower()
