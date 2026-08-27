"""Tests for unified Responder inbox."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services.responder_inbox_service import build_responder_inbox


def _campaign(*, cid=1, company_id=10, seller_id=5, name="Test"):
    return SimpleNamespace(id=cid, company_id=company_id, seller_id=seller_id, name=name)


def _prospect(**kw):
    base = {
        "id": 100,
        "campaign_id": 1,
        "name": "Ana Pérez",
        "company_name": "Acme",
        "last_inbound_at": datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        "linkedin_assisted_draft": "Hola Ana, ¿coordinamos?",
        "whatsapp_assisted_draft": None,
        "sequence_paused": True,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, *, prospects=None, messages=None, tasks=None):
        self.prospects = prospects or []
        self.messages = messages or []
        self.tasks = tasks or []

    def execute(self, _stmt):
        return _FakeResult([(p, _campaign()) for p in self.prospects])

    def scalars(self, _stmt):
        class _S:
            def __init__(self, items):
                self.items = items

            def first(self):
                return self.items[0] if self.items else None

        return _S(self.messages)

    def scalar(self, _stmt):
        return self.tasks[0] if self.tasks else None

    def get(self, _model, _id):
        return _campaign()


@patch("app.services.linkedin_assisted_service._task_action", return_value=("reply", True))
@patch("app.services.linkedin_assisted_service.reply_visible_in_queue", return_value=True)
def test_responder_inbox_linkedin_reply(_visible, _action):
    db = _FakeDb(prospects=[_prospect()], messages=[SimpleNamespace(message="Me interesa", channel="linkedin")])
    out = build_responder_inbox(db, company_id=10, seller_id=5)
    assert out["total"] == 1
    assert out["items"][0]["channel"] == "linkedin"
    assert "Hola Ana" in out["items"][0]["draft"]


@patch("app.services.linkedin_assisted_service._task_action", return_value=("message", False))
@patch("app.services.linkedin_assisted_service.reply_visible_in_queue", return_value=False)
def test_responder_inbox_whatsapp_reply(_visible, _action):
    p = _prospect(
        linkedin_assisted_draft=None,
        whatsapp_assisted_draft="Genial, ¿mañana 15 hs?",
    )
    db = _FakeDb(
        prospects=[p],
        messages=[SimpleNamespace(message="Dale", channel="whatsapp")],
    )
    out = build_responder_inbox(db, company_id=10, seller_id=5)
    assert out["total"] == 1
    assert out["items"][0]["channel"] == "whatsapp"


def test_nexus_3_template_validates():
    from app.core.sequence_templates import nexus_3_li_email_wa_plan, validate_plan

    plan = validate_plan(nexus_3_li_email_wa_plan())
    assert len(plan["steps"]) == 3
    assert plan["steps"][0]["channel"] == "linkedin"
    assert plan["steps"][1]["channel"] == "email"
    assert plan["steps"][2]["channel"] == "whatsapp"


def test_default_mvp_channels_include_whatsapp():
    from app.schemas.campaign_channels import DEFAULT_MVP_CHANNELS, coerce_allowed_channels

    assert "whatsapp" in DEFAULT_MVP_CHANNELS
    assert "whatsapp" in coerce_allowed_channels(None)
