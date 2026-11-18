"""Banner global: sin Gmail/Calendar/WhatsApp → avisos arriba."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.auth.deps import _connection_alerts


def test_missing_gmail_calendar_and_whatsapp_emit_alerts():
    user = SimpleNamespace(id=11, company_id=5, role="sdr")
    db = MagicMock()

    def _scalars(stmt):
        result = MagicMock()
        result.all.return_value = []
        result.first.return_value = None
        return result

    def _execute(stmt, params=None):
        result = MagicMock()
        result.first.return_value = None
        return result

    db.scalars.side_effect = _scalars
    db.execute.side_effect = _execute

    with patch("app.auth.deps._plan_payment_alerts", return_value=[]):
        alerts = _connection_alerts(user, db)

    keys = {a["key"] for a in alerts}
    assert "google_channels" in keys
    assert "whatsapp_channel" in keys
    google = next(a for a in alerts if a["key"] == "google_channels")
    assert "Gmail" in google["title"]
    assert "Calendar" in google["title"]
    assert google["action_url"] == "/configuracion/integraciones"
