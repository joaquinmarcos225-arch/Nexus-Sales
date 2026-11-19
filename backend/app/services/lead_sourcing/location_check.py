"""Chequeo de país de un contacto contra las ubicaciones que se mandaron a Prospeo.

Se usa igual en el buscador (Explorar) y en las campañas.
Contacto sin país = pasa (el filtro de locación de Prospeo ya restringió).
Ubicación sin país reconocible (ej. «North America», «Miami, Florida») = pasa.
Región LATAM: se expande a la lista de países; no se aprueba Brasil solo porque el label diga «sin Brasil».
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

_KNOWN_COUNTRIES = (
    "Argentina", "Bolivia", "Brazil", "Chile", "Colombia", "Costa Rica", "Cuba", "Dominican Republic",
    "Ecuador", "El Salvador", "Guatemala", "Honduras", "Mexico", "Nicaragua", "Panama", "Paraguay",
    "Peru", "Puerto Rico", "Uruguay", "Venezuela", "United States", "Canada", "Spain", "Portugal",
    "United Kingdom", "Ireland", "France", "Germany", "Italy", "Netherlands", "Belgium", "Switzerland",
    "Austria", "Sweden", "Norway", "Denmark", "Finland", "Poland", "Czechia", "Czech Republic",
    "Greece", "Romania", "Hungary", "Ukraine", "Russia", "Turkey", "Israel", "India", "China", "Japan",
    "South Korea", "Singapore", "Australia", "New Zealand", "Philippines", "Indonesia", "Malaysia",
    "Thailand", "Vietnam", "Pakistan", "Bangladesh", "South Africa", "Nigeria", "Kenya", "Egypt",
    "Morocco", "United Arab Emirates", "Saudi Arabia",
)


def _norm(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).lower().strip()


_COUNTRY_PATTERNS = [(c, re.compile(rf"(?<![a-z]){re.escape(_norm(c))}(?![a-z])")) for c in _KNOWN_COUNTRIES]


def countries_in(text: str | None) -> set[str]:
    blob = _norm(text)
    if not blob:
        return set()
    found = {c for c, pat in _COUNTRY_PATTERNS if pat.search(blob)}
    # «New Mexico» es un estado de EE.UU., no México.
    if "Mexico" in found and re.search(r"new mexico", blob) and not re.search(r"(?<!new )mexico", blob):
        found.discard("Mexico")
    return found


def _looks_like_latam_region(loc: str) -> bool:
    low = (loc or "").lower()
    return any(
        x in low
        for x in (
            "latam",
            "latin america",
            "latinoam",
            "america latina",
            "américa latina",
        )
    )


def _person_country_set(person_country: str | None) -> set[str]:
    raw = (person_country or "").strip()
    if not raw:
        return set()
    found = countries_in(raw)
    n = _norm(raw)
    if n in {"brasil", "br"} or re.search(r"(?<![a-z])brasil(?![a-z])", n):
        found.add("Brazil")
    return found


def countries_from_location_label(loc: str | None) -> set[str]:
    """Países que cubre un include de locación (país concreto o región LATAM)."""
    raw = (loc or "").strip()
    if not raw:
        return set()
    if _looks_like_latam_region(raw):
        from app.services.lead_sourcing.b2c_person_search import _location_queries

        expanded: set[str] = set()
        for name in _location_queries(raw):
            expanded |= countries_in(name)
        if expanded:
            return expanded
    return countries_in(raw)


def country_allowed(person_country: str | None, locations: list[str] | None) -> bool:
    if not locations or not (person_country or "").strip():
        return True
    allowed: set[str] = set()
    for loc in locations:
        allowed |= countries_from_location_label(loc)
    if not allowed:
        return True
    return bool(_person_country_set(person_country) & allowed)


def resolve_company_hq_locations(hq: str | None, *, max_locations: int = 6) -> list[str]:
    """Valores canónicos para ``company_location_search`` (HQ), o []."""
    from app.services.campaign_icp import is_icp_token_empty

    raw = (hq or "").strip()
    if not raw or is_icp_token_empty(raw):
        return []
    from app.services.lead_sourcing.b2c_person_search import (
        _location_queries,
        resolve_canonical_locations,
    )

    locations = resolve_canonical_locations(raw, max_locations=max_locations)
    if not locations:
        locations = _location_queries(raw) or [raw]
    return list(locations[:max_locations])


def company_hq_search_filter(hq: str | None, *, max_locations: int = 6) -> dict[str, Any]:
    """Filtro Prospeo por sede/HQ. Vacío si no hay ICP de HQ."""
    locations = resolve_company_hq_locations(hq, max_locations=max_locations)
    if not locations:
        return {}
    return {"company_location_search": {"include": locations}}


def company_hq_matches(*, company_country: str | None, campaign_hq: str | None) -> bool:
    """False solo si el país de empresa es conocido y contradice el HQ pedido.

    Sin país de empresa → True (incomplete; no inventar HQ con el país de la persona).
    """
    locations = resolve_company_hq_locations(campaign_hq)
    if not locations:
        return True
    raw = (company_country or "").strip()
    if not raw:
        return True
    return country_allowed(raw, locations)


def person_country(person: dict[str, Any]) -> str | None:
    """País de la persona (no de la sede de su empresa)."""
    loc = person.get("location") if isinstance(person.get("location"), dict) else {}
    value = loc.get("country") or person.get("country")
    text = str(value).strip() if value else ""
    return text or None


def person_city(person: dict[str, Any]) -> str | None:
    """Ciudad de la persona, si el proveedor la mandó. Vacío si no hay dato."""
    loc = person.get("location") if isinstance(person.get("location"), dict) else {}
    value = loc.get("city") or person.get("city")
    text = str(value).strip() if value else ""
    return text or None


GeoStatus = Literal["confirmed", "contradictory", "incomplete", "review"]


@dataclass(frozen=True)
class GeoAssessment:
    """Clasificación geográfica local. No confirma con sede de empresa ni con prefijo solo."""

    status: GeoStatus | None
    person_country: str | None
    location_country: str | None
    company_country: str | None
    phone_country: str | None
    resolved_country: str | None
    reason: str
    evidence: str


# Prefijos únicos (no +1). Solo señal complementaria, nunca confirmación.
_PHONE_PREFIX_COUNTRIES: tuple[tuple[str, str], ...] = (
    ("598", "Uruguay"),
    ("593", "Ecuador"),
    ("595", "Paraguay"),
    ("591", "Bolivia"),
    ("506", "Costa Rica"),
    ("507", "Panama"),
    ("502", "Guatemala"),
    ("504", "Honduras"),
    ("503", "El Salvador"),
    ("505", "Nicaragua"),
    ("351", "Portugal"),
    ("54", "Argentina"),
    ("55", "Brazil"),
    ("56", "Chile"),
    ("57", "Colombia"),
    ("51", "Peru"),
    ("52", "Mexico"),
    ("53", "Cuba"),
    ("58", "Venezuela"),
    ("34", "Spain"),
    ("33", "France"),
    ("49", "Germany"),
    ("44", "United Kingdom"),
    ("39", "Italy"),
    ("81", "Japan"),
    ("86", "China"),
    ("91", "India"),
    ("61", "Australia"),
)


def _is_region_label(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    if _looks_like_latam_region(raw):
        return True
    n = _norm(raw)
    return n in {
        "na",
        "emea",
        "apac",
        "europe",
        "europa",
        "north america",
        "norteamerica",
        "global",
        "worldwide",
        "any",
    }


def person_location_text(person: dict[str, Any]) -> str | None:
    """Texto de ubicación de la persona (ciudad/región), sin sede de empresa."""
    parts: list[str] = []
    loc = person.get("location")
    if isinstance(loc, dict):
        for key in ("city", "name", "raw", "text", "state", "region", "full"):
            val = loc.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    elif isinstance(loc, str) and loc.strip():
        parts.append(loc.strip())
    city = person_city(person)
    if city:
        parts.append(city)
    for key in ("location_name", "geo", "person_location"):
        val = person.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    blob = " ".join(parts).strip()
    return blob or None


def company_country_from_org(org: dict[str, Any] | None) -> str | None:
    if not isinstance(org, dict):
        return None
    for key in ("country", "hq_country", "company_country", "location_country"):
        val = org.get(key)
        if isinstance(val, str) and val.strip() and not _is_region_label(val):
            return val.strip()
    loc = org.get("location")
    if isinstance(loc, dict):
        val = loc.get("country")
        if isinstance(val, str) and val.strip() and not _is_region_label(val):
            return val.strip()
    return None


def infer_country_from_phone(phone: str | None) -> str | None:
    """País probable por prefijo. No usar como prueba definitiva (p.ej. no +1)."""
    digits = re.sub(r"\D+", "", str(phone or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) < 8:
        return None
    for prefix, country in _PHONE_PREFIX_COUNTRIES:
        if digits.startswith(prefix):
            return country
    return None


def _country_from_location_text(text: str | None) -> str | None:
    if not (text or "").strip():
        return None
    from app.services.lead_sourcing.icp_region import infer_country_from_text, region_countries

    found = region_countries(text)
    if len(found) == 1:
        return next(iter(found))
    inferred = infer_country_from_text(text)
    if inferred and (not found or inferred in found):
        return inferred
    return None


def country_in_campaign_region(
    country: str | None,
    campaign_region: str | None,
) -> bool | None:
    """True/False si hay país real y región ICP; None si falta alguno."""
    from app.services.campaign_icp import is_icp_token_empty
    from app.services.lead_sourcing.icp_region import score_region_alignment

    if campaign_region is None or is_icp_token_empty(campaign_region):
        return None
    raw = (country or "").strip()
    if not raw or _is_region_label(raw):
        return None
    score, _ = score_region_alignment(campaign_region, raw)
    if score >= 50:
        return True
    return False


def assess_person_geo(
    *,
    campaign_region: str | None,
    person_country: str | None = None,
    person_location: str | None = None,
    company_country: str | None = None,
    phone: str | None = None,
) -> GeoAssessment:
    """Jerarquía: país de persona → ubicación de persona → empresa (secundaria) → prefijo (complementario)."""
    from app.services.campaign_icp import is_icp_token_empty

    explicit = (person_country or "").strip() or None
    if explicit and _is_region_label(explicit):
        explicit = None
    location_country = _country_from_location_text(person_location)
    location_vs_person = False
    if location_country and explicit:
        loc_set = _person_country_set(location_country)
        exp_set = _person_country_set(explicit)
        if loc_set and exp_set and not (loc_set & exp_set):
            location_vs_person = True
        elif loc_set & exp_set:
            location_country = next(iter(loc_set & exp_set), location_country)
    company = (company_country or "").strip() or None
    if company and _is_region_label(company):
        company = None
    phone_country = infer_country_from_phone(phone)

    person_side = explicit or location_country
    evidence = "person_country" if explicit else (
        "person_location" if location_country else (
            "none" if not company and not phone_country else "secondary"
        )
    )
    resolved = person_side

    if campaign_region is None or is_icp_token_empty(campaign_region):
        return GeoAssessment(
            status=None,
            person_country=explicit,
            location_country=location_country,
            company_country=company,
            phone_country=phone_country,
            resolved_country=resolved,
            reason="ICP región no configurada",
            evidence=evidence,
        )

    person_match = country_in_campaign_region(person_side, campaign_region)
    company_match = country_in_campaign_region(company, campaign_region)
    phone_match = country_in_campaign_region(phone_country, campaign_region)

    person_vs_company = False
    if person_side and company:
        a, b = _person_country_set(person_side), _person_country_set(company)
        if a and b and not (a & b):
            person_vs_company = True

    person_vs_phone = False
    if person_side and phone_country:
        a, b = _person_country_set(person_side), _person_country_set(phone_country)
        if a and b and not (a & b):
            person_vs_phone = True

    if person_match is False:
        return GeoAssessment(
            status="contradictory",
            person_country=explicit,
            location_country=location_country,
            company_country=company,
            phone_country=phone_country,
            resolved_country=resolved,
            reason="el país de la persona no cumple la región solicitada",
            evidence=evidence,
        )

    if person_match is True and (person_vs_company or person_vs_phone or location_vs_person):
        return GeoAssessment(
            status="review",
            person_country=explicit,
            location_country=location_country,
            company_country=company,
            phone_country=phone_country,
            resolved_country=resolved,
            reason="señales geográficas contradictorias entre persona y empresa/teléfono",
            evidence=evidence,
        )

    if person_match is True:
        src = "país explícito de la persona" if explicit else "ubicación de la persona"
        return GeoAssessment(
            status="confirmed",
            person_country=explicit,
            location_country=location_country,
            company_country=company,
            phone_country=phone_country,
            resolved_country=resolved,
            reason=f"{src} coincide con la región solicitada",
            evidence=evidence,
        )

    # Sin evidencia de persona: empresa y teléfono no confirman.
    if company_match is False or phone_match is False:
        return GeoAssessment(
            status="review",
            person_country=explicit,
            location_country=location_country,
            company_country=company,
            phone_country=phone_country,
            resolved_country=None,
            reason="sin país de persona y hay una señal secundaria fuera de la región",
            evidence="secondary",
        )

    reason = "faltan datos de país/ubicación de la persona"
    if company_match is True:
        reason = "el país de la empresa no confirma por sí solo el de la persona"
    elif phone_match is True:
        reason = "el prefijo telefónico no confirma el país de la persona"
    return GeoAssessment(
        status="incomplete",
        person_country=explicit,
        location_country=location_country,
        company_country=company,
        phone_country=phone_country,
        resolved_country=None,
        reason=reason,
        evidence=evidence,
    )


def cache_country_inputs(row: Any) -> tuple[str | None, str | None]:
    """No asume que ``row.country`` histórico sea de la persona.

    Returns:
      (person_country, company_country)
    """
    stored = (getattr(row, "country", None) or "").strip() or None
    origin = (getattr(row, "country_origin", None) or "").strip().lower()
    if origin == "person":
        return stored, None
    if origin == "company":
        return None, stored
    return None, stored


def assess_person_geo_from_hit(
    person: dict[str, Any],
    *,
    campaign_region: str | None,
    org: dict[str, Any] | None = None,
    phone: str | None = None,
) -> GeoAssessment:
    loc = person.get("location") if isinstance(person.get("location"), dict) else {}
    loc_blob = person_location_text(person)
    if isinstance(person.get("location"), str):
        loc_blob = person.get("location")
    elif loc.get("country") and loc_blob:
        loc_blob = " ".join(
            p for p in (
                str(loc.get("city") or "").strip(),
                str(loc.get("state") or "").strip(),
                str(loc.get("name") or "").strip(),
            ) if p
        ) or loc_blob
    return assess_person_geo(
        campaign_region=campaign_region,
        person_country=person_country(person),
        person_location=loc_blob,
        company_country=company_country_from_org(org),
        phone=phone,
    )
