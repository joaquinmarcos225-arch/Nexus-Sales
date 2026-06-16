"""Teléfonos Prospeo — extracción y capacidades del plan."""

from __future__ import annotations

from typing import Any

from app.services.lead_sourcing.providers.prospeo_mvp import extract_email_phone


def extract_prospeo_phones(person: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """
    Devuelve (phone, whatsapp_number, phone_source).
    whatsapp_number: mismo móvil si Prospeo no distingue canal WhatsApp.
    """
    if not isinstance(person, dict):
        return None, None, None

    phone: str | None = None
    source: str | None = None

    mobile_obj = person.get("mobile")
    if isinstance(mobile_obj, dict):
        mob = mobile_obj.get("mobile") or mobile_obj.get("mobile_international")
        if mob and str(mob).strip():
            phone = str(mob).strip()
            if mobile_obj.get("revealed") is True:
                source = "prospeo_enrich_mobile"
            else:
                source = "prospeo_search_mobile"

    if not phone:
        for key in ("phone", "phone_number", "mobile_phone", "direct_phone"):
            val = person.get(key)
            if isinstance(val, str) and val.strip():
                phone = val.strip()
                source = source or "prospeo_search_phone"
                break
            if isinstance(val, dict):
                inner = val.get("number") or val.get("phone") or val.get("mobile")
                if inner and str(inner).strip():
                    phone = str(inner).strip()
                    source = source or "prospeo_search_phone"
                    break

    wa = person.get("whatsapp") or person.get("whatsapp_number")
    whatsapp: str | None = None
    if isinstance(wa, str) and wa.strip():
        whatsapp = wa.strip()
        source = source or "prospeo_whatsapp"
    elif phone:
        whatsapp = phone

    return phone, whatsapp, source


def merge_contact_channels(person: dict[str, Any]) -> dict[str, Any]:
    """Email + teléfonos + linkedin normalizado desde payload Prospeo."""
    from app.services.lead_sourcing.linkedin_identity import normalize_linkedin_url

    email, legacy_phone = extract_email_phone(person)
    phone, whatsapp, phone_source = extract_prospeo_phones(person)
    if not phone and legacy_phone:
        phone = legacy_phone
        phone_source = phone_source or "prospeo_search_mobile"

    linkedin = (
        person.get("linkedin_url")
        or person.get("linkedin")
        or person.get("person_linkedin_url")
    )
    return {
        "email": email,
        "phone": phone,
        "whatsapp_number": whatsapp,
        "phone_source": phone_source,
        "linkedin_url": normalize_linkedin_url(str(linkedin) if linkedin else None),
    }


def prospeo_phone_capabilities_note() -> dict[str, Any]:
    """
    Investigación plan actual (search-person + enrich-person).

    search-person: a veces trae objeto mobile sin número hasta enrich.
    enrich-person: POST https://api.prospeo.io/enrich-person con enrich_mobile=true revela móvil
    (consume créditos; disponibilidad según plan STARTER/PRO).
    """
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
        "whatsapp": "Prospeo no expone whatsapp_number separado; usamos mobile como WhatsApp opcional.",
    }
