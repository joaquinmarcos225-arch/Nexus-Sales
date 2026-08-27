"""Nexus Support: hilo por usuario + flag ops."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.automation_job_state import AutomationJobState
from app.models.company import Company
from app.models.support_ticket import SupportMessage, SupportThread  # noqa: F401
from app.models.user import User
from app.services.support import (
    add_message,
    get_or_create_company_thread,
    is_nexus_support_ops,
    list_ops_threads,
    serialize_thread,
    set_thread_status,
)
from app.services.support_observability import build_support_observability


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_ops_flag_uses_email_list(monkeypatch):
    monkeypatch.setenv("NEXUS_SUPPORT_OPS_EMAILS", "ops@nexus.com, joaquin@nexus.com")
    ops = User(
        company_id=9,
        first_name="Ops",
        last_name="",
        name="Ops",
        email="ops@nexus.com",
        role="sdr",
    )
    client = User(
        company_id=9,
        first_name="Ana",
        last_name="",
        name="Ana",
        email="ana@cliente.com",
        role="owner",
    )
    assert is_nexus_support_ops(ops) is True
    assert is_nexus_support_ops(client) is False


def test_ops_flag_fallback_ops_company(monkeypatch):
    monkeypatch.delenv("NEXUS_SUPPORT_OPS_EMAILS", raising=False)
    monkeypatch.setenv("NEXUS_OPS_COMPANY_ID", "1")
    owner = User(company_id=1, first_name="J", last_name="", name="J", email="j@x.com", role="owner")
    other = User(company_id=2, first_name="A", last_name="", name="A", email="a@x.com", role="owner")
    assert is_nexus_support_ops(owner) is True
    assert is_nexus_support_ops(other) is False


def test_thread_roundtrip_and_serialize():
    db = _session()
    company = Company(name="Cliente A", plan="starter", employee_count=1)
    db.add(company)
    db.flush()
    sdr = User(
        company_id=company.id,
        first_name="Ana",
        last_name="",
        name="Ana",
        email="ana@cliente.com",
        role="sdr",
    )
    ops = User(
        company_id=company.id,
        first_name="Ops",
        last_name="",
        name="Ops",
        email="ops@nexus.com",
        role="sdr",
    )
    db.add_all([sdr, ops])
    db.flush()

    t1 = get_or_create_company_thread(db, company_id=company.id, user=sdr)
    t2 = get_or_create_company_thread(db, company_id=company.id, user=sdr)
    assert t1.id == t2.id

    peer = User(
        company_id=company.id,
        first_name="Luis",
        last_name="",
        name="Luis",
        email="luis@cliente.com",
        role="sdr",
    )
    db.add(peer)
    db.flush()
    t_peer = get_or_create_company_thread(db, company_id=company.id, user=peer)
    assert t_peer.id != t1.id

    add_message(db, thread=t1, author=sdr, role="user", body="No me conecta Gmail")
    add_message(db, thread=t1, author=ops, role="support", body="Andá a Integraciones")
    db.commit()

    threads = list_ops_threads(db)
    assert len(threads) == 2
    loaded = next(t for t in threads if t.id == t1.id)
    ser = serialize_thread(loaded)
    assert ser["company_name"] == "Cliente A"
    assert ser["opened_by_email"] == "ana@cliente.com"
    assert ser["user_id"] == sdr.id
    assert ser["status"] == "open"
    assert ser["waiting"] is False
    assert ser["message_count"] == 2
    assert len(ser["messages"]) == 2
    assert ser["messages"][0]["role"] == "user"
    assert "Gmail" in ser["messages"][0]["text"]
    assert ser["messages"][1]["role"] == "support"

    set_thread_status(db, thread=t1, status="resolved")
    db.commit()
    ser2 = serialize_thread(t1)
    assert ser2["status"] == "resolved"
    assert ser2["waiting"] is False


def test_support_observability_empty_workspace(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PROSPEO_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr("app.services.support_observability.ns.scheduler_running", lambda: True)
    monkeypatch.setattr("app.services.support_observability.om.is_real_mode", lambda: True)

    db = _session()
    result = build_support_observability(db)

    assert result["summary"]["companies"] == 0
    assert result["summary"]["estimated_cost_per_sequence_usd"] == 0.20
    assert result["scheduler"]["running"] is True
    assert {p["key"] for p in result["providers"]} == {
        "openai",
        "prospeo",
        "brave",
        "gmail",
        "whatsapp",
    }
    assert all(row["used"] == 0 for row in result["channel_limits"])


def test_support_observability_accepts_sqlite_naive_job_datetimes(monkeypatch):
    monkeypatch.setattr("app.services.support_observability.ns.scheduler_running", lambda: True)
    monkeypatch.setattr("app.services.support_observability.om.is_real_mode", lambda: True)

    db = _session()
    db.add(
        AutomationJobState(
            job_key="automation:test",
            last_success_at=datetime.utcnow() - timedelta(minutes=5),
            run_count=1,
        )
    )
    db.commit()

    result = build_support_observability(db)

    assert result["jobs"][0]["status"] == "healthy"


def test_support_observability_includes_inbox_and_billing(monkeypatch):
    monkeypatch.setattr("app.services.support_observability.ns.scheduler_running", lambda: True)
    monkeypatch.setattr("app.services.support_observability.om.is_real_mode", lambda: True)

    db = _session()
    result = build_support_observability(db)

    assert "support_inbox" in result
    assert result["support_inbox"]["total"] == 0
    assert result["billing_cycle"]["basis"] == "planned"
    assert "planned_cogs_usd" in result["billing_cycle"]


def test_capacity_route_requires_auth():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get("/support/ops/capacity")
    assert response.status_code == 401
