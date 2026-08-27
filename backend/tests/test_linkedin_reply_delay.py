"""Tests for LinkedIn reply queue delay."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.services.linkedin_assisted_service import is_queue_eligible
from app.services.linkedin_reply_delay import (
    apply_reply_queue_delay,
    linkedin_reply_delay_minutes,
    reply_visible_in_queue,
)


def test_reply_visible_when_no_delay():
    prospect = MagicMock()
    prospect.linkedin_reply_available_at = None
    assert reply_visible_in_queue(prospect) is True


def test_reply_hidden_until_delay_elapsed():
    prospect = MagicMock()
    prospect.linkedin_reply_available_at = datetime.now(UTC) + timedelta(minutes=5)
    assert reply_visible_in_queue(prospect) is False
    prospect.linkedin_reply_available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert reply_visible_in_queue(prospect) is True


def test_is_queue_eligible_respects_delay(monkeypatch):
    prospect = MagicMock()
    prospect.linkedin_url = "https://www.linkedin.com/in/mia-alvarez/"
    prospect.linkedin_assisted_draft = "Hola Mia"
    prospect.linkedin_sdr_marked_sent_at = None
    prospect.linkedin_assist_status = "suggested"
    prospect.linkedin_reply_available_at = datetime.now(UTC) + timedelta(minutes=3)

    monkeypatch.setattr(
        "app.services.linkedin_assisted_service.is_real_linkedin_profile_url",
        lambda _u: True,
    )
    monkeypatch.setattr(
        "app.services.linkedin_assisted_service.read_assist_status",
        lambda _p: "suggested",
    )

    assert is_queue_eligible(prospect) is False

    prospect.linkedin_reply_available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert is_queue_eligible(prospect) is True


def test_apply_reply_queue_delay_zero_clears():
    prospect = MagicMock()
    prospect.linkedin_reply_available_at = datetime.now(UTC)
    assert apply_reply_queue_delay(prospect, minutes=0) is None
    assert prospect.linkedin_reply_available_at is None
