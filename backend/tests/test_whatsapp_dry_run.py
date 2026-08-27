from unittest.mock import patch

from app.services.whatsapp_cloud_service import (
    build_sequence_template_parameters,
    is_whatsapp_api_configured,
    is_whatsapp_cloud_api_enabled,
    meta_api_recipient_candidates,
    normalize_whatsapp_digits_for_meta_api,
    send_sequence_whatsapp_message,
    send_text_message,
    template_name_for_sequence_day,
    verify_whatsapp_api,
)


def test_default_mode_is_assisted_not_dry_run(monkeypatch):
    monkeypatch.delenv("WHATSAPP_USE_CLOUD_API", raising=False)
    monkeypatch.setenv("WHATSAPP_DRY_RUN", "1")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    assert is_whatsapp_api_configured() is False
    assert is_whatsapp_cloud_api_enabled() is False
    v = verify_whatsapp_api(deep=True)
    assert v["mode"] == "assisted"
    assert v["dry_run"] is False
    assert v["configured"] is True


def test_cloud_api_opt_in_requires_tokens(monkeypatch):
    monkeypatch.setenv("WHATSAPP_USE_CLOUD_API", "1")
    monkeypatch.delenv("WHATSAPP_DRY_RUN", raising=False)
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    assert is_whatsapp_cloud_api_enabled() is True
    assert is_whatsapp_api_configured() is True


def test_whatsapp_dry_run_send_still_works_for_legacy(monkeypatch):
    monkeypatch.setenv("WHATSAPP_DRY_RUN", "1")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    with patch("app.services.whatsapp_cloud_service._reload_whatsapp_env"):
        out = send_text_message(to_digits="5491112345678", body="Hola test")
    assert out["dry_run"] is True
    assert out["whatsapp_message_id"].startswith("dry_run_wamid_")


def test_meta_api_recipient_candidates_argentina():
    cands = meta_api_recipient_candidates("+5491128942875", None)
    assert "54111528942875" in cands
    assert "5491128942875" in cands
    assert "541128942875" in cands


def test_meta_api_recipient_candidates_interior_ar_with_without_9():
    cands = meta_api_recipient_candidates("+54 9 3476 36-2762", None)
    assert "5493476362762" in cands
    assert "543476362762" in cands


def test_meta_api_argentina_mobile_format():
    assert (
        normalize_whatsapp_digits_for_meta_api("+5491128942875", None)
        == "54111528942875"
    )
    assert (
        normalize_whatsapp_digits_for_meta_api("54111528942875", None)
        == "54111528942875"
    )
    # Interior AR: no corromper con octal \115 → 'M'
    converted = normalize_whatsapp_digits_for_meta_api("+54 9 3476 36-2762", None)
    assert converted is not None
    assert "M" not in converted
    assert converted.startswith("54")


def test_template_name_for_sequence_day(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TEMPLATE_DAY7", "tpl_d7")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "tpl_default")
    assert template_name_for_sequence_day(7) == "tpl_d7"
    assert template_name_for_sequence_day(16) == "tpl_default"


def test_build_sequence_template_parameters():
    params = build_sequence_template_parameters(
        prospect_name="Ana Lopez",
        company_name="Acme",
        body="Hola\nmundo",
    )
    assert params[0] == "Ana"
    assert params[1] == "Acme"
    assert "Hola mundo" in params[2]


def test_sequence_send_uses_template_when_configured(monkeypatch):
    monkeypatch.setenv("WHATSAPP_DRY_RUN", "1")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.setenv("WHATSAPP_TEMPLATE_DAY7", "nexus_sequence")
    with patch("app.services.whatsapp_cloud_service._reload_whatsapp_env"):
        out = send_sequence_whatsapp_message(
            to_digits="5491112345678",
            body="Mensaje IA",
            day=7,
            prospect_name="Ana",
            company_name="Acme",
        )
    assert out["dry_run"] is True
    assert out["raw"]["payload_type"] == "template"
