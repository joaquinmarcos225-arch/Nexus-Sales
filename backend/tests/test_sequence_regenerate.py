"""Tests de regeneración de secuencia."""

from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_FALLIDO,
    _count_sent_touches,
    _touch_log,
    explain_generate_sequence_block,
)


class _ProspectStub:
    def __init__(self, **kwargs):
        self.sequence_touch_log = kwargs.get("sequence_touch_log", "{}")
        self.sequence_started_at = kwargs.get("sequence_started_at")
        self.ownership_status = kwargs.get("ownership_status", "tomado")
        self.owner_user_id = kwargs.get("owner_user_id", 1)
        self.company_id = kwargs.get("company_id", 1)


class _UserStub:
    def __init__(self):
        self.id = 1
        self.company_id = 1
        self.role = "sdr"


def test_count_sent_touches():
    p = _ProspectStub(
        sequence_touch_log='{"1": {"status": "fallido"}, "4": {"status": "enviado"}}'
    )
    assert _count_sent_touches(p) == 1


def test_explain_generate_allows_force_regenerate_with_draft():
    user = _UserStub()
    prospect = _ProspectStub(
        sequence_touch_log='{"1": {"status": "fallido"}}',
        sequence_started_at="2026-01-01T00:00:00Z",
        ownership_status="en_secuencia",
        owner_user_id=1,
    )
    block = explain_generate_sequence_block(
        user,
        prospect,
        readiness={"is_ready": True},
        force_regenerate=True,
    )
    assert block is None


def test_explain_generate_blocks_force_regenerate_after_sent_touch(monkeypatch):
    from app.services import outreach_metrics as om

    monkeypatch.setattr(om, "is_sequence_testing_enabled", lambda: False)
    user = _UserStub()
    prospect = _ProspectStub(
        sequence_touch_log=f'{{"1": {{"status": "{TOUCH_ENVIADO}"}}}}',
        sequence_started_at="2026-01-01T00:00:00Z",
        ownership_status="en_secuencia",
        owner_user_id=1,
    )
    block = explain_generate_sequence_block(
        user,
        prospect,
        readiness={"is_ready": True},
        force_regenerate=True,
    )
    assert block is not None
    assert "toques enviados" in block.lower()


def test_explain_generate_allows_force_regenerate_with_sent_touch_in_testing(monkeypatch):
    from app.services import outreach_metrics as om

    monkeypatch.setattr(om, "is_sequence_testing_enabled", lambda: True)
    user = _UserStub()
    prospect = _ProspectStub(
        sequence_touch_log=f'{{"1": {{"status": "{TOUCH_ENVIADO}"}}}}',
        sequence_started_at="2026-01-01T00:00:00Z",
        ownership_status="en_secuencia",
        owner_user_id=1,
    )
    block = explain_generate_sequence_block(
        user,
        prospect,
        readiness={"is_ready": True},
        force_regenerate=True,
    )
    assert block is None
