from unittest.mock import patch

from app.services.sequence_touch_whatsapp import sequence_whatsapp_touch_uses_api


def test_sequence_whatsapp_default_is_assisted_not_cloud_api(monkeypatch):
    monkeypatch.delenv("WHATSAPP_USE_CLOUD_API", raising=False)
    with patch("app.services.sequence_touch_whatsapp.om.is_real_mode", return_value=True):
        assert sequence_whatsapp_touch_uses_api(day=7, channel="whatsapp") is False
        assert sequence_whatsapp_touch_uses_api(day=10, channel="whatsapp") is False


def test_sequence_whatsapp_touch_uses_api_when_cloud_opt_in():
    with patch("app.services.sequence_touch_whatsapp.om.is_real_mode", return_value=True):
        with patch(
            "app.services.whatsapp_cloud_service.is_whatsapp_cloud_api_enabled",
            return_value=True,
        ):
            assert sequence_whatsapp_touch_uses_api(day=7, channel="whatsapp") is True
            assert sequence_whatsapp_touch_uses_api(day=10, channel="whatsapp") is True
            assert sequence_whatsapp_touch_uses_api(day=4, channel="linkedin") is False
            assert sequence_whatsapp_touch_uses_api(day=1, channel="email") is False
