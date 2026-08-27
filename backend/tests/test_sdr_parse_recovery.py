import json

import pytest

from app.services.lead_sourcing.sdr_playbook_outreach import (
    SdrResponseParseError,
    _recover_touch_data_after_parse_error,
    _salvage_follow_up_touch_locally,
)
from app.services.openai_fallback import follow_up_response_question_from_body, normalize_follow_up_internal


def test_follow_up_response_question_skips_signature():
    body = (
        "Hola María, retomo mis mensajes anteriores.\n"
        "¿Te parece seguir conversando sobre esto o lo dejamos para más adelante?\n"
        "Joaquín"
    )
    assert "?" in follow_up_response_question_from_body(body)
    assert "Joaquín" not in follow_up_response_question_from_body(body)


def test_normalize_follow_up_internal_fills_role_and_cta():
    body = (
        "Hola María, retomo mis mensajes anteriores.\n"
        "¿Te parece seguir conversando sobre esto o lo dejamos para más adelante?\n"
        "Joaquín"
    )
    internal = normalize_follow_up_internal(
        {"response_question": "Joaquín", "selling_to_role": ""},
        body=body,
        prospect={"name": "María"},
        step_day=7,
        step_objective="WhatsApp D7",
    )
    assert len(internal["response_question"]) >= 12
    assert internal["selling_to_role"] == "Decisor comercial"


def test_recover_followup_from_plain_text_salvage():
    body = (
        "Hola María, retomo mis mensajes anteriores para no insistir sin sentido. "
        "¿Te parece seguir conversando ahora o prefieres que lo deje para más adelante?"
    )
    exc = SdrResponseParseError(
        message="OpenAI no devolvió JSON válido para el borrador SDR.",
        debug={"parse_error": "json.JSONDecodeError: Expecting value (pos 0)"},
        salvage_body=body,
    )
    prospect = {"name": "María Test", "role": "CEO"}
    recovered, used_fallback = _recover_touch_data_after_parse_error(
        exc,
        channel="whatsapp",
        prospect=prospect,
        campaign={"name": "Camp"},
        product={"name": "Nexus"},
        step_day=7,
        step_objective="Retomar por WhatsApp",
        prior_touches=[{"day": 1, "channel": "email"}],
        first_touch=False,
    )
    assert used_fallback is False
    assert recovered["body"] == body
    assert len(recovered["internal"]["response_question"]) >= 12
    assert recovered["internal"]["selling_to_role"] == "CEO"


def test_day7_whatsapp_follow_up_validation_is_minimal():
    from app.services.lead_sourcing.sdr_playbook_outreach import _validate_follow_up_body

    body = (
        "Hola María, retomo mis mensajes anteriores para no insistir sin sentido por distintos canales. "
        "¿Te parece si seguimos conversando o prefieres que lo deje para más adelante?\n"
        "Joaquín - CostGuard Demo Client"
    )
    acc = _validate_follow_up_body(body, step_day=7, channel="whatsapp")
    assert acc.issues == []


def test_recover_followup_with_playbook_fallback_when_empty_response(monkeypatch):
    monkeypatch.setenv("NEXUS_ENABLE_SEQUENCE_TESTING", "1")
    exc = SdrResponseParseError(
        message="OpenAI no devolvió JSON válido para el borrador SDR.",
        debug={"parse_error": "json.JSONDecodeError: Expecting value (pos 0)"},
        salvage_body=None,
    )
    recovered, used_fallback = _recover_touch_data_after_parse_error(
        exc,
        channel="whatsapp",
        prospect={"name": "María Test"},
        campaign={"name": "Camp"},
        product={"name": "Nexus"},
        step_day=7,
        step_objective="Retomar por WhatsApp",
        prior_touches=[{"day": 1, "channel": "email"}],
        first_touch=False,
    )
    assert used_fallback is True
    assert len(recovered["body"]) >= 20
    json.dumps(recovered)


def test_salvage_follow_up_touch_locally_repairs_bad_internal():
    body = (
        "Hola María, retomo mis mensajes anteriores para no insistir sin sentido.\n"
        "¿Seguimos conversando o lo dejamos para más adelante?\n"
        "Joaquín"
    )
    salvaged = _salvage_follow_up_touch_locally(
        data={"internal": {"response_question": "Joaquín", "selling_to_role": ""}},
        body=body,
        channel="whatsapp",
        step_day=7,
        step_objective="Retomar por WhatsApp",
        prospect={"name": "María Test", "role": "CEO"},
        campaign={"name": "Camp", "sender_name": "Joaquín"},
        product={"name": "Nexus"},
        prior_touches=[{"day": 1, "channel": "email"}],
    )
    assert salvaged["body"] == body
    assert len(salvaged["internal"]["response_question"]) >= 12
    assert salvaged["internal"]["selling_to_role"]
