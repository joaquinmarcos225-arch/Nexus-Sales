"""Mapeo Apollo → candidatos Nexus."""

from __future__ import annotations

from typing import Any

from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.schemas.prospect import ProspectCreate
from app.services.linkedin_assisted_service import is_real_linkedin_profile_url


def _pick_phone(person: dict) -> tuple[str | None, str | None]:
    """(phone, whatsapp) — mobile va a whatsapp si aplica."""
    numbers = person.get("phone_numbers") or []
    if not isinstance(numbers, list):
        return None, None
    mobile = None
    direct = None
    for entry in numbers:
        if not isinstance(entry, dict):
            continue
        raw = (entry.get("raw_number") or entry.get("sanitized_number") or "").strip()
        if not raw:
            continue
        kind = (entry.get("type") or "").lower()
        if kind in {"mobile", "cell", "whatsapp"} and not mobile:
            mobile = raw
        elif not direct:
            direct = raw
    phone = direct or mobile
    whatsapp = mobile if mobile and mobile != phone else mobile
    return phone, whatsapp


def _org_field(person: dict, field: str) -> str | None:
    org = person.get("organization") or person.get("account") or {}
    if not isinstance(org, dict):
        return None
    val = org.get(field)
    return str(val).strip() if val else None


def person_from_search_hit(hit: dict) -> LeadCandidateRead:
    first = (hit.get("first_name") or "").strip()
    last_obf = (hit.get("last_name_obfuscated") or hit.get("last_name") or "").strip()
    name = f"{first} {last_obf}".strip() or "Sin nombre"
    org_name = _org_field(hit, "name") or "Empresa desconocida"
    return LeadCandidateRead(
        external_id=str(hit.get("id") or ""),
        provider="apollo",
        first_name=first or None,
        last_name=last_obf or None,
        name=name,
        company_name=org_name,
        role=(hit.get("title") or "").strip() or None,
        industry=_org_field(hit, "industry"),
        country=None,
        email=None,
        linkedin_url=None,
        phone=None,
        whatsapp=None,
        company_website=None,
        has_email=bool(hit.get("has_email")),
        has_phone=str(hit.get("has_direct_phone") or "").lower() in {"yes", "true"},
        has_linkedin=False,
    )


def merge_enriched(base: LeadCandidateRead, person: dict) -> LeadCandidateRead:
    first = (person.get("first_name") or base.first_name or "").strip()
    last = (person.get("last_name") or base.last_name or "").strip()
    name = (person.get("name") or f"{first} {last}".strip() or base.name).strip()
    org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
    phone, whatsapp = _pick_phone(person)
    li = (person.get("linkedin_url") or "").strip() or None
    email = (person.get("email") or "").strip() or None
    if email and email.endswith("@email_not_unlocked.com"):
        email = None
    website = _org_field(person, "website_url") or _org_field(person, "primary_domain")
    if website and not website.startswith("http"):
        website = f"https://{website}"
    country = (
        (person.get("country") or "")
        or (org.get("country") if isinstance(org, dict) else "")
        or base.country
        or ""
    ).strip() or None
    return base.model_copy(
        update={
            "first_name": first or base.first_name,
            "last_name": last or base.last_name,
            "name": name,
            "company_name": _org_field(person, "name") or base.company_name,
            "role": (person.get("title") or base.role or "").strip() or None,
            "industry": _org_field(person, "industry") or base.industry,
            "country": country,
            "email": email,
            "linkedin_url": li,
            "phone": phone,
            "whatsapp": whatsapp,
            "company_website": website,
            "has_email": bool(email),
            "has_phone": bool(phone or whatsapp),
            "has_linkedin": bool(li and is_real_linkedin_profile_url(li)),
        }
    )


def company_from_hit(hit: dict) -> CompanyCandidateRead:
    org = hit.get("organization") if isinstance(hit.get("organization"), dict) else hit
    name = (org.get("name") or hit.get("name") or "Sin nombre").strip()
    website = (org.get("website_url") or org.get("primary_domain") or "").strip() or None
    if website and not website.startswith("http"):
        website = f"https://{website}"
    return CompanyCandidateRead(
        external_id=str(org.get("id") or hit.get("id") or ""),
        provider="apollo",
        name=name,
        website_url=website,
        industry=(org.get("industry") or hit.get("industry") or "").strip() or None,
        country=(org.get("country") or "").strip() or None,
        employee_count=org.get("estimated_num_employees") or org.get("employee_count"),
        city=(org.get("city") or "").strip() or None,
    )


def to_prospect_create(candidate: LeadCandidateRead) -> ProspectCreate:
    notes_parts = [f"Importado vía Lead Sourcing ({candidate.provider or 'pipeline'})."]
    if candidate.company_website:
        notes_parts.append(f"Web: {candidate.company_website}")
    return ProspectCreate(
        name=candidate.name,
        company_name=candidate.company_name,
        role=candidate.role,
        industry=candidate.industry,
        country=candidate.country,
        linkedin_url=candidate.linkedin_url,
        email=candidate.email,
        phone=candidate.phone,
        whatsapp=candidate.whatsapp,
        company_website=candidate.company_website,
        source_provider=candidate.provider,
        source_external_id=candidate.external_id,
        notes=" ".join(notes_parts),
    )
