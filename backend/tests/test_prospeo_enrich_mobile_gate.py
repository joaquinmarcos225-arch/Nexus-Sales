"""Gate enrich_mobile: solo pedir móvil Prospeo cuando hace falta."""

from __future__ import annotations

from app.services.lead_sourcing.providers import prospeo_mvp as mvp


def test_enrich_person_by_id_skips_mobile_when_not_required(monkeypatch):
    captured: dict = {}

    def fake_post(_url, body):
        captured["body"] = body
        return {"person": {"person_id": "p1"}}

    monkeypatch.setattr(mvp, "_post_json", fake_post)
    out = mvp.enrich_person_by_id("p1", require_mobile=False)
    assert out.get("person_id") == "p1"
    assert captured["body"]["enrich_mobile"] is False
    assert "only_verified_mobile" not in captured["body"]


def test_enrich_person_by_id_requests_verified_mobile_when_required(monkeypatch):
    captured: dict = {}

    def fake_post(_url, body):
        captured["body"] = body
        return {"person": {"person_id": "p2"}}

    monkeypatch.setattr(mvp, "_post_json", fake_post)
    mvp.enrich_person_by_id("p2", require_mobile=True)
    assert captured["body"]["enrich_mobile"] is True
    assert captured["body"]["only_verified_mobile"] is True


def test_enrich_person_by_id_fallback_can_request_mobile_without_filter(monkeypatch):
    captured: dict = {}

    def fake_post(_url, body):
        captured["body"] = body
        return {"person": {"person_id": "p3"}}

    monkeypatch.setattr(mvp, "_post_json", fake_post)
    mvp.enrich_person_by_id("p3", require_mobile=False, enrich_mobile=True)
    assert captured["body"]["enrich_mobile"] is True
    assert "only_verified_mobile" not in captured["body"]


def test_enrich_person_record_defaults_no_mobile(monkeypatch):
    captured: dict = {}

    def fake_post(_url, body):
        captured["body"] = body
        return {"person": {"full_name": "Ada"}}

    monkeypatch.setattr(mvp, "_post_json", fake_post)
    mvp.enrich_person_record(
        first_name="Ada",
        last_name="Lovelace",
        full_name="Ada Lovelace",
        company_name="Analytical",
        company_website="https://analytical.example",
    )
    assert captured["body"]["enrich_mobile"] is False


def test_enrich_person_record_requests_mobile_when_asked(monkeypatch):
    captured: dict = {}

    def fake_post(_url, body):
        captured["body"] = body
        return {"person": {"full_name": "Ada"}}

    monkeypatch.setattr(mvp, "_post_json", fake_post)
    mvp.enrich_person_record(
        first_name="Ada",
        last_name="Lovelace",
        full_name="Ada Lovelace",
        company_name="Analytical",
        company_website="https://analytical.example",
        enrich_mobile=True,
    )
    assert captured["body"]["enrich_mobile"] is True
