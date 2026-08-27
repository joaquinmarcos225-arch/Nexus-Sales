"""Búsqueda person-first (B2C) via Prospeo search-person.

Filtros correctos según docs Prospeo:
- person_location_search (valores canónicos vía Search Suggestions)
- person_job_title con match_mode CONTAINS
- person_name_or_job_title para keywords / intereses
- person_contact_details para priorizar email verificado
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url, linkedin_slug_key
from app.services.lead_sourcing.prospeo_suggestions import suggest_job_titles, suggest_locations
from app.services.lead_sourcing.providers.prospeo_mvp import (
    _person_display,
    _search_person_raw,
    enrich_person_by_id,
    extract_email_phone,
)
from app.services.lead_sourcing.prospeo_contact_validation import is_forbidden_email
from app.services.lead_sourcing.prospeo_phone import (
    apply_enrich_mobile_result,
    contact_details_filter,
    decide_enrich_mobile,
    merge_contact_channels,
    person_has_usable_mobile,
    person_mobile_verified,
    person_phone_preview_is_landline,
)

_logger = logging.getLogger(__name__)

# Intereses / señales B2C → títulos de búsqueda (Prospeo es B2B-oriented).
# target_area (situación) NO entra acá: solo copy.
_INTEREST_TITLE_HINTS: dict[str, list[str]] = {
    "running": ["Running Coach", "Athletics Coach", "Personal Trainer", "Fitness Coach"],
    "run": ["Running Coach", "Personal Trainer"],
    "fitness": ["Personal Trainer", "Fitness Coach", "Fitness Instructor", "Gym Manager"],
    "gym": ["Personal Trainer", "Gym Owner", "Fitness Coach"],
    "wellness": ["Wellness Coach", "Health Coach", "Wellness Manager"],
    "mindfulness": ["Meditation Teacher", "Wellness Coach", "Yoga Instructor"],
    "yoga": ["Yoga Instructor", "Yoga Teacher", "Wellness Coach"],
    "nutrición": ["Nutritionist", "Dietitian", "Health Coach"],
    "nutricion": ["Nutritionist", "Dietitian"],
    "nutrition": ["Nutritionist", "Dietitian", "Health Coach"],
    "viajes": ["Travel Advisor", "Travel Agent", "Tour Guide"],
    "turismo": ["Travel Advisor", "Tour Guide", "Travel Consultant"],
    "travel": ["Travel Advisor", "Travel Agent", "Tour Guide"],
    "gaming": ["Game Designer", "Esports", "Community Manager"],
    "mascotas": ["Veterinarian", "Pet Groomer", "Pet Sitter"],
    "pets": ["Veterinarian", "Pet Groomer"],
    "padres": ["Parent Educator", "Family Counselor", "Teacher"],
    "familia": ["Family Counselor", "Teacher"],
    "freelancer": ["Freelancer", "Independent Consultant", "Solopreneur"],
    "freelance": ["Freelancer", "Independent Consultant"],
    "emprendedor": ["Founder", "Entrepreneur", "Solopreneur"],
    "emprendedores": ["Founder", "Entrepreneur"],
    "estudiante": ["Student", "University"],
    "inversores": ["Investor", "Angel Investor", "Private Investor"],
    "inversor": ["Investor", "Angel Investor", "Private Investor"],
    "inmobiliario": ["Real Estate Investor", "Property Investor", "Real Estate"],
    "inmobiliaria": ["Real Estate", "Property Manager"],
    "propiedades": ["Real Estate Investor", "Property Investor", "Real Estate Agent"],
    "real estate": ["Real Estate Investor", "Real Estate Agent", "Property Manager"],
    "comprador": ["Home Buyer", "Real Estate", "Property"],
    "inquilino": ["Tenant", "Renter", "Real Estate"],
    "alquiler": ["Tenant", "Renter", "Property Manager"],
    "coach": ["Coach", "Personal Coach", "Business Coach"],
    "consultor": ["Consultant", "Independent Consultant"],
    "retail": ["Retail Buyer", "Store Manager", "Retail Associate"],
}

_REGION_QUERIES: dict[str, list[str]] = {
    "latam - brasil": ["Argentina", "Mexico", "Colombia", "Chile", "Peru", "Uruguay"],
    "latam + brasil": ["Argentina", "Brazil", "Mexico", "Colombia", "Chile", "Peru"],
    "latam": ["Argentina", "Mexico", "Colombia", "Chile", "Peru", "Uruguay"],
    "na": ["United States", "Canada"],
    "emea": ["Spain", "United Kingdom", "Germany", "France"],
    "apac": ["Australia", "Singapore", "India"],
    "argentina": ["Argentina"],
    "brasil": ["Brazil"],
    "brazil": ["Brazil"],
    "méxico": ["Mexico"],
    "mexico": ["Mexico"],
    "españa": ["Spain"],
    "spain": ["Spain"],
    "chile": ["Chile"],
    "colombia": ["Colombia"],
    "peru": ["Peru"],
    "perú": ["Peru"],
    "uruguay": ["Uruguay"],
}


def _split_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,;/|]+|\n+", raw)
    out: list[str] = []
    for p in parts:
        t = p.strip()
        if len(t) < 2:
            continue
        if t.lower() in {"no importante", "no importa", "cualquiera", "n/a", "-", "--"}:
            continue
        out.append(t[:80])
    return out[:10]


def _location_queries(country: str | None) -> list[str]:
    raw = (country or "").strip()
    if not raw:
        return []
    lower = raw.lower()
    for key, vals in _REGION_QUERIES.items():
        if key in lower or lower == key:
            return list(vals)
    # Si el usuario escribió una ciudad/país libre, usarlo como query de suggestions
    return [raw]


def resolve_canonical_locations(country: str | None, *, max_locations: int = 6) -> list[str]:
    queries = _location_queries(country)
    resolved: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for name in suggest_locations(q, limit=4):
            lk = name.lower()
            if lk in seen:
                continue
            seen.add(lk)
            resolved.append(name)
            if len(resolved) >= max_locations:
                return resolved
        # Sin suggestions (sin API / fallo): usar query cruda como último recurso
        if not resolved and len(q) >= 2:
            lk = q.lower()
            if lk not in seen:
                seen.add(lk)
                resolved.append(q)
    return resolved[:max_locations]


def _interest_title_seeds(interests: list[str]) -> list[str]:
    seeds: list[str] = []
    seen: set[str] = set()
    for interest in interests:
        low = interest.lower()
        # Match por token
        matched = False
        for key, titles in _INTEREST_TITLE_HINTS.items():
            if key in low or low in key:
                matched = True
                for t in titles:
                    lk = t.lower()
                    if lk not in seen:
                        seen.add(lk)
                        seeds.append(t)
        # También el interés crudo (CONTAINS puede matchear "Running" en títulos)
        if len(interest) >= 3 and interest.lower() not in seen:
            seen.add(interest.lower())
            seeds.append(interest)
        if not matched and " " in interest:
            # Primera palabra significativa
            first = interest.split()[0]
            if len(first) >= 3 and first.lower() not in seen:
                seen.add(first.lower())
                seeds.append(first)
    return seeds[:12]


def resolve_search_titles(*, profile_terms: list[str], interests: list[str]) -> list[str]:
    """Títulos canónicos (suggestions) + seeds de intereses."""
    seeds = list(profile_terms) + _interest_title_seeds(interests)
    out: list[str] = []
    seen: set[str] = set()
    for seed in seeds:
        for title in suggest_job_titles(seed, limit=4):
            lk = title.lower()
            if lk in seen:
                continue
            seen.add(lk)
            out.append(title)
            if len(out) >= 12:
                return out
    return out


def build_b2c_filter_variants(
    campaign: Campaign,
    *,
    require_mobile: bool = False,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    """
    Cascada de filtros: estricto → amplio.
    Cada item: (label, filters).
    """
    locations = resolve_canonical_locations(campaign.target_country)
    interests = _split_keywords(getattr(campaign, "target_interests", None))
    # Quién buscamos → títulos/keywords. Situación (target_area) es solo copy.
    profile = _split_keywords(campaign.target_role)
    vague = {
        "consumidor final",
        "usuario frecuente",
        "cliente",
        "persona",
        "personas",
    }
    profile = [p for p in profile if p.lower() not in vague]
    titles = resolve_search_titles(profile_terms=profile, interests=interests)

    meta = {
        "locations_resolved": locations,
        "titles_resolved": titles[:12],
        "interests": interests,
        "profile_terms": profile,
        "situation_copy_only": (getattr(campaign, "target_area", None) or "")[:80],
        "language_copy_only": (getattr(campaign, "target_language", None) or "")[:40],
        "require_mobile": require_mobile,
    }

    variants: list[tuple[str, dict[str, Any]]] = []

    def _base_loc() -> dict[str, Any]:
        if locations:
            return {"person_location_search": {"include": locations[:5]}}
        return {}

    contact = contact_details_filter(require_mobile=False, require_email=True)
    contact_mobile = contact_details_filter(require_mobile=True, require_email=False)

    if titles and locations:
        if require_mobile:
            variants.append(
                (
                    "loc+titles+mobile",
                    {
                        **_base_loc(),
                        "person_job_title": {
                            "include": titles[:8],
                            "match_mode": "CONTAINS",
                        },
                        **contact_mobile,
                    },
                )
            )
        variants.append(
            (
                "loc+titles+email",
                {
                    **_base_loc(),
                    "person_job_title": {
                        "include": titles[:8],
                        "match_mode": "CONTAINS",
                    },
                    **contact,
                },
            )
        )
        variants.append(
            (
                "loc+titles",
                {
                    **_base_loc(),
                    "person_job_title": {
                        "include": titles[:8],
                        "match_mode": "CONTAINS",
                    },
                },
            )
        )

    # Keywords / intereses como quick search
    for kw in (interests + profile)[:4]:
        if len(kw) < 2:
            continue
        f = {**_base_loc(), "person_name_or_job_title": kw[:80]}
        if require_mobile:
            f = {**f, **contact_mobile}
        variants.append((f"quick:{kw[:24]}", f))

    # Solo ubicación (luego filtramos localmente por intereses)
    if locations:
        if require_mobile:
            variants.append(("loc_only+mobile", {**_base_loc(), **contact_mobile}))
        variants.append(("loc_only+email", {**_base_loc(), **contact}))
        variants.append(("loc_only", _base_loc()))

    # Sin ubicación: títulos / quick search globales (último recurso)
    if not locations and titles:
        base_titles = {
            "person_job_title": {
                "include": titles[:6],
                "match_mode": "CONTAINS",
            },
        }
        if require_mobile:
            variants.append(("titles_global+mobile", {**base_titles, **contact_mobile}))
        variants.append(("titles_global", {**base_titles, **contact}))
    for kw in interests[:2]:
        if not locations:
            f = {"person_name_or_job_title": kw[:80]}
            if require_mobile:
                f = {**f, **contact_mobile}
            variants.append((f"quick_global:{kw[:24]}", f))

    # Dedup por label
    seen_labels: set[str] = set()
    unique: list[tuple[str, dict[str, Any]]] = []
    for label, filt in variants:
        if not filt:
            continue
        if label in seen_labels:
            continue
        seen_labels.add(label)
        unique.append((label, filt))
    return unique[:12], meta


def _person_text_blob(person: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "current_job_title",
        "job_title",
        "title",
        "headline",
        "summary",
        "about",
    ):
        val = person.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    org = person.get("company") if isinstance(person.get("company"), dict) else {}
    if isinstance(org, dict):
        for key in ("name", "industry"):
            val = org.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val)
    return " ".join(parts).lower()


def interest_match_score(person: dict[str, Any], interests: list[str]) -> int:
    if not interests:
        return 20  # sin intereses → no penalizar
    blob = _person_text_blob(person)
    if not blob:
        return 5
    hits = 0
    for interest in interests:
        tokens = [t for t in re.findall(r"\w{3,}", interest.lower()) if t]
        if not tokens:
            continue
        if all(t in blob for t in tokens) or interest.lower() in blob:
            hits += 1
        elif any(t in blob for t in tokens):
            hits += 0.5
    if hits <= 0:
        return 0
    return min(40, int(hits * 18))


def score_b2c_person(
    person: dict[str, Any],
    *,
    interests: list[str],
    locations: list[str],
    country_hint: str | None,
) -> tuple[int, str]:
    email, phone = extract_email_phone(person)
    linkedin = (
        person.get("linkedin_url")
        or person.get("person_linkedin_url")
        or person.get("linkedin")
        or ""
    )
    score = 25
    bits: list[str] = []

    # Contactabilidad
    if email and not is_forbidden_email(email):
        score += 20
        bits.append("email")
    if phone:
        score += 8
        bits.append("phone")
    if is_personal_linkedin_url(str(linkedin).strip() or None):
        score += 12
        bits.append("linkedin")

    # Intereses
    im = interest_match_score(person, interests)
    score += im
    if im >= 18:
        bits.append("interés")

    # Ubicación (texto libre en persona)
    loc_blob = " ".join(
        str(person.get(k) or "")
        for k in ("location", "country", "city", "person_location", "region")
    ).lower()
    loc_ok = False
    for loc in locations or _location_queries(country_hint):
        token = loc.split(",")[0].strip().lower()
        if token and token in loc_blob:
            loc_ok = True
            break
    if loc_ok:
        score += 15
        bits.append("región")
    elif locations:
        score += 5  # asumimos filtro de Prospeo ya filtró

    score = max(0, min(98, score))
    breakdown = "B2C · " + (" · ".join(bits) if bits else "base")
    return score, breakdown


def person_dict_to_lead(
    person: dict[str, Any],
    *,
    campaign_id: int,
    idx: int,
    country_hint: str | None,
    interests: list[str],
    locations: list[str],
) -> LeadCandidateRead | None:
    name = _person_display(person)
    if not name or name == "?":
        return None
    email, _ = extract_email_phone(person)
    if is_forbidden_email(email):
        email = None
    channels = merge_contact_channels(person)
    phone = channels.get("mobile_phone") or channels.get("phone")
    landline = channels.get("landline_phone")
    whatsapp = channels.get("whatsapp_number")
    linkedin = (
        person.get("linkedin_url")
        or person.get("person_linkedin_url")
        or person.get("linkedin")
        or ""
    )
    linkedin = str(linkedin).strip() or None
    role = (
        person.get("current_job_title")
        or person.get("job_title")
        or person.get("title")
        or person.get("headline")
        or ""
    )
    org = person.get("company") if isinstance(person.get("company"), dict) else {}
    if not org:
        org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
    company_name = (
        (org.get("name") if isinstance(org, dict) else None)
        or person.get("company_name")
        or ""
    )
    company_name = str(company_name).strip() or "Particular"
    pid = str(person.get("person_id") or person.get("id") or uuid4().hex[:12])
    score, breakdown = score_b2c_person(
        person,
        interests=interests,
        locations=locations,
        country_hint=country_hint,
    )

    return LeadCandidateRead(
        external_id=f"b2c-prospeo-{campaign_id}-{pid}-{idx}",
        provider="prospeo",
        first_name=(person.get("first_name") or "").strip() or None,
        last_name=(person.get("last_name") or "").strip() or None,
        name=name[:255],
        company_name=company_name[:255],
        role=str(role)[:255] if role else None,
        industry=(org.get("industry") if isinstance(org, dict) else None),
        country=country_hint,
        email=email,
        phone=phone,
        landline_phone=landline,
        whatsapp=whatsapp,
        whatsapp_number=whatsapp,
        linkedin_url=linkedin,
        compatibility_score=score,
        fit_tier="good" if score >= 45 else "low_fit",
        score_breakdown=breakdown,
        has_email=bool(email),
        has_phone=bool(phone),
        has_linkedin=is_personal_linkedin_url(linkedin),
        enriched_by_prospeo=bool(email or phone or linkedin),
        enrichment_source="prospeo_b2c",
        enrichment_confidence=score,
        contact_kind="person",
        visible_in_panel=True,
    )


def _email_usable(email: str | None) -> bool:
    e = (email or "").strip()
    return bool(e and "@" in e and not is_forbidden_email(e))


def _maybe_enrich_person(
    person: dict[str, Any],
    *,
    require_mobile: bool = False,
) -> dict[str, Any]:
    """Enrich-person si falta email usable o móvil completo (WhatsApp).

    Si search ya trajo móvil usable, no pedimos enrich_mobile otra vez (solo email).
    Si el preview clasifica como fijo → no pagar enrich_mobile.
    Tras enrich sin móvil WA → marcar para no reintentar.
    """
    email, _ = extract_email_phone(person)
    need_email = not _email_usable(email)
    want_mobile = bool(require_mobile) and not person_has_usable_mobile(person)
    need_mobile = decide_enrich_mobile(person, want_mobile=want_mobile)
    if want_mobile and not need_mobile and person_phone_preview_is_landline(person):
        marked = dict(person)
        marked["_nexus_skip_mobile_enrich"] = True
        if not need_email:
            return marked
        person = marked
    if not need_email and not need_mobile:
        return person
    pid = str(person.get("person_id") or person.get("id") or "").strip()
    if not pid:
        return person
    try:
        enriched = enrich_person_by_id(pid, require_mobile=need_mobile)
        return apply_enrich_mobile_result(
            person,
            enriched if isinstance(enriched, dict) else None,
            requested_mobile=need_mobile,
        )
    except Exception as e:
        _logger.debug("B2C enrich-person skipped for %s: %s", pid, e)
        if need_mobile:
            return apply_enrich_mobile_result(person, None, requested_mobile=True)
    return person


def search_b2c_people(
    campaign: Campaign,
    *,
    limit: int = 40,
    enrich_missing_email: bool = True,
    max_enrich: int = 8,
    require_mobile: bool = False,
    exclude_emails: set[str] | None = None,
    exclude_linkedin: set[str] | None = None,
    exclude_phones: set[str] | None = None,
) -> tuple[list[LeadCandidateRead], dict[str, Any]]:
    """Busca personas por ICP B2C con cascada de filtros Prospeo."""
    from app.services.prospect_ingestion import phone_identity_keys

    variants, build_meta = build_b2c_filter_variants(campaign, require_mobile=require_mobile)
    excl_em = {(e or "").strip().lower() for e in (exclude_emails or set()) if e}
    excl_li = set()
    for u in exclude_linkedin or set():
        key = linkedin_slug_key(u) or str(u or "").strip().lower()
        if key:
            excl_li.add(key)
    excl_phones = {str(p).strip() for p in (exclude_phones or set()) if p}
    interests = build_meta.get("interests") or []
    locations = build_meta.get("locations_resolved") or []

    seen: set[str] = set()
    people_raw: list[dict[str, Any]] = []
    diag: dict[str, Any] = {
        "mode": "b2c",
        "filters_tried": 0,
        "raw_hits": 0,
        "errors": [],
        "attempts": [],
        "require_mobile": require_mobile,
        "mobile_rejected": 0,
        "mobile_deferred": 0,
        "company_dupes_skipped": 0,
        "exclude_emails": len(excl_em),
        "exclude_linkedin": len(excl_li),
        "exclude_phones": len(excl_phones),
        **build_meta,
    }

    if not variants:
        diag["errors"].append(
            {
                "code": "NO_FILTERS",
                "msg": "ICP B2C insuficiente: indicá región, intereses o perfil.",
            }
        )
        return [], diag

    for label, filters in variants:
        if len(people_raw) >= limit * 2:
            break
        for page in (1, 2):
            if len(people_raw) >= limit * 2:
                break
            diag["filters_tried"] += 1
            hits, err, err_code, status, _preview = _search_person_raw(
                filters=filters, page=page
            )
            attempt = {
                "label": label,
                "page": page,
                "keys": list(filters.keys()),
                "hits": len(hits),
                "error_code": err_code,
                "status": status,
            }
            if err:
                attempt["error"] = err[:200]
                diag["errors"].append(
                    {"code": err_code, "msg": err[:200], "label": label, "status": status}
                )
                diag["attempts"].append(attempt)
                break  # no paginar si el filtro es inválido
            if err_code and not hits:
                diag["attempts"].append(attempt)
                # NO_RESULTS → probar siguiente variante; INVALID → cortar paginación
                if err_code in ("INVALID_DATAPOINTS", "INVALID_REQUEST", "INVALID_FILTER"):
                    break
                continue
            diag["raw_hits"] += len(hits)
            diag["attempts"].append(attempt)
            for person in hits:
                if not isinstance(person, dict):
                    continue
                key = str(
                    person.get("person_id")
                    or person.get("id")
                    or person.get("linkedin_url")
                    or f"{person.get('first_name')}|{person.get('last_name')}"
                )
                if key in seen:
                    continue
                email_early, phone_early = extract_email_phone(person)
                em_key = (email_early or "").strip().lower()
                li_key = linkedin_slug_key(
                    person.get("linkedin_url")
                    or person.get("linkedin")
                    or person.get("profile_url")
                ) or ""
                phone_keys = phone_identity_keys(phone_early, person.get("whatsapp"))
                if (
                    (em_key and em_key in excl_em)
                    or (li_key and li_key in excl_li)
                    or (phone_keys & excl_phones)
                ):
                    diag["company_dupes_skipped"] = int(diag.get("company_dupes_skipped") or 0) + 1
                    continue
                seen.add(key)
                people_raw.append(person)

        # Si ya tenemos buen volumen contactable, no seguir ampliando
        with_email = sum(1 for p in people_raw if extract_email_phone(p)[0])
        if with_email >= limit:
            break

    # Soft filter por intereses: si hay matches, priorizarlos; si no, conservar todos
    if interests and people_raw:
        ranked = sorted(
            people_raw,
            key=lambda p: interest_match_score(p, interests),
            reverse=True,
        )
        matched = [p for p in ranked if interest_match_score(p, interests) > 0]
        people_raw = matched if len(matched) >= max(5, limit // 4) else ranked

    # Enrich selectivo de email. Móvil WA: lazy en channel enrich al activar.
    enrich_count = 0
    if enrich_missing_email:
        if require_mobile:
            people_raw.sort(
                key=lambda p: (
                    0 if person_mobile_verified(p) else 1,
                    0 if person_has_usable_mobile(p) else 1,
                )
            )
        enriched_list: list[dict[str, Any]] = []
        for person in people_raw:
            email, _ = extract_email_phone(person)
            need_email = not _email_usable(email)
            if need_email and enrich_count < max_enrich:
                person = _maybe_enrich_person(person, require_mobile=False)
                enrich_count += 1
            if require_mobile and not person_has_usable_mobile(person):
                diag["mobile_deferred"] = int(diag.get("mobile_deferred") or 0) + 1
            enriched_list.append(person)
        people_raw = enriched_list
    elif require_mobile:
        for person in people_raw:
            if not person_has_usable_mobile(person):
                diag["mobile_deferred"] = int(diag.get("mobile_deferred") or 0) + 1
    diag["enriched"] = enrich_count

    leads: list[LeadCandidateRead] = []
    for person in people_raw:
        if len(leads) >= limit:
            break
        lead = person_dict_to_lead(
            person,
            campaign_id=campaign.id,
            idx=len(leads),
            country_hint=campaign.target_country,
            interests=interests,
            locations=locations,
        )
        if lead is None:
            continue
        em_key = (lead.email or "").strip().lower()
        li_key = linkedin_slug_key(lead.linkedin_url) or ""
        phone_keys = phone_identity_keys(
            getattr(lead, "phone", None), getattr(lead, "whatsapp", None)
        )
        if (
            (em_key and em_key in excl_em)
            or (li_key and li_key in excl_li)
            or (phone_keys & excl_phones)
        ):
            diag["company_dupes_skipped"] = int(diag.get("company_dupes_skipped") or 0) + 1
            continue
        leads.append(lead)

    # Ordenar por score B2C
    leads.sort(key=lambda L: int(L.compatibility_score or 0), reverse=True)
    diag["people_kept"] = len(leads)
    if not leads and not diag["errors"]:
        diag["errors"].append(
            {
                "code": "NO_RESULTS",
                "msg": (
                    "Prospeo no devolvió personas para este ICP B2C. "
                    "Ampliá región o intereses (ej. 'fitness coach', 'Argentina')."
                ),
            }
        )
    return leads[:limit], diag


# Compat: tests / callers antiguos
def build_b2c_person_filters(campaign: Campaign) -> list[dict[str, Any]]:
    variants, _ = build_b2c_filter_variants(campaign)
    return [f for _, f in variants]
