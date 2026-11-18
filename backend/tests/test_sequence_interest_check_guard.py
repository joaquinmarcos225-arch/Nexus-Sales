"""Regla 6: prohibido medir interés residual («¿seguís interesad@?»)."""

from app.services.sequence_cta_guard import extract_meeting_ctas
from app.services.sequence_interest_check_guard import (
    enforce_no_interest_check,
    has_interest_check,
)


def test_detects_seguis_interesada():
    assert has_interest_check("Hola.\n\n¿Seguís interesada?\n\nQuedo atento.")


def test_detects_seguis_con_ganas():
    assert has_interest_check("¿Seguís con ganas de verlo?")


def test_allows_meeting_cta_with_interesaria():
    body = "Hola.\n\n¿Te interesaría coordinar una reunión breve para ver si encaja?"
    assert not has_interest_check(body)


def test_strips_and_adds_meeting_cta():
    body = "Hola Laura,\n\nte había escrito por CostGuard.\n\n¿Seguís con ganas de explorarlo?\n\nQuedo atento."
    out = enforce_no_interest_check(body=body, prior_touches=[])
    assert not has_interest_check(out)
    assert extract_meeting_ctas(out)
    assert "laura" in out.lower()


def test_strips_todavia_te_interesa():
    body = "Retomo.\n\n¿Todavía te interesa ver el producto?"
    out = enforce_no_interest_check(body=body, prior_touches=[])
    assert not has_interest_check(out)
    assert extract_meeting_ctas(out)


def test_strips_si_seguis_interesada_clause():
    body = "Si seguís interesada, armamos 10 minutos esta semana."
    out = enforce_no_interest_check(body=body, prior_touches=[])
    assert "seguís interesada" not in out.lower()
    assert extract_meeting_ctas(out) or "10 minutos" in out.lower()
