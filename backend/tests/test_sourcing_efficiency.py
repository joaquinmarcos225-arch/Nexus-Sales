"""Tests de sourcing_efficiency (anti-despilfarro)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.services.lead_sourcing.sourcing_efficiency import (
    clear_sourcing_stall,
    effective_passes_limit,
    is_sourcing_stalled,
    record_sourcing_tick,
)


@patch("app.services.lead_sourcing.sourcing_efficiency.store.get_or_create")
@patch("app.services.lead_sourcing.sourcing_efficiency.store.load_meta")
@patch("app.services.lead_sourcing.sourcing_efficiency.store.save_meta")
def test_record_sourcing_tick_stalls_after_threshold(save_meta, load_meta, get_or_create):
    load_meta.return_value = {}
    row = MagicMock()
    get_or_create.return_value = row
    db = MagicMock()

    with patch(
        "app.services.lead_sourcing.sourcing_efficiency.stall_zero_import_ticks",
        return_value=2,
    ):
        assert record_sourcing_tick(db, 7, imported=0, pipeline_runs=1) is False
        assert record_sourcing_tick(db, 7, imported=0, pipeline_runs=1) is True

    saved = save_meta.call_args[0][1]
    stall = saved["sourcing_stall"]
    assert stall["stalled"] is True
    assert stall["zero_import_ticks"] == 2


@patch("app.services.lead_sourcing.sourcing_efficiency.store.get_or_create")
@patch("app.services.lead_sourcing.sourcing_efficiency.store.load_meta")
@patch("app.services.lead_sourcing.sourcing_efficiency.store.save_meta")
def test_record_sourcing_tick_clears_on_import(save_meta, load_meta, get_or_create):
    load_meta.return_value = {
        "sourcing_stall": {"stalled": True, "zero_import_ticks": 5},
    }
    get_or_create.return_value = MagicMock()
    db = MagicMock()

    assert record_sourcing_tick(db, 7, imported=2, pipeline_runs=1) is False
    saved = save_meta.call_args[0][1]
    assert "sourcing_stall" not in saved


@patch("app.services.lead_sourcing.sourcing_efficiency.get_stall_state")
def test_is_sourcing_stalled_respects_cooldown(get_stall):
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    get_stall.return_value = {"stalled": True, "reason": "test", "retry_after": future}
    db = MagicMock()
    stalled, reason = is_sourcing_stalled(db, 1)
    assert stalled is True
    assert reason == "test"

    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    get_stall.return_value = {"stalled": True, "retry_after": past}
    stalled2, _ = is_sourcing_stalled(db, 1)
    assert stalled2 is False


@patch("app.services.lead_sourcing.sourcing_efficiency.store.get_or_create")
@patch("app.services.lead_sourcing.sourcing_efficiency.store.load_meta")
@patch("app.services.lead_sourcing.sourcing_efficiency.store.save_meta")
def test_clear_sourcing_stall(clear_save, load_meta, get_or_create):
    load_meta.return_value = {"sourcing_stall": {"stalled": True}}
    get_or_create.return_value = MagicMock()
    db = MagicMock()
    clear_sourcing_stall(db, 9)
    saved = clear_save.call_args[0][1]
    assert "sourcing_stall" not in saved


def test_build_campaign_sourcing_status_complete():
    from unittest.mock import MagicMock, patch

    db = MagicMock()
    campaign = MagicMock()
    campaign.prospect_count = 10
    db.get.return_value = campaign

    with patch(
        "app.services.campaign_prospects.count_campaign_prospects",
        return_value=10,
    ), patch(
        "app.services.lead_sourcing.sourcing_efficiency.is_sourcing_stalled",
        return_value=(False, None),
    ), patch(
        "app.services.lead_sourcing.sourcing_efficiency.get_stall_state",
        return_value={},
    ), patch(
        "app.services.provider_guard.sourcing_providers_blocked",
        return_value=(False, None),
    ):
        from app.services.lead_sourcing.sourcing_efficiency import build_campaign_sourcing_status

        out = build_campaign_sourcing_status(db, 1)
    assert out["state"] == "complete"
    assert out["quota_met"] is True
