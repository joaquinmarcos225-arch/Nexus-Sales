"""Campaign / analytics datetimes must serialize as UTC-aware."""

from datetime import UTC, datetime

from app.schemas.analytics import CampaignAnalyticsRow
from app.schemas.campaign import CampaignRead
from app.schemas.datetime_utc import as_utc_datetime
from app.models.enums import AutopilotStatus, CampaignStatus, OutreachEmailMode, OutreachMode, InboundReplyMode


def test_as_utc_assumes_naive_is_utc():
    naive = datetime(2026, 8, 27, 14, 30, 0)
    aware = as_utc_datetime(naive)
    assert aware.tzinfo is not None
    assert aware.utcoffset().total_seconds() == 0
    assert aware.hour == 14


def test_campaign_read_json_marks_created_at_utc():
    row = CampaignRead(
        id=1,
        company_id=1,
        seller_id=1,
        product_id=1,
        product_name="P",
        seller_name="S",
        name="Campaña test",
        status=CampaignStatus.draft,
        autopilot_status=AutopilotStatus.off,
        outreach_mode=OutreachMode.b2b,
        product_market_scope=None,
        target_company_size=None,
        target_industry=None,
        target_country=None,
        target_language=None,
        target_role=None,
        prospect_count=10,
        calendar_link="https://cal.example/x",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="directo",
        allowed_channels=["email"],
        estimated_meetings_min=0,
        estimated_meetings_max=0,
        estimated_cost_min=0,
        estimated_cost_max=0,
        estimated_avg_cost_per_meeting=0.0,
        created_at=datetime(2026, 8, 27, 14, 30, 0),
        updated_at=None,
        outreach_email_mode=OutreachEmailMode.auto_send,
        inbound_reply_mode=InboundReplyMode.auto_send,
    )
    payload = row.model_dump(mode="json")
    created = payload["created_at"]
    assert isinstance(created, str)
    assert created.endswith("+00:00") or created.endswith("Z")
    assert "14:30:00" in created


def test_campaign_analytics_last_activity_utc():
    row = CampaignAnalyticsRow(
        campaign_id=1,
        name="X",
        status="running",
        seller_name="S",
        prospects_active=1,
        prospects_contacted=0,
        prospects_responded=0,
        prospects_interested=0,
        meetings=0,
        messages_sent=0,
        last_activity_at=datetime(2026, 8, 27, 18, 0, 0),
    )
    payload = row.model_dump(mode="json")
    assert payload["last_activity_at"].endswith("+00:00") or payload["last_activity_at"].endswith("Z")
