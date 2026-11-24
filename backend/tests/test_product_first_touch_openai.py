"""1º toque desde ficha de producto (OpenAI) — cualquier producto, no bank Nexus."""

from __future__ import annotations

import json

from app.services.lead_sourcing.sdr_playbook_outreach import (
    _body_looks_like_wrong_product_pitch,
    _product_for_outreach_copy,
    generate_sdr_playbook_touch,
)
from app.services.outreach_display_names import product_outreach_display_name
from app.services.sequence_email_subject_guard import short_value_subject_hook


_CONTRACTIA = {
    "name": "Contractia | Software de gestión y firma de contratos",
    "value_proposition": (
        "Centralizamos la creación, revisión y firma digital de contratos "
        "para que legales y ventas cierren más rápido sin Excel ni mail suelto."
    ),
    "description": (
        "Plataforma de contratos: plantillas, flujos de aprobación y firma electrónica."
    ),
    "pain_points": "Contratos dispersos, demoras en firmas, poca trazabilidad.",
    "benefits": "Ciclos más cortos, trazabilidad y menos fricción entre áreas.",
}


def test_product_outreach_display_name_strips_pipe():
    assert (
        product_outreach_display_name(
            "Contractia | Software de gestión y firma de contratos"
        )
        == "Contractia"
    )


def test_product_for_outreach_copy_keeps_full_name():
    out = _product_for_outreach_copy(_CONTRACTIA)
    assert out["name"] == "Contractia"
    assert "Software" in out["full_name"]


def test_contractia_subject_hook_not_nexus_outbound():
    hook = short_value_subject_hook(_CONTRACTIA)
    low = hook.lower()
    assert "outbound" not in low
    assert "prospecci" not in low
    assert "tareas manuales" not in low
    assert "contrat" in low or "firma" in low or "centraliz" in low or "ciclos" in low


def test_wrong_product_pitch_detector():
    assert _body_looks_like_wrong_product_pitch(
        "Con Nexus Sales bajamos las tareas manuales de outbound.",
        product={"name": "Contractia", "value_proposition": "Firmas digitales"},
    )
    assert not _body_looks_like_wrong_product_pitch(
        "Con Contractia acortamos el ciclo de firma de contratos.",
        product=_CONTRACTIA,
    )


def test_ensure_product_named_once_injects():
    from app.services.lead_sourcing.sdr_playbook_outreach import (
        _ensure_short_product_named_once,
    )

    body = "Hola Ana,\n\nSoy J de CostGuard.\n\nNosotros firmamos más rápido.\n\n¿Agendamos?"
    out = _ensure_short_product_named_once(body, product_name="Contractia")
    assert out.lower().count("contractia") == 1
    assert "Con Contractia" in out


def test_openai_first_touch_uses_product_fiche_not_bank(monkeypatch):
    payload = {
        "internal": {
            "probable_problem": "Contratos lentos entre legales y ventas",
            "why_it_matters": "Rol comercial necesita cerrar más rápido",
            "hypothesis": "Firma digital y flujos en un solo lugar",
            "response_question": "¿Agendamos 10 o 15 minutos esta semana?",
            "selling_to_role": "Gerente Comercial",
        },
        "sections": {
            "greeting": "Hola Eduardo,",
            "presentation": "Soy Joaquín de CostGuard. Vi que sos Gerente Comercial de Acme,",
            "problem": "y suele costar avanzar contratos entre áreas.",
            "solution": (
                "Con Contractia centralizamos plantillas, aprobaciones y firma digital "
                "para acortar el ciclo sin Excel ni mails sueltos."
            ),
            "benefits": "",
            "cta": "¿Te sirve una llamada de 10 o 15 minutos esta semana o la próxima?",
        },
        "subject": "Acelerá la firma de contratos",
        "body": (
            "Hola Eduardo,\n\n"
            "Soy Joaquín de CostGuard. Vi que sos Gerente Comercial de Acme, "
            "y suele costar avanzar contratos entre áreas.\n\n"
            "Con Contractia centralizamos plantillas, aprobaciones y firma digital "
            "para acortar el ciclo sin Excel ni mails sueltos.\n\n"
            "¿Te sirve una llamada de 10 o 15 minutos esta semana o la próxima?\n\n"
            "Saludos,\nJoaquín"
        ),
    }

    monkeypatch.setattr(
        "app.services.openai_service.openai_configured", lambda: True
    )
    monkeypatch.setattr(
        "app.services.openai_service._chat",
        lambda *a, **k: json.dumps(payload),
    )

    subject, body, reason = generate_sdr_playbook_touch(
        channel="email",
        prospect={
            "id": "edu-1",
            "name": "Eduardo Pérez",
            "company_name": "Acme",
            "role": "Gerente Comercial",
        },
        campaign={
            "id": "c1",
            "sender_name": "Joaquín",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
            "tone": "profesional cercano",
        },
        product=_CONTRACTIA,
        education="",
        step_day=1,
        step_objective="primer toque",
        prior_touches=[],
    )

    assert reason.bank_template_id == "openai_product_first_touch"
    assert subject
    assert "outbound" not in (subject or "").lower()
    assert "Contractia" in body
    assert "Nexus" not in body
    assert "Software de gestión" not in body  # sin título largo con |
    assert "tareas manuales" not in body.lower()


def test_first_touch_falls_back_to_bank_without_openai(monkeypatch):
    monkeypatch.setattr(
        "app.services.openai_service.openai_configured", lambda: False
    )
    subject, body, reason = generate_sdr_playbook_touch(
        channel="email",
        prospect={
            "id": "edu-2",
            "name": "Eduardo Pérez",
            "company_name": "Acme",
            "role": "Gerente Comercial",
        },
        campaign={
            "id": "c1",
            "sender_name": "Joaquín",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
        },
        product=_CONTRACTIA,
        education="",
        step_day=1,
        step_objective="primer toque",
        prior_touches=[],
    )
    assert body.strip()
    assert subject
    assert "outbound" not in (subject or "").lower()
    assert "Contractia" in body or "contrato" in body.lower()
    assert "Nexus" not in body
    assert "prospección outbound" not in body.lower()
    tid = (reason.bank_template_id or "").lower()
    assert "openai" not in tid


def test_bank_nexus_leak_is_rewritten_for_contractia(monkeypatch):
    """Si el banco (o Mi estilo) cuela Nexus, el candado rearma desde la ficha."""
    from app.schemas.mvp_outreach import SdrReasoningRead
    from app.services.cold_message_bank import ColdBankRender

    monkeypatch.setattr(
        "app.services.openai_service.openai_configured", lambda: False
    )

    def _leaky_bank(**kwargs):
        return ColdBankRender(
            subject="Automatizá tu outbound",
            body=(
                "Hola Ana,\n\n"
                "Soy Joaquin de CostGuard.\n\n"
                "Con Nexus Sales bajamos las tareas manuales de prospección outbound "
                "unificando email, LinkedIn y WhatsApp.\n\n"
                "¿Agendamos?"
            ),
            template_id="b2b-email-cold-99",
            market="b2b",
            channel="email",
            kind="cold",
            index=0,
            leverage_used=False,
            reasoning=SdrReasoningRead(
                why_it_matters="leaky",
                bank_template_id="b2b-email-cold-99",
            ),
        )

    # Primera y rebuild: leaky → fuerza armado nuclear desde ficha Contractia.
    monkeypatch.setattr(
        "app.services.cold_message_bank.render_cold_bank_touch",
        _leaky_bank,
    )

    subject, body, reason = generate_sdr_playbook_touch(
        channel="email",
        prospect={
            "id": "ana-1",
            "name": "Ana López",
            "company_name": "Acme",
            "role": "CEO",
        },
        campaign={
            "id": "c9",
            "sender_name": "Joaquín",
            "brand_name": "CostGuard",
            "outreach_mode": "b2b",
        },
        product=_CONTRACTIA,
        education="",
        step_day=1,
        step_objective="primer toque",
        prior_touches=[],
    )
    assert "Nexus" not in body
    assert "outbound" not in body.lower()
    assert "Contractia" in body
    assert (
        "contrat" in (subject or "").lower()
        or "firma" in (subject or "").lower()
        or "Simplific" in (subject or "")
    )
    assert (reason.bank_template_id or "").startswith("product_safe")
