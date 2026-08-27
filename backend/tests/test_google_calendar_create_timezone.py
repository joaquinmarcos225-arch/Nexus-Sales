"""Timezone / payload local para create_calendar_event."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.google_calendar_create import (
    format_google_local_datetime,
    to_campaign_local,
)


def test_to_campaign_local_keeps_wall_clock_in_buenos_aires():
    # 18:00 UTC = 15:00 en Argentina (UTC-3)
    utc = datetime(2026, 7, 28, 18, 0, tzinfo=ZoneInfo("UTC"))
    local = to_campaign_local(utc, "America/Argentina/Buenos_Aires")
    assert local.hour == 15
    assert local.minute == 0
    assert format_google_local_datetime(local) == "2026-07-28T15:00:00"


def test_naive_datetime_treated_as_campaign_local():
    naive = datetime(2026, 7, 28, 15, 30, 0)
    local = to_campaign_local(naive, "America/Argentina/Buenos_Aires")
    assert local.hour == 15
    assert local.minute == 30
    assert format_google_local_datetime(local) == "2026-07-28T15:30:00"


def test_create_calendar_event_body_uses_local_datetime(monkeypatch):
    """El body a Google no debe mandar UTC+Z con timeZone local."""
    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "evt-1", "htmlLink": "https://cal/evt-1"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, params=None, json=None):
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr(
        "app.services.google_calendar_create.get_valid_google_calendar_connection",
        lambda *a, **k: ("token", object()),
    )
    monkeypatch.setattr("app.services.google_calendar_create.httpx.Client", FakeClient)

    from app.services.google_calendar_create import create_calendar_event

    start = datetime(2026, 7, 28, 18, 0, tzinfo=ZoneInfo("UTC"))  # 15:00 ART
    out = create_calendar_event(
        db=None,  # type: ignore[arg-type]
        company_id=1,
        seller_user_id=1,
        title="Demo",
        description=None,
        start_at=start,
        duration_minutes=30,
        timezone="America/Argentina/Buenos_Aires",
    )
    body = captured["json"]
    assert body["start"]["dateTime"] == "2026-07-28T15:00:00"
    assert body["start"]["timeZone"] == "America/Argentina/Buenos_Aires"
    assert body["end"]["dateTime"] == "2026-07-28T15:30:00"
    assert not body["start"]["dateTime"].endswith("Z")
    assert out["event_id"] == "evt-1"
