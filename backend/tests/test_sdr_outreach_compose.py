"""Tests compose playbook SDR para outreach real."""

from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK
from app.services.sdr_outreach_compose import resolve_playbook_step


def test_resolve_playbook_step_day1_email_sin_historial():
    step = resolve_playbook_step("email", [])
    assert step.day == 1
    assert step.channel == "email"


def test_resolve_playbook_step_linkedin_despues_email():
    prior = [{"day": 1, "channel": "email", "body": "Hola Juan,\nSoy Ana de Nexus..."}]
    step = resolve_playbook_step("linkedin", prior)
    assert step.day == 4
    assert step.channel == "linkedin"


def test_day1_objective_menciona_cta_conversacion():
    obj = DEFAULT_MVP_PLAYBOOK[0].objective.lower()
    assert "personalizado" in obj or "gancho" in obj
    assert "reunión" in obj or "reunion" in obj
    assert "cerrar venta" in obj or "nunca" in obj
