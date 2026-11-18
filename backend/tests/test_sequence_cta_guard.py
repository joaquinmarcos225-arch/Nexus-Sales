"""Regla 4: misma formulación de CTA máx. 2 veces por secuencia."""

from app.services.sequence_cta_guard import (
    enforce_cta_repeat_limit,
    extract_meeting_ctas,
    normalize_cta,
    prior_cta_counts,
)


def test_extract_meeting_cta_ignores_small_talk():
    body = "Hola Laura, ¿cómo estás?\n\n¿Coordinamos una llamada corta esta semana?"
    ctas = extract_meeting_ctas(body)
    assert len(ctas) == 1
    assert "llamada" in ctas[0].lower()


def test_allows_same_cta_twice():
    cta = "¿Hablamos un rato esta semana?"
    prior = [
        {"body": f"Hola.\n\n{cta}"},
        {"body": f"Retomo.\n\n{cta}"},
    ]
    body = f"Cierro por acá.\n\n{cta}"
    # used 2 times in prior → third must change
    out = enforce_cta_repeat_limit(body=body, prior_touches=prior)
    assert normalize_cta(cta) not in normalize_cta(extract_meeting_ctas(out)[-1])
    assert extract_meeting_ctas(out)


def test_keeps_cta_when_under_limit():
    cta = "¿Hablamos un rato esta semana?"
    prior = [{"body": f"Hola.\n\n{cta}"}]
    body = f"Retomo.\n\n{cta}"
    out = enforce_cta_repeat_limit(body=body, prior_touches=prior)
    assert extract_meeting_ctas(out)[-1] == cta


def test_prior_counts():
    cta = "¿Agendamos 10 minutos para ver si encaja?"
    prior = [{"body": cta}, {"body": f"x\n\n{cta}"}]
    counts = prior_cta_counts(prior)
    assert counts[normalize_cta(cta)] == 2
