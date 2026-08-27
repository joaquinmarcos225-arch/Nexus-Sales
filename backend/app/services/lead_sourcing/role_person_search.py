"""B2B rol-first: Prospeo search-person por cargo + región (sin industria de empresa).

Over-fetch barato: muchas búsquedas (search), enrich-person solo si falta email
y el rol ya alineó con ICP. Importa solo los que pasan gate de calidad.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import LeadCandidateRead
from app.services.lead_sourcing.b2c_person_search import resolve_canonical_locations
from app.services.lead_sourcing.icp_import_gate import MIN_ROLE_MATCH_FOR_IMPORT
from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url, linkedin_slug_key
from app.services.lead_sourcing.prospeo_contact_validation import is_forbidden_email
from app.services.lead_sourcing.prospeo_lead_fit import score_prospeo_contact_fit
from app.services.lead_sourcing.prospeo_phone import (
    apply_enrich_mobile_result,
    contact_details_filter,
    decide_enrich_mobile,
    merge_contact_channels,
    person_has_usable_mobile,
    person_mobile_verified,
    person_phone_preview_is_landline,
)
from app.services.lead_sourcing.providers.prospeo_mvp import (
    _person_display,
    _search_person_raw,
    enrich_person_by_id,
    extract_email_phone,
)
from app.services.lead_sourcing.role_alignment import (
    best_icp_role_match,
    person_role_from_hit,
    prospeo_role_title_includes,
)
from app.services.lead_sourcing.timeouts_config import PROSPEO_SEARCH_THROTTLE_SEC

_logger = logging.getLogger(__name__)


def build_role_first_filter_variants(
    campaign: Campaign,
    *,
    require_mobile: bool = False,
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    locations = resolve_canonical_locations(campaign.target_country, max_locations=6)
    titles = prospeo_role_title_includes(campaign.target_role)
    from app.services.lead_sourcing.icp_industry_search import industry_search_terms

    industry_terms = industry_search_terms(getattr(campaign, "target_industry", None))[:3]
    # Prospeo company.industry labels (aprox. EN) + keywords ES del ICP.
    industry_keywords = list(industry_terms)
    ind_raw = (getattr(campaign, "target_industry", None) or "").strip()
    if ind_raw and ind_raw not in industry_keywords:
        industry_keywords.insert(0, ind_raw)
    # Mapeo corto a industrias Prospeo comunes.
    _PROSPEO_INDUSTRY_ALIASES: dict[str, list[str]] = {
        "inmobiliaria": ["Real Estate", "Real Estate Agents and Brokers", "Building Construction"],
        "real estate": ["Real Estate", "Real Estate Agents and Brokers"],
        "saas": ["Software Development", "IT Services and IT Consulting"],
        "fintech": ["Financial Services", "Banking"],
        "salud": ["Hospitals and Health Care", "Medical Practices"],
        "retail": ["Retail", "Retail Apparel and Fashion"],
    }
    key = ind_raw.lower()
    for alias, mapped in _PROSPEO_INDUSTRY_ALIASES.items():
        if alias in key:
            for m in mapped:
                if m not in industry_keywords:
                    industry_keywords.append(m)
            break

    meta = {
        "locations_resolved": locations,
        "titles_resolved": titles,
        "industry_keywords": industry_keywords,
        "target_role": (campaign.target_role or "").strip(),
        "require_mobile": require_mobile,
    }
    variants: list[tuple[str, dict[str, Any]]] = []
    contact_email = contact_details_filter(require_mobile=False, require_email=True)
    contact_mobile = contact_details_filter(require_mobile=True, require_email=False)
    contact_both = contact_details_filter(require_mobile=True, require_email=True)

    def _loc() -> dict[str, Any]:
        if locations:
            return {"person_location_search": {"include": locations[:5]}}
        return {}

    def _industry() -> dict[str, Any]:
        if not industry_keywords:
            return {}
        return {
            "company": {
                "industry": {"include": industry_keywords[:6]},
            }
        }

    if titles and locations:
        if require_mobile:
            variants.append(
                (
                    "loc+titles+mobile",
                    {
                        **_loc(),
                        "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                        **contact_mobile,
                    },
                )
            )
            variants.append(
                (
                    "loc+titles+mobile+email",
                    {
                        **_loc(),
                        "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                        **contact_both,
                    },
                )
            )
        if industry_keywords:
            variants.append(
                (
                    "loc+titles+industry+email",
                    {
                        **_loc(),
                        **_industry(),
                        "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                        **contact_email,
                    },
                )
            )
            variants.append(
                (
                    "loc+titles+industry",
                    {
                        **_loc(),
                        **_industry(),
                        "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                    },
                )
            )
        variants.append(
            (
                "loc+titles+email",
                {
                    **_loc(),
                    "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                    **contact_email,
                },
            )
        )
        variants.append(
            (
                "loc+titles",
                {
                    **_loc(),
                    "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                },
            )
        )
    elif titles:
        if require_mobile:
            variants.append(
                (
                    "titles+mobile",
                    {
                        "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                        **contact_mobile,
                    },
                )
            )
        variants.append(
            (
                "titles+email",
                {
                    "person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"},
                    **contact_email,
                },
            )
        )
        variants.append(
            (
                "titles",
                {"person_job_title": {"include": titles[:6], "match_mode": "CONTAINS"}},
            )
        )

    seen: set[str] = set()
    unique: list[tuple[str, dict[str, Any]]] = []
    for label, filt in variants:
        if not filt or label in seen:
            continue
        seen.add(label)
        unique.append((label, filt))
    return unique[:10], meta


def _org_blob(person: dict[str, Any]) -> dict[str, Any]:
    org = person.get("company") if isinstance(person.get("company"), dict) else {}
    if not org:
        org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
    return org if isinstance(org, dict) else {}


def _email_usable(email: str | None) -> bool:
    e = (email or "").strip()
    return bool(e and "@" in e and not is_forbidden_email(e))


def _maybe_enrich_if_needed(
    person: dict[str, Any],
    *,
    require_mobile: bool = False,
) -> dict[str, Any]:
    """Enrich cuando falta email usable, empresa, o móvil completo (WhatsApp).

    Si search ya trajo móvil usable, no pedimos enrich_mobile otra vez (solo email/empresa).
    Si el preview clasifica como fijo → no pagar enrich_mobile.
    Tras enrich sin móvil WA → marcar para no reintentar.
    """
    email, _ = extract_email_phone(person)
    org = _org_blob(person)
    company = str(org.get("name") or person.get("company_name") or "").strip()
    need_email = not _email_usable(email)
    need_company = not company
    want_mobile = bool(require_mobile) and not person_has_usable_mobile(person)
    need_mobile = decide_enrich_mobile(person, want_mobile=want_mobile)
    if want_mobile and not need_mobile and person_phone_preview_is_landline(person):
        # Fijo detectado: no gastar; seguir sin WA.
        marked = dict(person)
        marked["_nexus_skip_mobile_enrich"] = True
        if not need_email and not need_company:
            return marked
        person = marked
    if not need_email and not need_company and not need_mobile:
        return person
    pid = str(person.get("person_id") or person.get("id") or "").strip()
    if not pid:
        return person
    try:
        # require_mobile de API = ¿falta el móvil ahora?, no el flag de campaña.
        enriched = enrich_person_by_id(pid, require_mobile=need_mobile)
        return apply_enrich_mobile_result(
            person,
            enriched if isinstance(enriched, dict) else None,
            requested_mobile=need_mobile,
        )
    except Exception as e:  # noqa: BLE001
        _logger.debug("role-first enrich-person skipped for %s: %s", pid, e)
        if need_mobile:
            return apply_enrich_mobile_result(person, None, requested_mobile=True)
    return person


def person_dict_to_role_lead(
    person: dict[str, Any],
    *,
    campaign: Campaign,
    idx: int,
    fit_threshold: int = 70,
) -> LeadCandidateRead | None:
    name = _person_display(person)
    if not name or name == "?":
        return None
    role = person_role_from_hit(person)
    role_score, _ = best_icp_role_match(campaign.target_role, role)
    if role_score < MIN_ROLE_MATCH_FOR_IMPORT:
        return None

    email, _ = extract_email_phone(person)
    if email and is_forbidden_email(email):
        email = None
    channels = merge_contact_channels(person)
    phone = channels.get("mobile_phone") or channels.get("phone")
    landline = channels.get("landline_phone")
    whatsapp = channels.get("whatsapp_number")
    linkedin = (
        person.get("linkedin_url")
        or person.get("linkedin")
        or person.get("profile_url")
        or ""
    )
    linkedin = str(linkedin).strip() or None
    if not email and not is_personal_linkedin_url(linkedin):
        return None

    org = _org_blob(person)
    company_name = str(org.get("name") or person.get("company_name") or "").strip()
    domain = (
        str(org.get("domain") or org.get("website") or person.get("company_domain") or "")
        .strip()
        .lower()
        .removeprefix("www.")
        or None
    )
    from app.services.outreach_display_names import resolve_prospect_company_name

    company_name = resolve_prospect_company_name(
        company_name=company_name,
        email=email,
        domain=domain,
    ) or company_name
    # Nunca stampiar el label ICP como país del prospecto (rompe región).
    country = person.get("country") or (org.get("country") if org else None)
    industry = org.get("industry") if org else None
    company_size = (
        org.get("company_size")
        or org.get("size")
        or person.get("company_size")
        or None
    )
    emp_raw = (
        org.get("employee_count")
        or org.get("employees_count")
        or org.get("employees")
        or person.get("employee_count")
    )
    try:
        employee_count = int(emp_raw) if emp_raw is not None else None
    except (TypeError, ValueError):
        employee_count = None

    # Región y tamaño: hard filter solo con evidencia.
    # Hits de search-person a menudo no traen country/size aunque el filtro
    # person_location_search ya restringió la geo → no descartar por "desconocido".
    from app.services.campaign_icp import is_icp_token_empty
    from app.services.lead_sourcing.icp_import_gate import (
        MIN_GEO_HARD,
        MIN_SIZE_HARD,
        geo_hard_score,
        parse_employee_bounds,
        size_hard_score,
    )

    if campaign.target_country and not is_icp_token_empty(campaign.target_country):
        country_str = str(country).strip() if country else ""
        if country_str:
            geo_score, _ = geo_hard_score(country_str, campaign.target_country)
            if geo_score < MIN_GEO_HARD:
                return None
        # sin country en el hit: confiar en el filtro de ubicación de Prospeo

    if campaign.target_company_size and not is_icp_token_empty(campaign.target_company_size):
        known_size = parse_employee_bounds(
            company_size=str(company_size) if company_size else None,
            employee_count=employee_count,
        )
        if known_size is not None:
            size_score, _ = size_hard_score(
                campaign_size=campaign.target_company_size,
                company_size=str(company_size) if company_size else None,
                employee_count=employee_count,
            )
            if size_score < MIN_SIZE_HARD:
                return None
        # tamaño desconocido: no hard-reject (sigue al score blando / import gate)

    compat, breakdown = score_prospeo_contact_fit(
        email=email,
        company_domain=domain,
        company_icp_score=60,
        role=role,
        fit_threshold=fit_threshold,
        icp_target_role=campaign.target_role,
        icp_target_industry=campaign.target_industry,
        icp_target_country=campaign.target_country,
        icp_target_company_size=campaign.target_company_size,
        prospect_industry=str(industry) if industry else None,
        prospect_country=str(country) if country else None,
        linkedin_url=linkedin,
    )
    pid = str(person.get("person_id") or person.get("id") or uuid4().hex[:12])
    return LeadCandidateRead(
        external_id=f"role-prospeo-{campaign.id}-{pid}-{idx}",
        provider="prospeo",
        first_name=(person.get("first_name") or "").strip() or None,
        last_name=(person.get("last_name") or "").strip() or None,
        name=name[:255],
        company_name=company_name[:255],
        role=str(role)[:255] if role else None,
        industry=str(industry)[:128] if industry else None,
        country=str(country)[:128] if country else None,
        email=email,
        phone=phone,
        landline_phone=landline,
        whatsapp=whatsapp,
        whatsapp_number=whatsapp,
        linkedin_url=linkedin,
        company_domain=domain,
        company_website=f"https://{domain}" if domain else None,
        company_size=str(company_size)[:64] if company_size else None,
        employee_count=employee_count,
        compatibility_score=compat,
        fit_tier="good" if compat >= fit_threshold else "low_fit",
        score_breakdown=f"Rol-first · {breakdown}",
        has_email=bool(email),
        has_phone=bool(phone),
        has_linkedin=is_personal_linkedin_url(linkedin),
        enriched_by_prospeo=True,
        enrichment_source="prospeo_role_first",
        enrichment_confidence=compat,
        contact_kind="person",
        visible_in_panel=True,
    )


def search_role_first_people(
    campaign: Campaign,
    *,
    limit: int = 40,
    max_enrich: int = 10,
    exclude_emails: set[str] | None = None,
    exclude_linkedin: set[str] | None = None,
    exclude_phones: set[str] | None = None,
    require_mobile: bool = False,
) -> tuple[list[LeadCandidateRead], dict[str, Any]]:
    """
    Busca por rol ICP + región.
    Search over-fetch (hasta ~2.5× limit); enrich-person si falta email/empresa o móvil (WA).
    exclude_* = contactos ya en la empresa (otras campañas) → se saltan para traer gente nueva.
    """
    from app.services.prospect_ingestion import phone_identity_keys

    variants, build_meta = build_role_first_filter_variants(
        campaign, require_mobile=require_mobile
    )
    excl_em = {(e or "").strip().lower() for e in (exclude_emails or set()) if e}
    excl_li = set()
    for u in exclude_linkedin or set():
        key = linkedin_slug_key(u) or str(u or "").strip().lower()
        if key:
            excl_li.add(key)
    excl_phones = {str(p).strip() for p in (exclude_phones or set()) if p}
    diag: dict[str, Any] = {
        "mode": "role_first",
        "filters_tried": 0,
        "raw_hits": 0,
        "role_rejected": 0,
        "company_dupes_skipped": 0,
        "enriched": 0,
        "errors": [],
        "attempts": [],
        "exclude_emails": len(excl_em),
        "exclude_linkedin": len(excl_li),
        "exclude_phones": len(excl_phones),
        "require_mobile": require_mobile,
        "mobile_rejected": 0,
        "mobile_deferred": 0,
        **build_meta,
    }
    if not variants:
        diag["errors"].append(
            {"code": "NO_FILTERS", "msg": "Falta rol ICP para búsqueda person-first."}
        )
        return [], diag

    seen: set[str] = set()
    people_raw: list[dict[str, Any]] = []
    # Si hay muchos ya usados en la empresa, over-fetch más y más páginas.
    exclude_pressure = len(excl_em) + len(excl_li)
    search_cap = max(limit * 2, min(120, limit * 3 + exclude_pressure))
    pages = (1, 2, 3, 4) if exclude_pressure >= 5 else (1, 2)

    for label, filters in variants:
        if len(people_raw) >= search_cap:
            break
        for page in pages:
            if len(people_raw) >= search_cap:
                break
            diag["filters_tried"] += 1
            hits, err, err_code, status, _preview = _search_person_raw(
                filters=filters, page=page
            )
            diag["attempts"].append(
                {
                    "label": label,
                    "page": page,
                    "hits": len(hits),
                    "error": err,
                    "error_code": err_code,
                    "status": status,
                }
            )
            if err and not hits:
                diag["errors"].append({"code": err_code or "ERR", "msg": err[:200]})
                if err_code and "RATE_LIMIT" in str(err_code).upper():
                    break
                continue
            diag["raw_hits"] += len(hits)
            for person in hits:
                pid = str(person.get("person_id") or person.get("id") or "").strip()
                dedupe = pid or (
                    f"{person.get('linkedin_url')}|{person.get('first_name')}|"
                    f"{person.get('last_name')}"
                )
                if not dedupe or dedupe in seen:
                    continue
                role = person_role_from_hit(person)
                score, _ = best_icp_role_match(campaign.target_role, role)
                if score < MIN_ROLE_MATCH_FOR_IMPORT:
                    diag["role_rejected"] += 1
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
                    diag["company_dupes_skipped"] += 1
                    continue
                seen.add(dedupe)
                people_raw.append(person)
            if PROSPEO_SEARCH_THROTTLE_SEC > 0:
                time.sleep(PROSPEO_SEARCH_THROTTLE_SEC)

    # Enrich selectivo de email/empresa. Móvil WA: lazy en channel enrich al activar
    # (no gastar 10 créditos Prospeo acá; si search ya trajo número, se usa gratis).
    enrich_budget = max(0, int(max_enrich))
    if require_mobile:
        people_raw.sort(
            key=lambda p: (
                0 if person_mobile_verified(p) else 1,
                0 if person_has_usable_mobile(p) else 1,
            )
        )
    enriched_raw: list[dict[str, Any]] = []
    for person in people_raw:
        email, _ = extract_email_phone(person)
        org = _org_blob(person)
        company = str(org.get("name") or person.get("company_name") or "").strip()
        need_email = not _email_usable(email)
        need_company = not company
        if (need_email or need_company) and enrich_budget > 0:
            person = _maybe_enrich_if_needed(person, require_mobile=False)
            enrich_budget -= 1
            diag["enriched"] += 1
            if PROSPEO_SEARCH_THROTTLE_SEC > 0:
                time.sleep(PROSPEO_SEARCH_THROTTLE_SEC)
        if require_mobile and not person_has_usable_mobile(person):
            diag["mobile_deferred"] = int(diag.get("mobile_deferred") or 0) + 1
        enriched_raw.append(person)

    leads: list[LeadCandidateRead] = []
    for idx, person in enumerate(enriched_raw):
        if len(leads) >= limit:
            break
        lead = person_dict_to_role_lead(person, campaign=campaign, idx=idx)
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
            diag["company_dupes_skipped"] += 1
            continue
        leads.append(lead)

    diag["kept"] = len(leads)
    return leads, diag
