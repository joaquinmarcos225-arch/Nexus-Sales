"""Tests cola Mail (mails enviados)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.mail_queue_service import (
    build_campaign_mail_queue,
    gmail_web_link_for,
    parse_mail_history_text,
)


def test_parse_mail_history_with_prefix_and_subject():
    raw = (
        "[Gmail · envío real]\n"
        "Asunto: Reunión 15 min\n\n"
        "Hola Ana,\n\n¿Te parece si hablamos?"
    )
    subject, body = parse_mail_history_text(raw)
    assert subject == "Reunión 15 min"
    assert body.startswith("Hola Ana")


def test_parse_mail_history_plain():
    subject, body = parse_mail_history_text("Solo un cuerpo sin asunto")
    assert subject == ""
    assert "Solo un cuerpo" in body


def test_gmail_web_link():
    assert gmail_web_link_for("abc123") == "https://mail.google.com/mail/u/0/#all/abc123"
    assert gmail_web_link_for("") is None


def test_build_campaign_mail_queue_filters_sent_only():
    prospect = SimpleNamespace(
        id=7,
        name="Ana",
        company_name="Acme",
        email="ana@acme.com",
    )
    sent = SimpleNamespace(
        id=101,
        prospect_id=7,
        campaign_id=3,
        channel="email",
        direction="outbound",
        gmail_message_id="mid-1",
        message="[Gmail · envío real]\nAsunto: Hola\n\nCuerpo",
        created_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC),
        prospect=prospect,
    )
    draft = SimpleNamespace(
        id=102,
        prospect_id=7,
        campaign_id=3,
        channel="email",
        direction="outbound",
        gmail_message_id=None,
        message="[Borrador]\nAsunto: No\n\nX",
        created_at=datetime(2026, 8, 20, 16, 0, tzinfo=UTC),
        prospect=prospect,
    )

    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=3)
    # Query only returns rows already filtered by service WHERE; we simulate that.
    db.scalars.return_value.all.return_value = [sent]

    out = build_campaign_mail_queue(db, 3, viewer=None)
    assert out.campaign_id == 3
    assert out.total == 1
    assert out.items[0].prospect_name == "Ana"
    assert out.items[0].subject == "Hola"
    assert out.items[0].body == "Cuerpo"
    assert out.items[0].gmail_message_id == "mid-1"
    assert "mid-1" in (out.items[0].gmail_web_link or "")
    del draft  # documents that drafts are excluded by SQL filter
