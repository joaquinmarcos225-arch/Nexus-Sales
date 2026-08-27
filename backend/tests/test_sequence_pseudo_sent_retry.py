import json
from datetime import UTC, datetime

from app.models.prospect import Prospect
from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_PENDIENTE,
    _completed_days,
    _maybe_reset_retryable_touch,
    _touch_log,
    build_sequence_tracking,
    next_executable_day,
)


def _prospect_with_pseudo_whatsapp_sent() -> Prospect:
    log = {
        "1": {
            "status": "enviado",
            "message_body": "Email día 1",
            "body": "Email día 1",
            "gmail_message_id": "gmail-real-1",
            "gmail_manually_sent": True,
        },
        "4": {"status": "omitido"},
        "7": {
            "status": TOUCH_ENVIADO,
            "sent_at": "2026-06-29T14:22:39+00:00",
            "message_id": 92,
            "message_body": "[FALLBACK TEST]\n\nHola Test.",
            "body": "[FALLBACK TEST]\n\nHola Test.",
            "fallback_test": True,
        },
    }
    return Prospect(
        id=7,
        company_id=1,
        campaign_id=3,
        sequence_started_at=datetime(2026, 6, 28, 12, 0, tzinfo=UTC),
        sequence_touch_log=json.dumps(log),
        sequence_fired_milestones=json.dumps([1, 7]),
        sequence_playbook_draft=json.dumps(
            [
                {"day": 1, "body": "Email día 1"},
                {"day": 7, "body": "[FALLBACK TEST]\n\nHola Test."},
            ]
        ),
        phone="+5491128942875",
        whatsapp="+5491128942875",
    )


def test_pseudo_sent_whatsapp_not_counted_as_completed(monkeypatch):
    monkeypatch.setenv("NEXUS_REAL_MODE", "1")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1178898915307914")

    prospect = _prospect_with_pseudo_whatsapp_sent()
    done = _completed_days(prospect)
    assert 7 not in done
    assert next_executable_day(prospect) == 7


def test_build_tracking_allows_retry_for_pseudo_sent(monkeypatch):
    monkeypatch.setenv("NEXUS_REAL_MODE", "1")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1178898915307914")

    prospect = _prospect_with_pseudo_whatsapp_sent()
    from unittest.mock import MagicMock

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    tracking = build_sequence_tracking(db=db, prospect=prospect)
    day7 = next(s for s in tracking["steps"] if s["day"] == 7)
    assert day7["touch_status"] == "fallido"
    assert day7["can_execute"] is True


def test_reset_touch_for_retry_clears_pseudo_sent(monkeypatch):
    monkeypatch.setenv("NEXUS_REAL_MODE", "1")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1178898915307914")

    prospect = _prospect_with_pseudo_whatsapp_sent()
    assert _maybe_reset_retryable_touch(prospect, 7) is True
    entry = _touch_log(prospect)["7"]
    assert entry["status"] == TOUCH_PENDIENTE
    assert entry.get("fallback_test") is False
    assert prospect.sequence_fired_milestones == "[1]"
