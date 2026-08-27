"""Tests for LinkedIn inbound registration."""

from unittest.mock import MagicMock, patch

from app.services.linkedin_inbound_sync import (
    LINKEDIN_INBOUND_PREFIX,
    process_linkedin_inbound_for_prospect,
    register_linkedin_inbound,
)


@patch("app.services.linkedin_inbound_sync.pipeline_sync")
@patch("app.services.prospect_commercial_state.sync_commercial_state_from_inbound")
@patch("app.services.linkedin_inbound_sync.mseq")
@patch("app.services.linkedin_inbound_sync.followup_engine")
@patch("app.services.linkedin_inbound_sync.ci")
@patch("app.services.linkedin_inbound_sync.campaign_education_blob", return_value="")
@patch("app.services.linkedin_inbound_sync._conversation_digest_rows", return_value="")
@patch("app.services.linkedin_inbound_sync._has_pending_hot_lead", return_value=False)
@patch("app.services.linkedin_inbound_sync._has_pending_review_inbound", return_value=False)
def test_process_linkedin_inbound_inserts_message(
    _review,
    _hot,
    _digest,
    _edu,
    mock_ci,
    mock_followup,
    mock_mseq,
    _pcs,
    _pipe,
):
    mock_ci.classify_inbound_full.return_value = MagicMock(
        objection_type=None,
        interest_level="medium",
        prospect_timing_hold=False,
        defer_resume_at_iso=None,
    )
    mock_ci.prospect_status_from_inbound_signals.side_effect = lambda status, _sig: status
    mock_ci.timing_deferral_should_apply.return_value = False
    mock_ci.normalize_inbound_text_for_classification.return_value = "hola"
    mock_mseq.prospect_in_meeting_priority.return_value = False

    db = MagicMock()
    db.scalar.return_value = 0
    prospect = MagicMock(id=10, status="contacted", interest_level=None)
    campaign = MagicMock(id=4, company_id=1)

    ok = process_linkedin_inbound_for_prospect(
        db,
        prospect=prospect,
        campaign=campaign,
        inbound_plain="Hola, me interesa",
        linkedin_message_id="li-msg-1",
    )
    assert ok is True
    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added.channel == "linkedin"
    assert added.direction == "inbound"
    assert LINKEDIN_INBOUND_PREFIX in added.message
    mock_followup.record_prospect_inbound.assert_called_once()
    mock_mseq.on_inbound_pause_sequence.assert_called_once()


@patch(
    "app.services.linkedin_assisted_service.prepare_linkedin_reply_after_inbound",
    return_value="Gracias por responder...",
)
@patch("app.services.linkedin_inbound_sync.process_linkedin_inbound_for_prospect", return_value=True)
def test_register_linkedin_inbound_prepares_draft(mock_process, mock_prepare):
    db = MagicMock()
    prospect = MagicMock(id=10, sequence_paused=True)
    campaign = MagicMock(id=4)

    out = register_linkedin_inbound(
        db,
        prospect=prospect,
        campaign=campaign,
        message="Sí, contame más",
        linkedin_message_id="li-2",
    )
    assert out["inserted"] is True
    assert out["reply_draft_ready"] is True
    mock_prepare.assert_called_once()


def test_register_ignores_meeting_confirmation_echo():
    """Confirmación «Te agendé…» nunca debe crear Responder."""
    from app.services.linkedin_inbound_sync import (
        _looks_like_nexus_meeting_confirmation,
        register_linkedin_inbound,
    )

    confirm = (
        "Perfecto.\n\n"
        "Te agendé para Lunes 10:00.\n\n"
        "Acá tenés la invitación:\n"
        "https://www.google.com/calendar/event?eid=abc\n\n"
        "Nos vemos ahí."
    )
    assert _looks_like_nexus_meeting_confirmation(confirm) is True

    db = MagicMock()
    prospect = MagicMock(id=48, sequence_paused=False, linkedin_assisted_draft=None)
    campaign = MagicMock(id=5)
    out = register_linkedin_inbound(
        db,
        prospect=prospect,
        campaign=campaign,
        message=confirm,
        linkedin_message_id="echo-confirm-1",
    )
    assert out.get("echo_ignored") is True
    assert out.get("inserted") is False
    assert out.get("reply_draft_ready") is False
