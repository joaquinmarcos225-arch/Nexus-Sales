"""Tests auto-detect de borradores Gmail de secuencia enviados."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from app.services.sequence_gmail_draft_sent import (
    draft_touch_was_sent,
    gmail_draft_status,
    reconcile_prospect_gmail_draft_sents,
)


def test_gmail_draft_status_missing_on_404():
    client = MagicMock(spec=httpx.Client)
    response = MagicMock()
    response.status_code = 404
    client.get.return_value = response

    assert gmail_draft_status(client, "token", "draft-abc") == "missing"


def test_gmail_draft_status_exists_on_200():
    client = MagicMock(spec=httpx.Client)
    response = MagicMock()
    response.status_code = 200
    client.get.return_value = response

    assert gmail_draft_status(client, "token", "draft-abc") == "exists"


@patch("app.services.sequence_gmail_draft_sent.fetch_thread_full")
def test_draft_touch_was_sent_matches_thread(mock_fetch):
    mock_fetch.return_value = {
        "messages": [
            {
                "labelIds": ["SENT"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "sdr@test.com"},
                        {"name": "To", "value": "prospect@example.com"},
                        {"name": "Subject", "value": "Hola Mia"},
                    ]
                },
            }
        ]
    }
    prospect = MagicMock()
    prospect.email = "prospect@example.com"
    prospect.gmail_thread_id = "thread-1"

    client = MagicMock(spec=httpx.Client)
    assert draft_touch_was_sent(
        client,
        "token",
        user_email="sdr@test.com",
        prospect=prospect,
        subject="Hola Mia",
        thread_id="thread-1",
    )


@patch("app.services.sequence_gmail_draft_sent.mark_sequence_gmail_touch_sent")
@patch("app.services.sequence_gmail_draft_sent.draft_touch_was_sent", return_value=True)
@patch("app.services.sequence_gmail_draft_sent.gmail_draft_status", return_value="missing")
@patch("app.services.sequence_gmail_draft_sent._touch_log")
@patch("app.services.sequence_gmail_draft_sent._playbook_step")
@patch("app.services.sequence_gmail_draft_sent.sequence_email_touch_uses_gmail", return_value=True)
def test_reconcile_marks_pending_touch(
    _uses_gmail,
    mock_step,
    mock_touch_log,
    _draft_status,
    _was_sent,
    mock_mark,
):
    mock_step.return_value = MagicMock(channel="email")
    mock_touch_log.return_value = {
        "1": {
            "status": "generado",
            "gmail_draft_id": "draft-1",
            "subject": "Asunto",
            "message_body": "Cuerpo",
        }
    }
    db = MagicMock()
    user = MagicMock()
    campaign = MagicMock()
    prospect = MagicMock()
    prospect.id = 10
    prospect.email = "prospect@example.com"

    marked = reconcile_prospect_gmail_draft_sents(
        db,
        user=user,
        campaign=campaign,
        prospect=prospect,
        user_email="sdr@test.com",
        client=MagicMock(),
        access="token",
    )

    assert marked == [1]
    mock_mark.assert_called_once()
