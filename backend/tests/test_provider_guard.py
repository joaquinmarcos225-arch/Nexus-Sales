"""Tests guardas de cuota proveedor."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.automation_job_state import AutomationJobState
from app.services.provider_guard import (
    brave_quota_paused,
    mark_brave_quota_paused,
    prospeo_min_credits_to_source,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_mark_brave_pause_persists(monkeypatch):
    monkeypatch.setenv("BRAVE_QUOTA_PAUSE_SEC", "3600")
    db = _session()
    until = mark_brave_quota_paused(db, reason="402")
    db.commit()
    assert until > datetime.now(UTC)
    assert brave_quota_paused(db) is True


def test_prospeo_min_credits_default():
    assert prospeo_min_credits_to_source() >= 100


def test_sourcing_blocked_when_brave_paused(monkeypatch):
    monkeypatch.setenv("BRAVE_SOURCING_PAUSED", "1")
    from app.services.provider_guard import sourcing_providers_blocked

    blocked, reason = sourcing_providers_blocked()
    assert blocked is True
    assert "Brave" in (reason or "")
