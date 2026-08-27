"""Tests — cola de llamadas asistidas."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.call_assisted_service import (
    prospect_call_target,
    prospect_has_callable_number,
    tel_href_for,
)
from app.services.lead_sourcing.prospeo_phone import merge_contact_channels


def test_merge_contact_channels_splits_mobile_and_landline():
    person = {
        "mobile": {"mobile": "+5491128942875", "revealed": True, "status": "VERIFIED"},
        "direct_phone": "+54 11 4376-2000",
    }
    ch = merge_contact_channels(person)
    assert ch.get("whatsapp_number") == "+5491128942875"
    assert ch.get("landline_phone") is not None
    assert ch.get("mobile_phone") == "+5491128942875"


def test_prospect_call_target_prefers_landline():
    p = SimpleNamespace(
        landline_phone="+54 11 4376-2000",
        whatsapp="+5491128942875",
        phone="+5491128942875",
    )
    digits, kind, display = prospect_call_target(p)
    assert kind == "landline"
    assert display
    assert digits.startswith("5411")


def test_prospect_has_callable_number_with_mobile_only():
    p = SimpleNamespace(
        landline_phone=None,
        whatsapp="+5491128942875",
        phone="+5491128942875",
    )
    assert prospect_has_callable_number(p) is True
    assert tel_href_for("5491128942875").startswith("tel:+")


def test_prospect_call_target_mobile_fallback():
    p = SimpleNamespace(landline_phone=None, whatsapp=None, phone="+5491122334455")
    _, kind, _ = prospect_call_target(p)
    assert kind == "mobile"
