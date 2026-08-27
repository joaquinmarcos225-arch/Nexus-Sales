"""LinkedIn inbound must persist even when OpenAI reply draft fails."""

from unittest.mock import MagicMock, patch

from app.services.linkedin_inbound_sync import register_linkedin_inbound


@patch(
    "app.services.linkedin_reply_compose.compose_linkedin_inbound_reply",
    return_value=(
        "Hola Mia, sobre tu interés: Nexus automatiza outbound multicanal. "
        "¿Charlamos 15 min?\n\nSaludos,\nJoaquin"
    ),
)
@patch(
    "app.services.linkedin_assisted_service.prepare_linkedin_reply_after_inbound",
    side_effect=RuntimeError("openai down"),
)
@patch("app.services.linkedin_inbound_sync.process_linkedin_inbound_for_prospect", return_value=True)
def test_register_linkedin_inbound_survives_openai_failure(
    mock_process,
    mock_prepare,
    mock_compose,
):
    db = MagicMock()
    prospect = MagicMock(
        id=10,
        name="Mia Álvarez",
        company_name="Test Co",
        linkedin_url="https://www.linkedin.com/in/mia-alvarez/",
        sequence_paused=True,
        campaign_id=4,
    )
    campaign = MagicMock(id=4, company_id=1)

    with patch(
        "app.services.linkedin_assisted_service.is_real_linkedin_profile_url",
        return_value=True,
    ):
        out = register_linkedin_inbound(
            db,
            prospect=prospect,
            campaign=campaign,
            message="Hola, me interesa saber más.",
        )

    assert out["inserted"] is True
    assert out["reply_draft_ready"] is True
    draft = out["reply_draft"] or ""
    assert "Mia" in draft
    assert "consolidar prospectos, campañas y reportes" not in draft.lower()
    mock_compose.assert_called_once()
