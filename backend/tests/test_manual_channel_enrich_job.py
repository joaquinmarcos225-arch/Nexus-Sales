"""Búsqueda async de canales faltantes post-insert."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.services.manual_channel_enrich_job import (
    STATUS_SEARCHING,
    STATUS_SKIPPED,
    begin_manual_channel_enrich,
    has_enrich_anchor,
)
from app.services.manual_prospect_channel_enrichment import enrich_missing_channels


def _prospect(**kw):
    defaults = dict(
        id=1,
        name="Ana Pérez",
        company_name="Acme Latam",
        company_website=None,
        email=None,
        linkedin_url="https://www.linkedin.com/in/ana-perez",
        phone=None,
        whatsapp=None,
        channel_enrich_status="none",
        channel_enrich_deadline_at=None,
        channel_enrich_message=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_has_enrich_anchor_email_only():
    assert has_enrich_anchor(
        _prospect(name="Contacto", linkedin_url=None, email="solo@mail.com", company_name="—")
    )


def test_has_enrich_anchor_whatsapp_only():
    assert has_enrich_anchor(
        _prospect(name="Contacto", linkedin_url=None, email=None, whatsapp="+54911", company_name="—")
    )


def test_has_enrich_anchor_name_and_surname_only():
    assert has_enrich_anchor(
        _prospect(name="Ana Pérez", linkedin_url=None, email=None, whatsapp=None, company_name="—")
    )


def test_begin_enrich_marks_searching_when_missing_channels():
    p = _prospect()
    plan = {
        "steps": [
            {"day": 1, "channel": "email"},
            {"day": 2, "channel": "linkedin"},
            {"day": 3, "channel": "whatsapp"},
        ]
    }
    out = begin_manual_channel_enrich(None, p, sequence_plan=plan)
    assert out["enriching"] is True
    assert p.channel_enrich_status == STATUS_SEARCHING
    assert p.channel_enrich_deadline_at is not None
    assert "email" in (p.channel_enrich_message or "").lower() or "whatsapp" in (
        p.channel_enrich_message or ""
    ).lower()


def test_begin_enrich_skips_when_nothing_missing():
    p = _prospect(
        email="ana@acme.com",
        phone="+5491112345678",
        linkedin_url="https://www.linkedin.com/in/ana-perez",
    )
    plan = {
        "steps": [
            {"day": 1, "channel": "email"},
            {"day": 2, "channel": "linkedin"},
            {"day": 3, "channel": "whatsapp"},
        ]
    }
    out = begin_manual_channel_enrich(None, p, sequence_plan=plan)
    assert out["enriching"] is False
    assert p.channel_enrich_status == STATUS_SKIPPED


def test_enrich_respects_past_deadline():
    p = _prospect(email=None, phone=None)
    plan = {"steps": [{"day": 1, "channel": "email"}, {"day": 2, "channel": "whatsapp"}]}
    past = datetime.now(UTC) - timedelta(seconds=5)
    with patch(
        "app.services.manual_prospect_channel_enrichment._try_prospeo_enrich"
    ) as mock_prospeo:
        out = enrich_missing_channels(None, p, sequence_plan=plan, deadline_at=past)
        mock_prospeo.assert_not_called()
    assert out.get("timed_out") is True
    assert out["filled"] == {}


def test_enrich_retries_while_deadline_allows(monkeypatch):
    """No cortar al primer vacío: varias rondas Prospeo/Brave hasta hallar o vencer."""
    p = _prospect(email=None, phone=None, linkedin_url="https://www.linkedin.com/in/ana-perez")
    plan = {"steps": [{"day": 1, "channel": "email"}, {"day": 2, "channel": "whatsapp"}]}
    future = datetime.now(UTC) + timedelta(seconds=25)
    calls = {"n": 0}

    def _prospeo(_prospect, missing, **_kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return {}
        _prospect.email = "ana@acme.com"
        missing.discard("email")
        return {"email": "ana@acme.com"}

    monkeypatch.setattr(
        "app.services.manual_prospect_channel_enrichment._try_prospeo_enrich",
        _prospeo,
    )
    monkeypatch.setattr(
        "app.services.manual_prospect_channel_enrichment.time.sleep",
        lambda _s: None,
    )
    out = enrich_missing_channels(None, p, sequence_plan=plan, deadline_at=future)
    assert calls["n"] >= 2
    assert out["filled"].get("email") == "ana@acme.com"
    assert "email" not in (out.get("missing_after") or [])


def test_begin_enrich_default_max_seconds_at_least_2_min():
    from app.services.manual_channel_enrich_job import MANUAL_CHANNEL_ENRICH_MAX_SECONDS

    assert MANUAL_CHANNEL_ENRICH_MAX_SECONDS >= 120
    p = _prospect()
    plan = {"steps": [{"day": 1, "channel": "email"}, {"day": 2, "channel": "whatsapp"}]}
    out = begin_manual_channel_enrich(None, p, sequence_plan=plan)
    assert out["max_seconds"] >= 120
    assert p.channel_enrich_deadline_at is not None
    rem = (p.channel_enrich_deadline_at - datetime.now(UTC)).total_seconds()
    assert rem > 90
