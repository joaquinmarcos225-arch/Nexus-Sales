"""Regla 9: asunto email con valor del producto."""

from app.services.sequence_email_subject_guard import (
    build_value_email_subject,
    enforce_value_email_subject,
    is_weak_email_subject,
    short_value_subject_hook,
    subject_has_value_signal,
)

_NEXUS = {
    "name": "Nexus Sales",
    "value_proposition": (
        "Automatiza entre 60% y 90% de tareas manuales de prospección outbound "
        "integrando email, LinkedIn y WhatsApp."
    ),
    "description": "plataforma B2B de ventas outbound",
}


def test_hook_from_nexus_vp():
    hook = short_value_subject_hook(_NEXUS)
    assert "automatiz" in hook.lower() or "outbound" in hook.lower() or "tarea" in hook.lower()


def test_propuesta_breve_para_company_is_weak():
    assert is_weak_email_subject("Propuesta breve para Andes Retail")


def test_value_subject_is_strong():
    assert not is_weak_email_subject("Automatizá tu outbound")
    assert not is_weak_email_subject("Propuesta breve: Automatizá tu outbound")
    assert subject_has_value_signal("Automatizá tu outbound")


def test_enforce_rewrites_generic():
    out = enforce_value_email_subject(
        subject="Propuesta breve para Andes Retail",
        product=_NEXUS,
        company="Andes Retail",
        seed="1",
        sequence_first_touch=True,
        channel="email",
    )
    assert out
    assert "propuesta breve para andes" not in out.lower()
    assert subject_has_value_signal(out) or "propuesta breve:" in out.lower()


def test_keeps_re_on_followup():
    out = enforce_value_email_subject(
        subject="Re: Automatizá tu outbound",
        product=_NEXUS,
        company="Andes",
        seed="2",
        sequence_first_touch=False,
        channel="email",
    )
    assert out.lower().startswith("re:")


def test_build_subject_includes_hook():
    subj = build_value_email_subject(product=_NEXUS, company="Acme", seed="x")
    assert subj
    assert len(subj) <= 72
