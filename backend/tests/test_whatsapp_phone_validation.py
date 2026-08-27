"""Validación móvil WhatsApp — rechazo de fijos y números corruptos."""

from app.services.whatsapp_cloud_service import normalize_whatsapp_digits
from app.services.whatsapp_phone_validation import (
    is_argentina_landline_digits,
    is_usable_whatsapp_mobile,
    sanitize_whatsapp_mobile,
)


def test_accepts_argentina_mobile():
    assert is_usable_whatsapp_mobile("+5491128942875") is True
    assert is_usable_whatsapp_mobile("+54 9 3476 36-2762") is True
    assert sanitize_whatsapp_mobile("11 5289-4287") is not None


def test_rejects_caba_landline():
    # Fijo CABA típico (5411 + 8) — no tiene WhatsApp
    assert is_usable_whatsapp_mobile("+54 11 4376-2000") is False
    assert is_argentina_landline_digits("541143762000") is True
    assert normalize_whatsapp_digits("+54 11 4376-2000", None) is None


def test_rejects_interior_landline():
    assert is_usable_whatsapp_mobile("+54 351 456-7890") is False
    assert normalize_whatsapp_digits("+54 351 456-7890", None) is None


def test_rejects_masked_and_short():
    assert sanitize_whatsapp_mobile("+54 9 342 6**-****") is None
    assert sanitize_whatsapp_mobile("12345") is None


def test_normalize_keeps_valid_mobile():
    assert normalize_whatsapp_digits("+5491128942875", None) == "5491128942875"
    assert normalize_whatsapp_digits("+54 9 3476 36-2762", None) == "5493476362762"


def test_does_not_force_ar_on_ambiguous_foreign():
    # No prepender 54 a un número corto ambiguo
    assert normalize_whatsapp_digits("5551234567", None) is None or normalize_whatsapp_digits(
        "5551234567", None
    ).startswith("52")
