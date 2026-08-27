"""Re-enrich teléfonos de campaña con WhatsApp."""

from types import SimpleNamespace
from unittest.mock import patch

from app.routes.campaigns import re_enrich_campaign_phones


def test_re_enrich_phones_requires_whatsapp_channel():
    campaign = SimpleNamespace(
        id=7,
        sequence_plan=None,
        allowed_channels='["linkedin","email"]',
    )
    db = SimpleNamespace(
        scalars=lambda *_a, **_k: type("R", (), {"all": lambda self: []})(),
        commit=lambda: None,
    )
    with patch(
        "app.services.campaign_sequence_channels.campaign_requires_whatsapp",
        return_value=False,
    ):
        try:
            re_enrich_campaign_phones(7, db=db, campaign=campaign)  # type: ignore[arg-type]
            assert False, "expected HTTPException"
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
