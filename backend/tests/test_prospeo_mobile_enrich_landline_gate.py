"""Gate: no pagar enrich_mobile si preview es fijo; no reintentar si no hay WA."""

from __future__ import annotations

from app.services.lead_sourcing import prospeo_phone as pp
from app.services.lead_sourcing.role_person_search import _maybe_enrich_if_needed


def test_preview_landline_caba_masked():
    assert pp.preview_looks_like_landline("+54 11 4376-****") is True
    assert pp.preview_looks_like_mobile("+54 11 4376-****") is False


def test_preview_mobile_ar_masked():
    assert pp.preview_looks_like_mobile("+54 9 342 6**-****") is True
    assert pp.preview_looks_like_landline("+54 9 342 6**-****") is False


def test_decide_enrich_skips_landline_preview():
    person = {
        "person_id": "p1",
        "phone": "+54 11 4376-****",
        "email": {"email": "a@b.com", "revealed": True},
    }
    assert pp.person_phone_preview_is_landline(person) is True
    assert pp.decide_enrich_mobile(person, want_mobile=True) is False
    assert pp.should_skip_enrich_mobile(person) is True


def test_decide_enrich_allows_masked_mobile_preview():
    person = {
        "person_id": "p2",
        "mobile": {"mobile": "+54 9 11 2**-****", "revealed": False},
    }
    assert pp.preview_looks_like_mobile("+54 9 11 2**-****") is True
    assert pp.decide_enrich_mobile(person, want_mobile=True) is True


def test_decide_enrich_skips_after_failed_attempt():
    person = {"person_id": "p3", "_nexus_skip_mobile_enrich": True}
    assert pp.decide_enrich_mobile(person, want_mobile=True) is False


def test_apply_enrich_marks_skip_when_no_wa():
    original = {"person_id": "p4", "name": "Ana"}
    enriched = {"direct_phone": "+54 11 4376-2000", "phone": "+54 11 4376-2000"}
    merged = pp.apply_enrich_mobile_result(original, enriched, requested_mobile=True)
    assert merged.get("_nexus_mobile_enrich_done") is True
    assert merged.get("_nexus_skip_mobile_enrich") is True
    assert pp.person_has_usable_mobile(merged) is False
    assert pp.decide_enrich_mobile(merged, want_mobile=True) is False


def test_apply_enrich_keeps_wa_mobile():
    original = {"person_id": "p5"}
    enriched = {
        "mobile": {
            "mobile": "+5491128942875",
            "revealed": True,
            "status": "VERIFIED",
        }
    }
    merged = pp.apply_enrich_mobile_result(original, enriched, requested_mobile=True)
    assert pp.person_has_usable_mobile(merged) is True
    assert merged.get("_nexus_skip_mobile_enrich") is not True
    # Ya tiene WA → no volver a pagar
    assert pp.decide_enrich_mobile(merged, want_mobile=True) is False


def test_maybe_enrich_skips_mobile_api_for_landline_preview(monkeypatch):
    captured: dict = {}

    def fake_enrich(pid, *, require_mobile=False, enrich_mobile=None):
        captured["require_mobile"] = require_mobile
        return {"person_id": pid, "email": {"email": "x@y.com", "revealed": True}}

    monkeypatch.setattr(
        "app.services.lead_sourcing.role_person_search.enrich_person_by_id",
        fake_enrich,
    )
    person = {
        "person_id": "land1",
        "phone": "+54 11 4800-****",
        "company": {"name": "Acme"},
    }
    out = _maybe_enrich_if_needed(person, require_mobile=True)
    # Puede enriquecer email (require_mobile=False en API) pero no móvil
    assert captured.get("require_mobile") is False
    assert out.get("_nexus_skip_mobile_enrich") is True
