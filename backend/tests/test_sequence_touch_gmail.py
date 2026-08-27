from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.sequence_touch_gmail import (
    deliver_sequence_email_touch_via_gmail,
    sequence_email_touch_uses_gmail,
)


def test_sequence_email_touch_uses_gmail_any_email_day():
    with patch("app.services.sequence_touch_gmail.om.is_real_mode", return_value=True):
        assert sequence_email_touch_uses_gmail(day=1, channel="email") is True
        assert sequence_email_touch_uses_gmail(day=4, channel="email") is True
        assert sequence_email_touch_uses_gmail(day=4, channel="linkedin") is False
        assert sequence_email_touch_uses_gmail(day=10, channel="email") is True


@patch("app.services.sequence_touch_gmail.om.is_real_mode", return_value=True)
@patch("app.services.sequence_touch_gmail.create_draft_for_user")
@patch("app.services.sequence_touch_gmail.get_valid_gmail_connection")
def test_deliver_sequence_email_touch_via_gmail(mock_conn, mock_draft, _real):
    mock_conn.return_value = ("token", MagicMock(external_email="sdr@test.com"))
    mock_draft.return_value = {
        "draft_id": "draft-1",
        "message_id": "gm-1",
        "thread_id": "th-1",
        "gmail_web_link": "https://mail.google.com",
    }

    db = MagicMock()
    user = MagicMock(id=6)
    campaign = MagicMock(company_id=1, seller_id=6, calendar_link="")
    prospect = MagicMock(
        id=10,
        email="fernandezjoaquinjose@gmail.com",
        gmail_thread_id=None,
        status="compatible",
        preferred_channel=None,
    )

    out = deliver_sequence_email_touch_via_gmail(
        db,
        user=user,
        campaign=campaign,
        prospect=prospect,
        day=1,
        subject="Hola",
        body="Cuerpo del día 1",
    )
    assert out["gmail_draft_id"] == "draft-1"
    assert out["gmail_message_id"] == "gm-1"
    mock_draft.assert_called_once()
    db.add.assert_called_once()


@patch("app.services.sequence_touch_gmail.om.is_real_mode", return_value=True)
def test_deliver_blocks_demo_email(_real):
    db = MagicMock()
    user = MagicMock(id=6)
    campaign = MagicMock(company_id=1, seller_id=6)
    prospect = MagicMock(id=10, email="demo.prospect.1@mail.nexus-sales.local")

    with pytest.raises(HTTPException) as exc:
        deliver_sequence_email_touch_via_gmail(
            db,
            user=user,
            campaign=campaign,
            prospect=prospect,
            day=1,
            subject="Hola",
            body="Test",
        )
    assert exc.value.status_code == 400
