"""Tests go-live readiness."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.go_live import assess_company_go_live, assess_server_go_live


def test_assess_server_go_live_structure():
    out = assess_server_go_live()
    assert "checks" in out
    assert isinstance(out["checks"], list)
    assert len(out["checks"]) >= 5
    assert "prod_ready" in out


def test_assess_company_go_live_empty(monkeypatch):
    db = MagicMock()
    db.get.return_value = SimpleNamespace(name="Acme", plan="starter")
    db.scalars.return_value.all.return_value = []
    # campaign_count, sdr_count, assigned_sum
    db.scalar.side_effect = [0, 0, 0]
    db.scalars.return_value.first.return_value = SimpleNamespace(total_balance=100)

    out = assess_company_go_live(db, 1)
    assert out["company_id"] == 1
    assert out["ready"] is False
    assert out["pending_count"] >= 1
    assert out["credit_pool"] == 100
    assert out["credits_assigned"] == 0
