"""Validación de teléfonos aptos para WhatsApp (móvil real, no fijo ni basura)."""

from __future__ import annotations

import re

from app.services.whatsapp_cloud_service import is_masked_phone, sanitize_stored_phone

_DIGITS_RE = re.compile(r"\D+")

# Argentina móvil E.164: 549 + área + número (10–11 dígitos tras el 9).
_AR_MOBILE_E164 = re.compile(r"^549\d{10,11}$")
# Formato legacy Meta/interior: 54 + área + 15 + número.
_AR_MOBILE_15 = re.compile(r"^54\d{2,4}15\d{6,8}$")
# Fijo CABA típico: 5411 + 8 dígitos (sin 9 móvil).
_AR_LANDLINE_CABA = re.compile(r"^5411\d{8}$")
# Fijos interior: 54 + área 2–4 dígitos + 7–8 dígitos (sin 9 ni 15 móvil).
_AR_LANDLINE_INTERIOR = re.compile(
    r"^54(2[0-9]|3[0-9]|4[0-9]|5[0-9]|6[0-9]|7[0-9]|8[0-9])\d{7,8}$"
)

# Local AR sin país.
_AR_LOCAL_MOBILE = re.compile(r"^(?:9\d{10}|11\d{8}|15\d{8})$")

# Otros países LATAM/US frecuentes en campañas.
_INTL_MOBILE = (
    re.compile(r"^1\d{10}$"),  # US/CA
    re.compile(r"^55\d{10,11}$"),  # BR
    re.compile(r"^52\d{10}$"),  # MX
    re.compile(r"^56\d{9}$"),  # CL
    re.compile(r"^57\d{10}$"),  # CO
    re.compile(r"^51\d{9}$"),  # PE
    re.compile(r"^598\d{8,9}$"),  # UY
    re.compile(r"^34\d{9}$"),  # ES
)


def digits_only(raw: str | None) -> str:
    return _DIGITS_RE.sub("", (raw or "").strip())


def is_trivially_invalid(digits: str) -> bool:
    if len(digits) < 10:
        return True
    if len(set(digits)) <= 2:
        return True
    if digits.startswith(("0800", "0810", "900")):
        return True
    return False


def is_argentina_landline_digits(digits: str) -> bool:
    if not digits.startswith("54"):
        return False
    if _AR_MOBILE_E164.match(digits) or _AR_MOBILE_15.match(digits):
        return False
    if _AR_LANDLINE_CABA.match(digits):
        return True
    if _AR_LANDLINE_INTERIOR.match(digits):
        return True
    # 54 + resto sin 9 móvil ni 15 → sospechoso de fijo
    if digits.startswith("54") and not digits.startswith("549") and "15" not in digits[2:6]:
        if 11 <= len(digits) <= 13:
            return True
    return False


def is_whatsapp_mobile_digits(digits: str) -> bool:
    """True si los dígitos parecen un móvil usable en WhatsApp."""
    if is_trivially_invalid(digits):
        return False
    if is_argentina_landline_digits(digits):
        return False

    if _AR_MOBILE_E164.match(digits) or _AR_MOBILE_15.match(digits):
        return True
    if _AR_LOCAL_MOBILE.match(digits):
        return True
    for pat in _INTL_MOBILE:
        if pat.match(digits):
            return True
    # Internacional genérico: 12–15 dígitos, no empieza en 0
    if 12 <= len(digits) <= 15 and not digits.startswith("0"):
        if not digits.startswith("54"):
            return True
    return False


def normalize_local_ar_to_e164(digits: str) -> str:
    """Convierte formatos locales AR conocidos a 549… (solo si aplica)."""
    if digits.startswith("54"):
        return digits
    if _AR_LOCAL_MOBILE.match(digits):
        if digits.startswith("9") and len(digits) == 11:
            return "54" + digits
        if digits.startswith("11") and len(digits) == 10:
            return "54911" + digits[2:]
        if digits.startswith("15") and len(digits) == 10:
            return "54911" + digits[2:]
    if len(digits) == 10 and digits.startswith("11"):
        return "54911" + digits[2:]
    return digits


def sanitize_whatsapp_mobile(raw: str | None) -> str | None:
    """
    Devuelve el teléfono original limpio si parece móvil WA; None si fijo/enmascarado/inválido.
    """
    cleaned = sanitize_stored_phone(raw)
    if not cleaned or is_masked_phone(cleaned):
        return None
    digits = digits_only(cleaned)
    if digits.startswith("00"):
        digits = digits[2:]
    digits = normalize_local_ar_to_e164(digits)
    if not is_whatsapp_mobile_digits(digits):
        return None
    return cleaned


def is_usable_whatsapp_mobile(raw: str | None) -> bool:
    return sanitize_whatsapp_mobile(raw) is not None


def is_landline_phone(raw: str | None) -> bool:
    """True si parece teléfono fijo (no móvil WA)."""
    cleaned = sanitize_stored_phone(raw)
    if not cleaned or is_masked_phone(cleaned):
        return False
    if is_usable_whatsapp_mobile(cleaned):
        return False
    digits = digits_only(cleaned)
    if digits.startswith("00"):
        digits = digits[2:]
    digits = normalize_local_ar_to_e164(digits)
    if is_trivially_invalid(digits):
        return False
    if is_argentina_landline_digits(digits):
        return True
    # Genérico: tiene longitud de teléfono pero no califica como móvil.
    return len(digits) >= 10


def classify_phone_kind(raw: str | None) -> str:
    """mobile | landline | unknown"""
    if is_usable_whatsapp_mobile(raw):
        return "mobile"
    if is_landline_phone(raw):
        return "landline"
    return "unknown"


def sanitize_landline_phone(raw: str | None) -> str | None:
    if not is_landline_phone(raw):
        return None
    return sanitize_stored_phone(raw)
