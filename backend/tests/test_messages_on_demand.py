"""Paso A: mensajes bajo demanda — scaffold NO genera copy real."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import prospect_sequence as seq
from app.services.lead_sourcing import sdr_playbook_outreach as sdr_pb


def test_bootstrap_scaffold_is_placeholders_only_no_ai():
    prospect = SimpleNamespace(
        id=1,
        name="Ana",
        company_name="Acme",
        sequence_playbook_draft=None,
        playbook_name=None,
        sequence_touch_log=None,
    )
    campaign = SimpleNamespace(
        id=10,
        product_id=None,
        sequence_plan={
            "mode": "fixed",
            "touches": [
                {"day": 1, "channel": "email", "objective": "opener"},
                {"day": 4, "channel": "linkedin", "objective": "bump"},
                {"day": 7, "channel": "whatsapp", "objective": "wa"},
            ],
        },
        allowed_channels=None,
    )
    db = MagicMock()

    with patch.object(sdr_pb, "generate_sdr_playbook_touch") as gen_sdr:
        out = seq.bootstrap_sequence_scaffold_fast(
            db, prospect=prospect, campaign=campaign, product=None
        )

    gen_sdr.assert_not_called()
    assert len(out["touches"]) >= 2
    for t in out["touches"]:
        assert "Se generará con IA al ejecutar el toque" in t["body_preview"]
        assert "[Vista previa" in t["body_preview"]
    stored = json.loads(prospect.sequence_playbook_draft)
    assert len(stored) == len(out["touches"])


def test_generate_ready_outreach_drafts_is_noop_batch():
    from app.services.lead_sourcing import service as ls

    db = MagicMock()
    campaign = SimpleNamespace(id=1)
    with patch.object(ls, "get_pipeline", return_value={"ok": True}) as gp:
        out = ls.generate_ready_outreach_drafts(db, campaign)
    gp.assert_called_once_with(db, campaign)
    assert out == {"ok": True}
