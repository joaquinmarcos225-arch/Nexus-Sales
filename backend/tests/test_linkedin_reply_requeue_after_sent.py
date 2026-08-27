"""Regression: inbound after mark-sent must re-queue reply draft automatically."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.linkedin_assisted_service import (
    STATUS_SENT,
    STATUS_SUGGESTED,
    is_queue_eligible,
    mark_draft_suggested,
    prepare_linkedin_reply_after_inbound,
    read_assist_status,
)


def _prospect(**kwargs):
    base = dict(
        id=34,
        name="Ivan Braga",
        linkedin_url="https://www.linkedin.com/in/ivan-braga-253454262/",
        linkedin_assisted_draft="Hola Ivan outbound viejo",
        linkedin_assist_status=STATUS_SENT,
        linkedin_assist_session_id="sess",
        linkedin_last_assisted_at=datetime.now(UTC),
        linkedin_sdr_marked_sent_at=datetime.now(UTC),
        linkedin_reply_available_at=None,
        linkedin_connection_status="connected",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_is_queue_eligible_blocks_sent_even_with_draft():
    p = _prospect(linkedin_assisted_draft="Réplica lista")
    assert is_queue_eligible(p) is False


def test_mark_draft_suggested_reopens_sent_into_queue():
    p = _prospect()
    campaign = MagicMock()
    mark_draft_suggested(MagicMock(), p, campaign, "Genial, ¿agendamos?", log_event=False)
    assert p.linkedin_assisted_draft.startswith("Genial")
    assert read_assist_status(p) == STATUS_SUGGESTED
    assert is_queue_eligible(p) is True


@patch(
    "app.services.linkedin_assisted_service.ensure_linkedin_draft",
    return_value="Genial, ¿te queda miércoles 10?",
)
@patch(
    "app.services.linkedin_assisted_service.is_real_linkedin_profile_url",
    return_value=True,
)
def test_prepare_reply_after_inbound_clears_sent_and_queues(_url, mock_ensure):
    p = _prospect()
    campaign = MagicMock()
    draft = prepare_linkedin_reply_after_inbound(MagicMock(), p, campaign)
    assert draft == "Genial, ¿te queda miércoles 10?"
    assert p.linkedin_sdr_marked_sent_at is None
    assert read_assist_status(p) == STATUS_SUGGESTED
    assert p.linkedin_assisted_draft is None  # cleared before ensure; ensure sets it
    mock_ensure.assert_called_once()
    # After ensure would set draft via mark_draft_suggested in real path;
    # eligibility with suggested + draft:
    p.linkedin_assisted_draft = draft
    assert is_queue_eligible(p) is True
