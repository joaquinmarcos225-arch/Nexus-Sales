"""WhatsApp Web asistido: tras mark-sent no regenerar frío ni resetear el toque."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.prospect import Prospect
from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_GENERADO,
    TOUCH_PENDIENTE,
    _completed_days,
    _maybe_reset_retryable_touch,
    _touch_entry_lacks_real_delivery_meta,
    _touch_log,
    complete_pending_whatsapp_sequence_touch,
)


def _assisted_wa_prospect(*, status: str = TOUCH_ENVIADO, assisted_flag: bool = True) -> Prospect:
    entry = {
        "status": status,
        "sent_at": "2026-08-09T15:00:00+00:00",
        "message_body": "Hola Ana, te escribo de Nexus.",
        "body": "Hola Ana, te escribo de Nexus.",
    }
    if assisted_flag:
        entry["whatsapp_assisted_sent"] = True
        entry["sdr_marked_sent"] = True
    return Prospect(
        id=42,
        company_id=1,
        campaign_id=3,
        sequence_started_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sequence_touch_log=json.dumps({"1": entry}),
        sequence_fired_milestones=json.dumps([1] if status == TOUCH_ENVIADO else []),
        sequence_playbook_draft=json.dumps(
            [{"day": 1, "channel": "whatsapp", "body": "Hola Ana, te escribo de Nexus."}]
        ),
        phone="+5491128942875",
        whatsapp="+5491128942875",
        whatsapp_assisted_draft=None,
        whatsapp_sdr_marked_sent_at=datetime(2026, 8, 9, 15, 0, tzinfo=UTC),
        whatsapp_assist_status="sent",
    )


def test_assisted_whatsapp_sent_has_real_delivery_meta(monkeypatch):
    monkeypatch.delenv("WHATSAPP_USE_CLOUD_API", raising=False)
    monkeypatch.setenv("NEXUS_REAL_MODE", "0")
    p = _assisted_wa_prospect()
    entry = _touch_log(p)["1"]
    assert _touch_entry_lacks_real_delivery_meta(p, 1, entry) is False


def test_assisted_whatsapp_sent_not_reset(monkeypatch):
    monkeypatch.delenv("WHATSAPP_USE_CLOUD_API", raising=False)
    monkeypatch.setenv("NEXUS_REAL_MODE", "0")
    p = _assisted_wa_prospect()
    assert _maybe_reset_retryable_touch(p, 1) is False
    assert _touch_log(p)["1"]["status"] == TOUCH_ENVIADO
    assert 1 in _completed_days(p)


def test_complete_pending_sets_assisted_flags():
    p = Prospect(
        id=9,
        company_id=1,
        campaign_id=1,
        sequence_started_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        sequence_touch_log=json.dumps(
            {
                "1": {
                    "status": TOUCH_GENERADO,
                    "message_body": "Hola frío",
                    "body": "Hola frío",
                }
            }
        ),
        sequence_fired_milestones="[]",
        phone="+5491111111111",
        whatsapp="+5491111111111",
        whatsapp_assisted_draft=None,
        whatsapp_sdr_marked_sent_at=datetime.now(UTC),
    )
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        sequence_channels_json=None,
        sequence_template_key=None,
    )
    db = MagicMock()
    db.get.return_value = campaign

    # Playbook default: day 1 may be email — force step via monkeypatch.
    from app.services import prospect_sequence as ps

    step = SimpleNamespace(day=1, channel="whatsapp", objective="opener")
    original = ps._playbook_step

    def _step(day, campaign=None):
        if int(day) == 1:
            return step
        return original(day, campaign)

    ps._playbook_step = _step
    try:
        closed = complete_pending_whatsapp_sequence_touch(db, prospect=p)
    finally:
        ps._playbook_step = original

    assert closed == 1
    entry = _touch_log(p)["1"]
    assert entry["status"] == TOUCH_ENVIADO
    assert entry.get("whatsapp_assisted_sent") is True
    assert entry.get("sdr_marked_sent") is True
    assert entry["status"] != TOUCH_PENDIENTE
