"""Teléfonos Prospeo — extracción móvil vs fijo + capacidades del plan."""

from __future__ import annotations

import re
from typing import Any

from app.services.lead_sourcing.providers.prospeo_mvp import extract_email_phone
from app.services.whatsapp_cloud_service import is_masked_phone, sanitize_stored_phone
from app.services.whatsapp_phone_validation import (
    classify_phone_kind,
    digits_only,
    is_argentina_landline_digits,
    sanitize_landline_phone,
    sanitize_whatsapp_mobile,
)

# Marcadores en el dict person (pipeline en memoria): no re-gastar enrich_mobile.
_SKIP_MOBILE_ENRICH_KEY = "_nexus_skip_mobile_enrich"
_MOBILE_ENRICH_DONE_KEY = "_nexus_mobile_enrich_done"


def extract_prospeo_phones(
    person: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    """
    Devuelve (mobile, landline, whatsapp_number, phone_source).

    - mobile / whatsapp: solo celular validado (objeto ``mobile`` de Prospeo).
    - landline: fijo u otro teléfono que no es móvil WA.
    """
    if not isinstance(person, dict):
        return None, None, None, None

    mobile: str | None = None
    landline: str | None = None
    source: str | None = None

    mobile_obj = person.get("mobile")
    if isinstance(mobile_obj, dict):
        mob = mobile_obj.get("mobile") or mobile_obj.get("mobile_international")
        if mob and str(mob).strip() and not is_masked_phone(str(mob)):
            wa = sanitize_whatsapp_mobile(str(mob).strip())
            if wa:
                mobile = wa
                source = (
                    "prospeo_enrich_mobile"
                    if mobile_obj.get("revealed") is True
                    else "prospeo_search_mobile"
                )

    if not mobile:
        for key in ("mobile_phone",):
            val = person.get(key)
            if isinstance(val, str) and val.strip():
                wa = sanitize_whatsapp_mobile(val.strip())
                if wa:
                    mobile = wa
                    source = source or "prospeo_search_mobile"
                    break

    for key in ("direct_phone", "phone", "phone_number"):
        val = person.get(key)
        raw = None
        if isinstance(val, str) and val.strip():
            raw = val.strip()
        elif isinstance(val, dict):
            inner = val.get("number") or val.get("phone") or val.get("mobile")
            if inner and str(inner).strip():
                raw = str(inner).strip()
        if not raw or is_masked_phone(raw):
            continue
        kind = classify_phone_kind(raw)
        if kind == "mobile" and not mobile:
            mobile = sanitize_whatsapp_mobile(raw)
            source = source or "prospeo_search_phone"
        elif kind == "landline" and not landline:
            landline = sanitize_landline_phone(raw)
            source = source or "prospeo_search_landline"

    whatsapp = mobile
    return mobile, landline, whatsapp, source


def merge_contact_channels(person: dict[str, Any]) -> dict[str, Any]:
    """Email + teléfonos + linkedin normalizado desde payload Prospeo."""
    from app.services.lead_sourcing.linkedin_identity import normalize_linkedin_url

    email, legacy_phone = extract_email_phone(person)
    mobile, landline, whatsapp, phone_source = extract_prospeo_phones(person)
    if not mobile and legacy_phone:
        wa_legacy = sanitize_whatsapp_mobile(legacy_phone)
        if wa_legacy:
            mobile = wa_legacy
            whatsapp = wa_legacy
            phone_source = phone_source or "prospeo_search_mobile"
        elif not landline:
            landline = sanitize_landline_phone(legacy_phone)

    linkedin = (
        person.get("linkedin_url")
        or person.get("linkedin")
        or person.get("person_linkedin_url")
    )
    return {
        "email": email,
        "phone": mobile or landline,
        "mobile_phone": mobile,
        "landline_phone": landline,
        "whatsapp_number": whatsapp,
        "phone_source": phone_source,
        "linkedin_url": normalize_linkedin_url(str(linkedin) if linkedin else None),
    }


def person_has_usable_mobile(person: dict[str, Any]) -> bool:
    """True si Prospeo devolvió un móvil completo y válido para WhatsApp."""
    ch = merge_contact_channels(person)
    return bool(ch.get("whatsapp_number"))


def person_mobile_verified(person: dict[str, Any]) -> bool:
    if not isinstance(person, dict):
        return False
    mobile_obj = person.get("mobile")
    if not isinstance(mobile_obj, dict):
        return False
    return str(mobile_obj.get("status") or "").strip().upper() == "VERIFIED"


def _raw_phone_candidates(person: dict[str, Any]) -> list[str]:
    """Previews / números crudos (incluye enmascarados) para clasificar antes de pagar."""
    if not isinstance(person, dict):
        return []
    out: list[str] = []

    def _add(val: object) -> None:
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
        elif isinstance(val, dict):
            for k in ("mobile", "mobile_international", "number", "phone", "direct_phone"):
                inner = val.get(k)
                if isinstance(inner, str) and inner.strip():
                    out.append(inner.strip())

    _add(person.get("mobile"))
    for key in ("mobile_phone", "direct_phone", "phone", "phone_number"):
        _add(person.get(key))
    return out


def digits_from_phone_preview(raw: str | None) -> str:
    """Dígitos visibles aunque el resto esté enmascarado (*)."""
    if not raw:
        return ""
    # Quitar máscaras y quedarnos con dígitos (ignora * X x).
    cleaned = re.sub(r"[*Xx×]", "", str(raw))
    digits = digits_only(cleaned)
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def preview_looks_like_mobile(raw: str | None) -> bool:
    """True si el preview (aunque incompleto) sugiere celular WA / AR 549…"""
    digits = digits_from_phone_preview(raw)
    if not digits:
        return False
    if digits.startswith("549"):
        return True
    # Local AR con 9 de móvil: 9 + área…
    if digits.startswith("9") and len(digits) >= 3:
        return True
    # Prefijo internacional móvil incompleto pero con 9 tras 54
    if digits.startswith("54") and len(digits) >= 3 and digits[2] == "9":
        return True
    return False


def preview_looks_like_landline(raw: str | None) -> bool:
    """
    True si el preview sugiere fijo (no pagar enrich_mobile).

    Ej.: +54 11 4376-**** → 54114376 (CABA sin 9 móvil).
    """
    if preview_looks_like_mobile(raw):
        return False
    digits = digits_from_phone_preview(raw)
    if len(digits) < 4:
        return False
    # Número completo: usar clasificador estricto
    if len(digits) >= 10 and not is_masked_phone(raw):
        return classify_phone_kind(raw) == "landline"
    # Preview parcial AR: 5411… / 54 + área sin 9
    if digits.startswith("5411"):
        return True
    if digits.startswith("54") and len(digits) >= 4 and digits[2] != "9":
        # 542…, 543… fijos interior (no 549 móvil)
        return True
    if is_argentina_landline_digits(digits):
        return True
    return False


def person_phone_preview_is_landline(person: dict[str, Any]) -> bool:
    """True si algún teléfono/preview de Prospeo clasifica como fijo (y ninguno como móvil)."""
    if not isinstance(person, dict):
        return False
    if person.get(_SKIP_MOBILE_ENRICH_KEY):
        return True
    candidates = _raw_phone_candidates(person)
    if not candidates:
        return False
    saw_landline = False
    for raw in candidates:
        if preview_looks_like_mobile(raw):
            return False
        if preview_looks_like_landline(raw):
            saw_landline = True
    # También: ya extrajimos landline y no hay móvil usable
    ch = merge_contact_channels(person)
    if ch.get("landline_phone") and not ch.get("whatsapp_number"):
        return True
    return saw_landline


def should_skip_enrich_mobile(person: dict[str, Any]) -> bool:
    """
    No pagar enrich_mobile si:
    - ya hay móvil WA usable,
    - el preview/número clasifica como fijo,
    - o ya intentamos enrich_mobile y no sirvió para WA.
    """
    if not isinstance(person, dict):
        return True
    if person.get(_SKIP_MOBILE_ENRICH_KEY) or person.get(_MOBILE_ENRICH_DONE_KEY):
        return True
    if person_has_usable_mobile(person):
        return True
    if person_phone_preview_is_landline(person):
        return True
    return False


def decide_enrich_mobile(person: dict[str, Any], *, want_mobile: bool) -> bool:
    """True solo si queremos móvil y no hay razón para skip."""
    if not want_mobile:
        return False
    return not should_skip_enrich_mobile(person)


def mark_mobile_enrich_done(person: dict[str, Any], *, got_whatsapp: bool) -> dict[str, Any]:
    """Marca el person para no volver a gastar enrich_mobile en este contacto."""
    out = dict(person) if isinstance(person, dict) else {}
    out[_MOBILE_ENRICH_DONE_KEY] = True
    if not got_whatsapp:
        out[_SKIP_MOBILE_ENRICH_KEY] = True
    return out


def apply_enrich_mobile_result(
    original: dict[str, Any],
    enriched: dict[str, Any] | None,
    *,
    requested_mobile: bool,
) -> dict[str, Any]:
    """Merge post-enrich: WA solo si es móvil válido; si pedimos móvil y falló → no reintentar."""
    base = dict(original) if isinstance(original, dict) else {}
    if isinstance(enriched, dict) and enriched:
        base.update({k: v for k, v in enriched.items() if v not in (None, "", [], {})})
    # Forzar que whatsapp del merge no arrastre fijo: merge_contact_channels ya lo separa.
    got_wa = person_has_usable_mobile(base)
    if requested_mobile:
        base = mark_mobile_enrich_done(base, got_whatsapp=got_wa)
    elif person_phone_preview_is_landline(base) and not got_wa:
        base[_SKIP_MOBILE_ENRICH_KEY] = True
    return base


def contact_details_filter(*, require_mobile: bool = False, require_email: bool = True) -> dict[str, Any]:
    details: dict[str, Any] = {
        "operator": "AND" if require_mobile and require_email else "OR",
        "hide_people_with_details_already_revealed": False,
    }
    if require_mobile:
        details["mobile"] = ["VERIFIED"]
    if require_email:
        details["email"] = ["VERIFIED"]
    return {"person_contact_details": details}


def prospeo_phone_capabilities_note() -> dict[str, Any]:
    return {
        "search_person_has_mobile_field": True,
        "search_person_reveals_number_without_enrich": "sometimes",
        "enrich_person_endpoint": "POST https://api.prospeo.io/enrich-person",
        "enrich_person_params": {
            "enrich_mobile": True,
            "only_verified_email": False,
            "data": "linkedin_url | person_id | name + company",
        },
        "batch_mode_note": (
            "El pipeline por lotes usa solo search-person (sin enrich-person por contacto) "
            "por timeout; los teléfonos pueden quedar vacíos aunque el plan los permita."
        ),
        "if_empty_phone": (
            "Opciones: (1) enrich-person por person_id tras search, "
            "(2) subir de plan Prospeo, (3) proveedor alternativo (Apollo.io, Lusha) vía integración aparte."
        ),
        "whatsapp": (
            "Celular validado → WhatsApp. Fijo (direct_phone / preview 5411…) → landline_phone; "
            "no se usa para WA ni se paga enrich_mobile. Tras enrich sin móvil WA no se reintenta."
        ),
    }
