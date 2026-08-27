"""Límites diarios por canal por SDR (anti-bloqueo)."""

from unittest.mock import MagicMock, patch

from app.services import daily_send_limits as dsl


def test_default_limits():
    assert dsl.limit_for(dsl.KIND_EMAIL) == 300
    assert dsl.limit_for(dsl.KIND_LINKEDIN_INVITE) == 40
    assert dsl.limit_for(dsl.KIND_LINKEDIN_DM) == 30
    assert dsl.limit_for(dsl.KIND_WHATSAPP) == 20


def test_whatsapp_remaining_includes_inbound_bonus():
    db = MagicMock()
    seller_id = 7
    with (
        patch.object(dsl, "limit_for", return_value=20),
        patch.object(dsl, "whatsapp_inbounds_today", return_value=3),
        patch.object(dsl, "used_today", return_value=18),
    ):
        assert dsl.remaining(db, seller_id, dsl.KIND_WHATSAPP) == 5


def test_env_override(monkeypatch):
    monkeypatch.setenv("NEXUS_DAILY_LIMIT_WHATSAPP", "3")
    assert dsl.limit_for(dsl.KIND_WHATSAPP) == 3
    monkeypatch.setenv("NEXUS_DAILY_LIMIT_LINKEDIN_INVITE", "0")
    assert dsl.limit_for(dsl.KIND_LINKEDIN_INVITE) == 0


def test_unknown_kind_is_zero():
    assert dsl.limit_for("inexistente") == 0
