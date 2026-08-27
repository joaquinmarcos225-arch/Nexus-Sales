"""Auto-reply skip outcomes: skipped_disabled no ensucia actividad y puede reintentar."""

from unittest.mock import MagicMock, patch

from app.services.inbound_auto_reply import (
    auto_reply_is_finished,
    deliver_auto_reply_for_inbound,
    inbound_auto_reply_enabled,
    inbound_needs_auto_reply_retry,
)


def test_skipped_disabled_silent_and_retryable(monkeypatch):
    monkeypatch.setenv("NEXUS_INBOUND_AUTO_REPLY", "0")
    assert inbound_auto_reply_enabled() is False

    db = MagicMock()
    campaign = MagicMock(id=4, company_id=1, seller_id=6, automation_paused=False)
    prospect = MagicMock(id=10, company_id=1, status="contacted", ai_paused=False)

    inbound_row = MagicMock()
    with patch("app.services.inbound_auto_reply.get_auto_reply_receipt", return_value=None):
        with patch("app.services.inbound_auto_reply.record_auto_reply_receipt") as record:
            with patch("app.services.inbound_auto_reply.log_auto_reply_outcome_to_activity") as log_act:
                with patch("app.services.inbound_auto_reply._get_inbound_row", return_value=inbound_row):
                    out = deliver_auto_reply_for_inbound(
                        db,
                        campaign=campaign,
                        prospect=prospect,
                        inbound_gmail_message_id="gmail-mid-1",
                    )
    assert out == "skipped_disabled"
    record.assert_called_once()
    log_act.assert_not_called()

    # No es terminal: si se reactiva NEXUS_INBOUND_AUTO_REPLY, el poll reintenta.
    receipt = MagicMock(outcome="skipped_disabled")
    with patch("app.services.inbound_auto_reply.get_auto_reply_receipt", return_value=receipt):
        assert auto_reply_is_finished(db, 10, "gmail-mid-1") is False
        assert inbound_needs_auto_reply_retry(db, 10, "gmail-mid-1") is True
