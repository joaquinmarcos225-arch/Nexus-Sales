"""Tests de sourcing automático hasta prospect_count."""

from unittest.mock import MagicMock, patch

from app.models.campaign import Campaign
from app.services.lead_sourcing.auto_bootstrap import (
    auto_source_and_import_until_quota,
    process_campaign_sourcing_refill,
    sourcing_refill_enabled,
)


def _campaign(prospect_count: int = 10, campaign_id: int = 7) -> Campaign:
    return Campaign(id=campaign_id, prospect_count=prospect_count)


@patch("app.services.lead_sourcing.auto_bootstrap.count_campaign_prospects", return_value=0)
def test_until_quota_skips_when_no_target(_count):
    campaign = _campaign(prospect_count=0)
    db = MagicMock()
    result = auto_source_and_import_until_quota(db, campaign)
    assert result["skipped"] is True
    assert result["reason"] == "no_prospect_quota"
    assert result["ran"] is False


@patch("app.services.lead_sourcing.auto_bootstrap.count_campaign_prospects", return_value=12)
def test_until_quota_skips_when_quota_met(_count):
    campaign = _campaign(prospect_count=10)
    db = MagicMock()
    result = auto_source_and_import_until_quota(db, campaign)
    assert result["skipped"] is True
    assert result["reason"] == "quota_met"
    assert result["quota_met"] is True
    assert "12 de 10" in (result.get("message") or "")


@patch("app.services.lead_sourcing.providers.registry.pipeline_ready_for_campaign", return_value=False)
@patch("app.services.lead_sourcing.auto_bootstrap.refresh_lead_sourcing_env")
@patch("app.services.lead_sourcing.auto_bootstrap.count_campaign_prospects", return_value=2)
def test_until_quota_not_configured(_count, _refresh, _ready):
    campaign = _campaign(prospect_count=10)
    db = MagicMock()
    result = auto_source_and_import_until_quota(db, campaign)
    assert result["ran"] is True
    assert result["reason"] == "sourcing_not_configured"
    assert result["imported"] == 0


@patch("app.services.lead_sourcing.auto_bootstrap._pipeline_company_count", return_value=0)
@patch("app.services.lead_sourcing.auto_bootstrap._run_mvp_pipeline", return_value={"ok": True, "message": "ok"})
@patch("app.services.lead_sourcing.auto_bootstrap._import_batch")
@patch("app.services.lead_sourcing.providers.registry.pipeline_ready_for_campaign", return_value=True)
@patch("app.services.lead_sourcing.auto_bootstrap.refresh_lead_sourcing_env")
@patch("app.services.lead_sourcing.auto_bootstrap.count_campaign_prospects")
def test_until_quota_imports_from_store_then_pipeline(
    count_mock, _refresh, _ready, import_mock, _pipe, _co_count
):
    # count_before, remaining×N, count_after — secuencia estable para cupo 6.
    count_mock.side_effect = [1, 1, 4, 6, 6, 6, 6, 6]
    import_mock.side_effect = [
        {"imported": 3, "skipped_duplicates": 0, "errors": []},
        {"imported": 2, "skipped_duplicates": 1, "errors": []},
        {"imported": 0, "skipped_duplicates": 0, "errors": []},
    ]
    campaign = _campaign(prospect_count=6)
    db = MagicMock()
    result = auto_source_and_import_until_quota(db, campaign, max_pipeline_passes=2)
    assert result["imported"] == 5
    assert result["quota_met"] is True
    assert result["prospect_count_after"] == 6
    assert "5 prospecto" in (result.get("message") or "")
    assert import_mock.call_count >= 2
    assert _pipe.call_count >= 1


def test_process_campaign_sourcing_refill_delegates():
    campaign = _campaign(prospect_count=10)
    db = MagicMock()
    with patch(
        "app.services.lead_sourcing.auto_bootstrap.count_campaign_prospects",
        return_value=2,
    ), patch(
        "app.services.lead_sourcing.auto_bootstrap.auto_source_and_import_until_quota",
        return_value={"ran": True, "imported": 1},
    ) as mock_fn:
        out = process_campaign_sourcing_refill(db, campaign, max_pipeline_passes=1)
    mock_fn.assert_called_once_with(db, campaign, max_pipeline_passes=1)
    assert out["imported"] == 1


@patch(
    "app.services.lead_sourcing.auto_bootstrap._importable_external_ids",
    return_value=["x"],
)
@patch(
    "app.services.lead_sourcing.auto_bootstrap.store.load_meta",
    return_value={"quota_force_full": False},
)
@patch("app.services.lead_sourcing.auto_bootstrap.store.get_or_create")
@patch(
    "app.services.lead_sourcing.auto_bootstrap._enrich_progress_snapshot",
    return_value={"has_more": True, "processed": 8, "total": 40},
)
@patch("app.services.lead_sourcing.auto_bootstrap._pipeline_company_count", return_value=40)
@patch("app.services.lead_sourcing.auto_bootstrap.ls_service.run_pipeline_step")
def test_run_mvp_pipeline_uses_enrich_when_has_more(
    run_step, _co, _ep, _get, _meta, _importable
):
    from types import SimpleNamespace

    from app.services.lead_sourcing.auto_bootstrap import _run_mvp_pipeline

    run_step.return_value = SimpleNamespace(ok=True, message="enrich ok")
    campaign = _campaign(prospect_count=60)
    campaign.target_industry = "SaaS"
    campaign.target_role = "Head of Sales"
    db = MagicMock()
    out = _run_mvp_pipeline(db, campaign, remaining_slots=48)
    assert out["ok"] is True
    assert out["step"] == "enrich"
    assert run_step.call_args.args[2] == "enrich"


@patch("app.services.lead_sourcing.auto_bootstrap.ls_service.run_pipeline_step")
def test_run_mvp_pipeline_role_first_without_industry(run_step):
    from types import SimpleNamespace

    from app.services.lead_sourcing.auto_bootstrap import _run_mvp_pipeline

    run_step.return_value = SimpleNamespace(ok=True, message="people ok")
    campaign = _campaign(prospect_count=20)
    campaign.target_industry = None
    campaign.target_role = "Head of Sales"
    campaign.outreach_mode = "b2b"
    db = MagicMock()
    out = _run_mvp_pipeline(db, campaign, remaining_slots=19)
    assert out["step"] == "people_direct"
    assert run_step.call_args.args[2] == "people_direct"


@patch.dict("os.environ", {"NEXUS_SOURCING_REFILL_ENABLED": "0"}, clear=False)
def test_sourcing_refill_disabled_explicit():
    assert sourcing_refill_enabled() is False


@patch.dict("os.environ", {"NEXUS_SOURCING_REFILL_ENABLED": "1"}, clear=False)
def test_sourcing_refill_enabled_explicit():
    assert sourcing_refill_enabled() is True


@patch.dict(
    "os.environ",
    {"NEXUS_SOURCING_REFILL_ENABLED": "", "NEXUS_AUTOMATION_SCHEDULER": "1", "NEXUS_REAL_MODE": "0"},
    clear=False,
)
def test_sourcing_refill_enabled_when_scheduler_on():
    assert sourcing_refill_enabled() is True
