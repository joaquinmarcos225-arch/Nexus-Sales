"""Tests: bloqueo integración vs omitir canal / kickoff por prospecto."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.enums import IntegrationStatus
from app.services.sequence_channel_gate import (
    channel_label,
    continue_sequence_without_channel,
    read_campaign_integration_block,
    seller_channel_block,
    set_campaign_integration_block,
)


def test_channel_label():
    assert channel_label("linkedin") == "LinkedIn"
    assert channel_label("whatsapp") == "WhatsApp"
    assert channel_label("email") == "email"


def test_seller_channel_block_linkedin_allows_missing_account():
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    camp = SimpleNamespace(seller_id=1, company_id=9)
    assert seller_channel_block(db, camp, "linkedin") is None


def test_seller_channel_block_linkedin_holds_on_extension_error():
    db = MagicMock()
    db.scalars.return_value.first.return_value = SimpleNamespace(
        status=IntegrationStatus.error.value
    )
    camp = SimpleNamespace(seller_id=1, company_id=9)
    block = seller_channel_block(db, camp, "linkedin")
    assert block is not None
    assert block["channel"] == "linkedin"
    assert "reconex" in block["error"].lower() or "error" in block["error"].lower()


def test_set_and_read_integration_block_roundtrip():
    camp = SimpleNamespace(outreach_activity_log=None)
    set_campaign_integration_block(
        camp,
        {
            "channel": "linkedin",
            "code": "extension_disconnected",
            "error": "Extensión Nexus de LinkedIn necesita reconexión.",
            "action": "reconnect_extension",
        },
        blocked_prospects=2,
    )
    block = read_campaign_integration_block(camp)
    assert block is not None
    assert block["channel"] == "linkedin"
    assert "Extensión" in block["error"]
    assert block["blocked_prospects"] == 2


def test_continue_without_channel_removes_from_allowed():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    camp = SimpleNamespace(
        id=7,
        seller_id=1,
        company_id=9,
        allowed_channels=["linkedin", "whatsapp", "email"],
        outreach_activity_log=None,
    )
    out = continue_sequence_without_channel(db, camp, channel="linkedin", actor_user_id=1)
    assert out["ok"] is True
    assert "linkedin" not in camp.allowed_channels
    assert "whatsapp" in camp.allowed_channels


def test_continue_without_last_channel_rejected():
    db = MagicMock()
    camp = SimpleNamespace(
        id=7,
        seller_id=1,
        company_id=9,
        allowed_channels=["linkedin"],
        outreach_activity_log=None,
    )
    out = continue_sequence_without_channel(db, camp, channel="linkedin", actor_user_id=1)
    assert out["ok"] is False
    assert "sin canales" in (out.get("detail") or "").lower()


@patch("app.services.campaign_day1_assisted.seller_channel_block", return_value=None)
@patch("app.services.campaign_day1_assisted.seq")
def test_kickoff_prospect_waits_calendar_after_omit_day1(seq_mock, _block):
    """Foco D: omitir día 1 no dispara día 4 el mismo día."""
    from datetime import UTC, datetime

    from app.services.campaign_day1_assisted import kickoff_assisted_day1_for_prospect

    seq_mock._planned_days.return_value = [1, 4]
    seq_mock._completed_days.side_effect = [
        set(),
        {1},
    ]
    seq_mock.execute_sequence_touch.side_effect = [
        {"omitted": True, "channel": "linkedin", "summary": "sin dato"},
    ]
    seq_mock.compute_next_touch.return_value = (datetime.now(UTC), "Día 4 · whatsapp")
    db = MagicMock()
    camp = SimpleNamespace(id=1, product_id=None, seller_id=2, status="running")
    prospect = SimpleNamespace(
        id=59,
        status="nuevo",
        sequence_paused=False,
        sequence_started_at=datetime.now(UTC),
        linkedin_assisted_draft=None,
        whatsapp_assisted_draft=None,
        next_touch_at=None,
    )
    actor = SimpleNamespace(id=2)

    with patch(
        "app.services.campaign_day1_assisted._day_channel",
        side_effect=["linkedin", "whatsapp"],
    ), patch(
        "app.services.campaign_day1_assisted._linkedin_day1_already_queued",
        return_value=False,
    ), patch(
        "app.services.campaign_day1_assisted._whatsapp_day_already_queued",
        return_value=False,
    ):
        out = kickoff_assisted_day1_for_prospect(db, camp, prospect, actor=actor)

    assert out["started"] is False
    assert out.get("waiting_calendar") is True
    assert out["next_day"] == 4
    assert out["omitted_days"] == [1]
    assert seq_mock.execute_sequence_touch.call_count == 1
    assert prospect.next_touch_at is not None


@patch("app.services.campaign_day1_assisted.seller_channel_block", return_value=None)
@patch("app.services.campaign_day1_assisted.seq")
def test_kickoff_prospect_can_catch_up_when_day_due(seq_mock, _block):
    """Si el calendario ya llegó al día 4, omitir día 1 y ejecutar día 4 está OK."""
    from datetime import UTC, datetime, timedelta

    from app.services.campaign_day1_assisted import kickoff_assisted_day1_for_prospect

    seq_mock._planned_days.return_value = [1, 4]
    seq_mock._completed_days.side_effect = [
        set(),
        {1},
        {1},
    ]
    seq_mock.execute_sequence_touch.side_effect = [
        {"omitted": True, "channel": "linkedin", "summary": "sin dato"},
        {"linkedin_assisted": False, "whatsapp_assisted": True, "channel": "whatsapp"},
    ]
    db = MagicMock()
    camp = SimpleNamespace(id=1, product_id=None, seller_id=2, status="running")
    prospect = SimpleNamespace(
        id=59,
        status="nuevo",
        sequence_paused=False,
        sequence_started_at=datetime.now(UTC) - timedelta(days=3),
        linkedin_assisted_draft=None,
        whatsapp_assisted_draft=None,
    )
    actor = SimpleNamespace(id=2)

    with patch(
        "app.services.campaign_day1_assisted._day_channel",
        side_effect=["linkedin", "whatsapp"],
    ), patch(
        "app.services.campaign_day1_assisted._linkedin_day1_already_queued",
        return_value=False,
    ), patch(
        "app.services.campaign_day1_assisted._whatsapp_day_already_queued",
        return_value=False,
    ):
        out = kickoff_assisted_day1_for_prospect(db, camp, prospect, actor=actor)

    assert out["started"] is True
    assert out["delivered_day"] == 4
    assert out["omitted_days"] == [1]
    assert out["queued_whatsapp"] is True


@patch(
    "app.services.campaign_day1_assisted.seller_channel_block",
    return_value={
        "channel": "linkedin",
        "code": "extension_disconnected",
        "error": "Reconectá la extensión Nexus de LinkedIn.",
        "action": "reconnect_extension",
    },
)
@patch("app.services.campaign_day1_assisted.seq")
def test_kickoff_prospect_holds_when_extension_blocked(seq_mock, _block):
    from datetime import UTC, datetime

    from app.services.campaign_day1_assisted import kickoff_assisted_day1_for_prospect

    seq_mock._planned_days.return_value = [1]
    seq_mock._completed_days.return_value = set()
    db = MagicMock()
    camp = SimpleNamespace(id=1, product_id=None, seller_id=2, status="running")
    prospect = SimpleNamespace(
        id=60,
        status="nuevo",
        sequence_paused=False,
        sequence_started_at=datetime.now(UTC),
        linkedin_assisted_draft=None,
        whatsapp_assisted_draft=None,
    )
    with patch(
        "app.services.campaign_day1_assisted._day_channel", return_value="linkedin"
    ), patch(
        "app.services.campaign_day1_assisted._linkedin_day1_already_queued",
        return_value=False,
    ):
        out = kickoff_assisted_day1_for_prospect(
            db, camp, prospect, actor=SimpleNamespace(id=2)
        )

    assert out["held"] is True
    assert out["started"] is False
    assert "Reconectá" in (out["block"] or {}).get("error", "")
    seq_mock.execute_sequence_touch.assert_not_called()
