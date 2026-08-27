"""Orden de canales + plan de secuencia en ejecución real."""

from types import SimpleNamespace

from app.schemas.campaign_channels import normalize_allowed_channels
from app.services.campaign_sequence_channels import (
    channel_plan_summary,
    effective_channel_for_day,
)


def test_normalize_preserves_user_order():
    assert normalize_allowed_channels(["whatsapp", "linkedin", "email"]) == [
        "whatsapp",
        "linkedin",
        "email",
    ]
    assert normalize_allowed_channels(["email", "whatsapp"]) == ["email", "whatsapp"]


def test_custom_plan_channels_respected():
    campaign = SimpleNamespace(
        allowed_channels=["whatsapp", "linkedin", "email"],
        sequence_plan={
            "mode": "fixed",
            "steps": [
                {"day": 1, "channel": "whatsapp"},
                {"day": 4, "channel": "linkedin"},
                {"day": 7, "channel": "email"},
                {"day": 10, "channel": "whatsapp"},
                {"day": 13, "channel": "linkedin"},
                {"day": 16, "channel": "email"},
                {"day": 19, "channel": "whatsapp"},
            ],
        },
    )
    assert effective_channel_for_day(campaign, 1) == "whatsapp"
    assert effective_channel_for_day(campaign, 4) == "linkedin"
    assert effective_channel_for_day(campaign, 7) == "email"
    summary = channel_plan_summary(campaign)
    assert [s["channel"] for s in summary[:3]] == ["whatsapp", "linkedin", "email"]


def test_short_custom_plan_only_those_touches():
    from app.core.sequence_templates import plan_touch_days, validate_plan
    from app.services.campaign_sequence_channels import effective_playbook_steps

    plan = validate_plan(
        {
            "mode": "fixed",
            "template_name": "Corta",
            "steps": [
                {"day": 1, "channel": "email"},
                {"day": 4, "channel": "linkedin"},
                {"day": 7, "channel": "whatsapp"},
            ],
            "follow_up": {"enabled": False, "channel": "auto"},
        }
    )
    assert plan_touch_days(plan) == (1, 4, 7)
    campaign = SimpleNamespace(
        allowed_channels=["email", "linkedin", "whatsapp"],
        sequence_plan=plan,
    )
    steps = effective_playbook_steps(campaign)
    assert [s.day for s in steps] == [1, 4, 7]
    assert [s.channel for s in steps] == ["email", "linkedin", "whatsapp"]
    assert channel_plan_summary(campaign) == [
        {"day": 1, "channel": "email"},
        {"day": 4, "channel": "linkedin"},
        {"day": 7, "channel": "whatsapp"},
    ]


def test_subset_whatsapp_linkedin_only_cycles_order():
    campaign = SimpleNamespace(
        allowed_channels=["whatsapp", "linkedin"],
        sequence_plan=None,
    )
    assert effective_channel_for_day(campaign, 1) == "whatsapp"
    assert effective_channel_for_day(campaign, 4) == "linkedin"
    assert effective_channel_for_day(campaign, 7) == "whatsapp"
    assert effective_channel_for_day(campaign, 10) == "linkedin"
    # Nunca email si no está habilitado
    for day in (1, 4, 7, 10, 13, 16, 19):
        assert effective_channel_for_day(campaign, day) in ("whatsapp", "linkedin")


def test_plan_email_day_remapped_when_email_not_allowed():
    campaign = SimpleNamespace(
        allowed_channels=["whatsapp", "linkedin"],
        sequence_plan={
            "mode": "fixed",
            "steps": [
                {"day": 1, "channel": "email"},
                {"day": 4, "channel": "linkedin"},
                {"day": 7, "channel": "whatsapp"},
                {"day": 10, "channel": "email"},
                {"day": 13, "channel": "linkedin"},
                {"day": 16, "channel": "whatsapp"},
                {"day": 19, "channel": "email"},
            ],
        },
    )
    # Día 1 pedía email pero no está allowed → remapea al orden permitido
    assert effective_channel_for_day(campaign, 1) == "whatsapp"
    assert effective_channel_for_day(campaign, 4) == "linkedin"
