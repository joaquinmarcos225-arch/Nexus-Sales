"""Paso 2: si search ya trae móvil, enrich no vuelve a pedir enrich_mobile."""

from __future__ import annotations

from app.services.lead_sourcing import b2c_person_search as b2c
from app.services.lead_sourcing import role_person_search as role


def _person_with_mobile_no_email() -> dict:
    return {
        "person_id": "pid-1",
        "full_name": "Ana Test",
        "company": {"name": "Acme SA"},
        "mobile": {"mobile": "+5491112345678", "revealed": True, "status": "VERIFIED"},
    }


def test_role_enrich_skips_mobile_when_search_already_has_phone(monkeypatch):
    captured: dict = {}

    def fake_enrich(pid, *, require_mobile=False, enrich_mobile=None):
        captured["require_mobile"] = require_mobile
        return {"email": {"email": "ana@acme.test", "revealed": True}}

    monkeypatch.setattr(role, "enrich_person_by_id", fake_enrich)
    out = role._maybe_enrich_if_needed(_person_with_mobile_no_email(), require_mobile=True)
    assert captured["require_mobile"] is False
    assert (out.get("email") or {}).get("email") == "ana@acme.test"


def test_role_enrich_requests_mobile_when_missing(monkeypatch):
    captured: dict = {}

    def fake_enrich(pid, *, require_mobile=False, enrich_mobile=None):
        captured["require_mobile"] = require_mobile
        return {
            "mobile": {"mobile": "+5491199999999", "revealed": True},
            "email": {"email": "bob@acme.test", "revealed": True},
        }

    monkeypatch.setattr(role, "enrich_person_by_id", fake_enrich)
    person = {
        "person_id": "pid-2",
        "full_name": "Bob",
        "company": {"name": "Acme"},
        # sin email ni móvil usable
    }
    role._maybe_enrich_if_needed(person, require_mobile=True)
    assert captured["require_mobile"] is True


def test_role_enrich_noop_when_email_company_and_mobile_present(monkeypatch):
    calls = {"n": 0}

    def fake_enrich(*_a, **_k):
        calls["n"] += 1
        return {}

    monkeypatch.setattr(role, "enrich_person_by_id", fake_enrich)
    person = {
        "person_id": "pid-3",
        "full_name": "Cara",
        "company": {"name": "Acme"},
        "email": {"email": "cara@acme.test", "revealed": True},
        "mobile": {"mobile": "+5491111111111", "revealed": True},
    }
    out = role._maybe_enrich_if_needed(person, require_mobile=True)
    assert calls["n"] == 0
    assert out is person


def test_b2c_enrich_skips_mobile_when_search_already_has_phone(monkeypatch):
    captured: dict = {}

    def fake_enrich(pid, *, require_mobile=False, enrich_mobile=None):
        captured["require_mobile"] = require_mobile
        return {"email": {"email": "ana@acme.test", "revealed": True}}

    monkeypatch.setattr(b2c, "enrich_person_by_id", fake_enrich)
    b2c._maybe_enrich_person(_person_with_mobile_no_email(), require_mobile=True)
    assert captured["require_mobile"] is False
