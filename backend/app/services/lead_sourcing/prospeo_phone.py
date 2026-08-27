"""Teléfonos Prospeo — extracción móvil vs fijo + capacidades del plan."""

from __future__ import annotations

from typing import Any

from app.services.lead_sourcing.providers.prospeo_mvp import extract_email_phone
from app.services.whatsapp_cloud_service import is_masked_phone, sanitize_stored_phone
from app.services.whatsapp_phone_validation import (
    classify_phone_kind,
    sanitize_landline_phone,
    sanitize_whatsapp_mobile,
)


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
            "Celular validado → WhatsApp. Fijo (direct_phone) → landline_phone en Nexus, "
            "visible aparte; no se usa para WA."
        ),
    }
