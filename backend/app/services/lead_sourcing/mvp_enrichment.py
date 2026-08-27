"""Enriquecimiento MVP: empresa → personas Prospeo → contactos verificados."""

from __future__ import annotations

import os
import re
import time
from dataclasses import replace
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.schemas.mvp_outreach import CompanyContactRowRead, MvpContactMetricsRead
from app.services.lead_sourcing.contact_identity import (
    filter_generic_contacts,
    filter_pipeline_people,
    is_outreach_ready_person,
    is_pipeline_contact,
    is_pipeline_generic_contact,
    is_real_person_lead,
)
from app.services.lead_sourcing.generic_email_fallback import build_generic_email_leads
from app.services.lead_sourcing.corporate_domain_resolver import (
    companies_ready_for_prospeo,
    company_has_verified_domain,
    refresh_domain_trust_on_company,
    resolve_corporate_domains_for_companies,
)
from app.services.lead_sourcing.prospeo_contact_validation import (
    email_domain,
    is_directory_host,
    is_forbidden_email,
    is_prospeo_searchable_domain,
    validate_prospeo_contact,
)
from app.services.lead_sourcing.providers.base import ProviderAPIError
from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain
from app.services.lead_sourcing.linkedin_identity import is_personal_linkedin_url, normalize_linkedin_url
from app.services.lead_sourcing.prospeo_lead_fit import score_prospeo_contact_fit
from app.services.lead_sourcing.prospeo_phone import merge_contact_channels
from app.services.lead_sourcing.prospecting_lead import (
    is_prospecting_outreach_ready,
    prospecting_missing_fields,
)
from app.services.lead_sourcing.providers.prospeo_mvp import (
    confidence_from_person,
    enrich_company_domain,
    enrich_person_by_id,
    enrich_person_record,
    extract_email_phone,
    search_people_at_company_with_diagnostic,
)
from app.services.lead_sourcing.providers.registry import get_contact_enrichment_provider
from app.services.lead_sourcing.timeouts_config import (
    DOMAIN_RESOLVE_MAX_PER_ENRICH,
    DOMAIN_RESOLVE_PER_COMPANY_SEC,
    PROSPEO_ENRICH_BATCH_SIZE,
    PROSPEO_ENRICH_MAX_SEC,
    PROSPEO_ENRICH_PER_COMPANY_SEC,
    PROSPEO_SEARCH_THROTTLE_SEC,
)


def _batch_size() -> int:
    try:
        return max(1, min(int(os.getenv("PROSPEO_ENRICH_BATCH_SIZE", "8")), 15))
    except ValueError:
        return 8


def _build_enrich_progress(*, cursor: int, total: int, last_batch: int) -> dict[str, Any]:
    processed = min(cursor, total)
    return {
        "processed": processed,
        "total": total,
        "has_more": cursor < total,
        "batch_size": _batch_size(),
        "last_batch_count": last_batch,
    }


def _people_per_company() -> int:
    try:
        return max(1, min(int(os.getenv("PROSPEO_PEOPLE_PER_COMPANY", "3")), 8))
    except ValueError:
        return 3


def _person_global_key(email: str | None, linkedin: str | None, name: str) -> str:
    em = (email or "").strip().lower()
    li = (linkedin or "").strip().lower()
    if em or li:
        return f"{em}|{li}"
    return f"name:{name.strip().lower()}"


def _company_match_keys(company: CompanyCandidateRead) -> set[str]:
    keys: set[str] = set()
    for raw in (company.canonical_key, company.external_id, company.name):
        s = (raw or "").strip().lower()
        if s:
            keys.add(s)
    dom = (company.company_domain or "").strip().lower().removeprefix("www.")
    if dom:
        keys.add(dom)
    return keys


def _lead_matches_company(lead: LeadCandidateRead, company_keys: set[str]) -> bool:
    for raw in (lead.linked_company_key, lead.company_name):
        s = (raw or "").strip().lower()
        if s and s in company_keys:
            return True
    cn = (lead.company_name or "").strip().lower()
    for k in company_keys:
        if len(k) >= 3 and (k == cn or k in cn or cn in k):
            return True
    dom = (lead.company_domain or "").strip().lower().removeprefix("www.")
    if dom and dom in company_keys:
        return True
    return False


def _sync_prospeo_company_diag(
    company_diag: dict[str, Any],
    *,
    search_hits: list,
    found_valid: int,
    enrich_discards: list,
) -> dict[str, Any]:
    """Alinea contadores de diagnóstico con contactos realmente guardados."""
    from app.services.lead_sourcing.prospeo_api_health import SEARCH_OUTCOME_NO_RESULTS, SEARCH_OUTCOME_OK

    reqs = company_diag.get("requests")
    api_hits = 0
    if isinstance(reqs, list):
        api_hits = sum(
            int(q.get("results_count") or 0) for q in reqs if isinstance(q, dict)
        )
    raw = max(
        int(company_diag.get("prospeo_results") or 0),
        int(company_diag.get("after_dedupe") or 0),
        api_hits,
        len(search_hits),
        found_valid,
    )
    out = dict(company_diag)
    out["prospeo_results"] = raw
    out["after_dedupe"] = max(int(out.get("after_dedupe") or 0), len(search_hits))
    out["valid_results"] = found_valid
    out["discarded_count"] = len(enrich_discards)
    if found_valid > 0:
        out["search_outcome"] = SEARCH_OUTCOME_OK
        out["status_message"] = f"{found_valid} válidos / {raw} Prospeo"
        out["discard_reason"] = (
            f"{len(enrich_discards)} descartados en enrich" if enrich_discards else "—"
        )
    elif raw > 0:
        out["search_outcome"] = out.get("search_outcome") or SEARCH_OUTCOME_NO_RESULTS
        if enrich_discards:
            reasons: dict[str, int] = {}
            for d in enrich_discards:
                r = d.get("reason") or "?"
                reasons[r] = reasons.get(r, 0) + 1
            top = sorted(reasons.items(), key=lambda x: -x[1])[:2]
            out["discard_reason"] = "; ".join(f"{r} ({n})" for r, n in top)
            out["status_message"] = f"0 válidos — {raw} Prospeo, todos descartados"
        else:
            out["status_message"] = f"{raw} en API, 0 tras validación"
            out["discard_reason"] = "Sin personas válidas tras filtros"
    else:
        out["status_message"] = "0 resultados reales"
        out["discard_reason"] = "Prospeo devolvió 0 personas (revisar dominio/filtros API)"
    return out


def _upsert_prospeo_debug(debug_rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    name = (row.get("company_name") or "").strip().lower()
    if not name:
        debug_rows.append(row)
        return
    for i, existing in enumerate(debug_rows):
        if (existing.get("company_name") or "").strip().lower() == name:
            debug_rows[i] = {**existing, **row}
            return
    debug_rows.append(row)


def _person_display_name(person: dict[str, Any]) -> str:
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    full = (person.get("full_name") or person.get("name") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return full or ""


def _person_country_from_hit(
    person: dict[str, Any],
    company: CompanyCandidateRead,
) -> str | None:
    """País del contacto (Prospeo/location), no el hint de búsqueda web."""
    from app.services.lead_sourcing.icp_region import infer_country_from_text

    org = person.get("company") or person.get("organization") or person.get("current_company") or {}
    if not isinstance(org, dict):
        org = {}

    loc = person.get("location") or person.get("person_location") or person.get("geo")
    loc_country: str | None = None
    if isinstance(loc, dict):
        loc_country = (
            loc.get("country")
            or loc.get("country_name")
            or loc.get("country_code")
        )
    elif isinstance(loc, str) and loc.strip():
        loc_country = infer_country_from_text(loc) or loc.strip()

    raw = (
        person.get("country")
        or loc_country
        or org.get("country")
        or org.get("company_country")
        or org.get("hq_country")
        or org.get("location_country")
    )
    if raw:
        return str(raw).strip()[:128]
    return company.country


def _apply_company_enrichment(company: CompanyCandidateRead, firmo: dict[str, Any]) -> CompanyCandidateRead:
    if not firmo:
        domain = _website_domain(company.website_url)
        return company.model_copy(
            update={
                "company_domain": domain,
                "enrichment_source": company.enrichment_source or "web_search",
            }
        )
    firmo_dom = _website_domain(firmo.get("website") or firmo.get("company_website"))
    web_dom = _website_domain(company.website_url)
    domain = firmo_dom if firmo_dom and not is_directory_host(firmo_dom) else None
    if not domain and web_dom and not is_directory_host(web_dom):
        domain = web_dom
    industry = firmo.get("industry") or firmo.get("company_industry") or company.industry
    country = (
        firmo.get("country")
        or firmo.get("company_country")
        or firmo.get("hq_country")
        or firmo.get("location_country")
        or company.country
    )
    size_raw = firmo.get("employee_count") or firmo.get("headcount") or firmo.get("company_size")
    emp_count: int | None = company.employee_count
    company_size = company.company_size
    if isinstance(size_raw, int):
        emp_count = size_raw
        company_size = str(size_raw)
    elif isinstance(size_raw, str) and size_raw.strip():
        company_size = size_raw.strip()[:64]
        nums = re.findall(r"\d+", size_raw.replace(",", ""))
        if nums:
            try:
                emp_count = int(nums[0])
            except ValueError:
                pass
    corp_email = None
    email_obj = firmo.get("email") or {}
    if isinstance(email_obj, dict):
        corp_email = email_obj.get("email")
    return company.model_copy(
        update={
            "company_domain": domain,
            "industry": str(industry)[:255] if industry else company.industry,
            "country": str(country)[:128] if country else company.country,
            "company_size": company_size,
            "employee_count": emp_count,
            "corporate_email": str(corp_email).strip() if corp_email else company.corporate_email,
            "enrichment_source": "prospeo",
            "enrichment_confidence": 80,
        },
    )


def _lead_from_prospeo_person(
    *,
    person: dict[str, Any],
    company: CompanyCandidateRead,
    campaign_id: int,
    fit_score: int,
    fit_threshold: int,
    idx: int,
    icp_target_role: str | None = None,
    icp_target_industry: str | None = None,
    icp_target_country: str | None = None,
    icp_target_company_size: str | None = None,
) -> LeadCandidateRead | None:
    name = _person_display_name(person)
    if not name:
        return None
    channels = merge_contact_channels(person)
    email = channels.get("email")
    mobile = channels.get("mobile_phone") or channels.get("whatsapp_number")
    landline = channels.get("landline_phone")
    phone = mobile or landline
    whatsapp_number = channels.get("whatsapp_number")
    phone_source = channels.get("phone_source")
    linkedin = channels.get("linkedin_url")
    if is_forbidden_email(email):
        return None
    role = (
        person.get("current_job_title")
        or person.get("job_title")
        or person.get("title")
        or person.get("headline")
        or ""
    )
    from app.services.lead_sourcing.icp_import_gate import is_noisy_prospect

    if is_noisy_prospect(role=str(role) if role else None, linkedin_url=linkedin):
        return None
    person_country = _person_country_from_hit(person, company)
    domain = company.company_domain or _website_domain(company.website_url)
    conf = confidence_from_person(person)
    compat, breakdown = score_prospeo_contact_fit(
        email=email,
        company_domain=domain,
        company_icp_score=fit_score,
        role=str(role) if role else None,
        fit_threshold=fit_threshold,
        icp_target_role=icp_target_role,
        icp_target_industry=icp_target_industry,
        icp_target_country=icp_target_country,
        icp_target_company_size=icp_target_company_size,
        prospect_industry=company.industry,
        prospect_country=person_country,
        company_size=company.company_size,
        linkedin_url=linkedin,
    )
    key = company.canonical_key or company.external_id or company.name
    lead = LeadCandidateRead(
        external_id=f"prospeo-{campaign_id}-{key[:20]}-{idx}",
        provider="prospeo",
        name=name[:255],
        company_name=(company.name or "").strip()[:255] or "",
        role=str(role)[:255] if role else None,
        industry=company.industry,
        country=person_country,
        email=email,
        phone=mobile,
        landline_phone=landline,
        whatsapp=whatsapp_number,
        whatsapp_number=whatsapp_number,
        phone_source=phone_source,
        linkedin_url=linkedin,
        company_website=company.website_url or (f"https://{domain}" if domain else None),
        company_domain=domain,
        linked_company_key=key,
        compatibility_score=compat,
        fit_tier="good" if compat >= fit_threshold else "low_fit",
        score_breakdown=f"Prospeo · confianza {conf}% · {breakdown}",
        has_email=bool(email),
        has_phone=bool(phone),
        has_linkedin=is_personal_linkedin_url(linkedin),
        enriched_by_prospeo=True,
        enrichment_source="prospeo",
        enrichment_confidence=conf,
        contact_kind="person",
        company_size=company.company_size,
    )
    if not is_pipeline_contact(lead):
        return None
    return lead


def _strip_placeholders(people: list[LeadCandidateRead]) -> list[LeadCandidateRead]:
    return filter_pipeline_people(people)


def reconcile_prospeo_search_debug(
    debug_rows: list[dict[str, Any]],
    companies: list[CompanyCandidateRead],
    people: list[LeadCandidateRead],
) -> list[dict[str, Any]]:
    """Corrige filas de diagnóstico desactualizadas según contactos guardados."""
    if not debug_rows:
        return debug_rows
    out = [dict(r) for r in debug_rows if isinstance(r, dict)]
    for c in companies:
        if c.result_kind != "company":
            continue
        ckeys = _company_match_keys(c)
        reals = [
            p
            for p in people
            if (p.contact_kind or "person") == "person" and _lead_matches_company(p, ckeys)
        ]
        generics = [
            p
            for p in people
            if (p.contact_kind or "") == "generic_email" and _lead_matches_company(p, ckeys)
        ]
        total_valid = len(reals) + len(generics)
        if total_valid == 0:
            continue
        name = (c.name or "").strip().lower()
        for i, row in enumerate(out):
            if (row.get("company_name") or "").strip().lower() != name:
                continue
            synced = _sync_prospeo_company_diag(
                row,
                search_hits=reals,
                found_valid=total_valid,
                enrich_discards=[],
            )
            synced["valid_results"] = len(reals)
            synced["prospeo_results"] = max(
                int(synced.get("prospeo_results") or 0),
                len(reals),
                total_valid,
            )
            if generics:
                synced["discard_reason"] = (
                    f"{len(reals)} reales · {len(generics)} genéricos"
                )
            out[i] = synced
            break
    return out


def refresh_prospeo_people_scores(
    people: list[LeadCandidateRead],
    companies: list[CompanyCandidateRead],
    *,
    fit_threshold: int,
    icp_target_role: str | None = None,
    icp_target_industry: str | None = None,
    icp_target_country: str | None = None,
    icp_target_company_size: str | None = None,
) -> list[LeadCandidateRead]:
    """Re-calcula score de contactos Prospeo/genéricos ya guardados (p. ej. ICP 0%)."""
    index: dict[str, CompanyCandidateRead] = {}
    for c in companies:
        if c.result_kind != "company":
            continue
        for k in _company_match_keys(c):
            index[k] = c

    refreshed: list[LeadCandidateRead] = []
    for p in people:
        is_prospeo = (p.provider or "") == "prospeo" or (p.contact_kind or "") == "person"
        is_generic = (p.contact_kind or "") == "generic_email"
        if not is_prospeo and not is_generic:
            refreshed.append(p)
            continue
        company: CompanyCandidateRead | None = None
        lk = (p.linked_company_key or p.company_name or "").strip().lower()
        if lk and lk in index:
            company = index[lk]
        if company is None:
            for c in companies:
                if c.result_kind == "company" and _lead_matches_company(p, _company_match_keys(c)):
                    company = c
                    break
        if company is None:
            refreshed.append(p)
            continue
        compat, breakdown = score_prospeo_contact_fit(
            email=p.email,
            company_domain=p.company_domain or company.company_domain,
            company_icp_score=max(company.icp_relevance_score or 0, fit_threshold),
            role=p.role,
            fit_threshold=fit_threshold,
            is_generic=is_generic,
            icp_target_role=icp_target_role,
            icp_target_industry=icp_target_industry,
            icp_target_country=icp_target_country,
            icp_target_company_size=icp_target_company_size,
            prospect_industry=p.industry or company.industry,
            prospect_country=p.country or company.country,
            company_size=company.company_size,
            linkedin_url=p.linkedin_url,
        )
        refreshed.append(
            p.model_copy(
                update={
                    "compatibility_score": compat,
                    "fit_tier": "good" if compat >= fit_threshold else "low_fit",
                    "score_breakdown": breakdown,
                }
            )
        )
    return refreshed


def compute_mvp_metrics(
    companies: list[CompanyCandidateRead],
    people: list[LeadCandidateRead],
    *,
    fit_threshold: int,
) -> MvpContactMetricsRead:
    company_rows = [c for c in companies if c.result_kind == "company"]
    real_people = filter_pipeline_people(people)
    generic = filter_generic_contacts(people)
    emails = sum(
        1
        for p in real_people
        if (p.email or "").strip() and "@" in (p.email or "") and not is_forbidden_email(p.email)
    )
    ready_person = sum(1 for p in real_people if is_outreach_ready_person(p, fit_threshold=fit_threshold))
    return MvpContactMetricsRead(
        companies_found=len(company_rows),
        contacts_found=len(real_people),
        generic_emails_found=len(generic),
        emails_found=emails + len(generic),
        contacts_ready_outreach=ready_person,
    )


def refresh_prospecting_contact_fields(
    people: list[LeadCandidateRead],
) -> list[LeadCandidateRead]:
    """Normaliza LinkedIn personal y flags al leer contactos guardados."""
    out: list[LeadCandidateRead] = []
    for p in people:
        if (p.contact_kind or "") not in ("person", "generic_email") and (p.provider or "") != "prospeo":
            out.append(p)
            continue
        li = normalize_linkedin_url(p.linkedin_url)
        out.append(
            p.model_copy(
                update={
                    "linkedin_url": li,
                    "has_linkedin": is_personal_linkedin_url(li),
                }
            )
        )
    return out


def build_prospecting_lead_rows(
    people: list[LeadCandidateRead],
    *,
    fit_threshold: int,
) -> list:
    from app.schemas.mvp_outreach import ProspectingLeadRowRead

    rows: list[ProspectingLeadRowRead] = []
    for p in filter_pipeline_people(people):
        li = normalize_linkedin_url(p.linkedin_url)
        missing = prospecting_missing_fields(p)
        rows.append(
            ProspectingLeadRowRead(
                external_id=p.external_id,
                person_name=p.name,
                company_name=p.company_name or "—",
                role=(p.role or "").strip() or None,
                email=(p.email or "").strip() or None,
                linkedin_url=li,
                phone=(p.phone or "").strip() or None,
                whatsapp_number=(p.whatsapp_number or p.whatsapp or "").strip() or None,
                phone_source=p.phone_source,
                outreach_ready=is_prospecting_outreach_ready(p, fit_threshold=fit_threshold),
                linkedin_valid=is_personal_linkedin_url(li),
                missing_fields=missing,
            )
        )
    rows.sort(key=lambda r: (-int(r.outreach_ready), r.company_name, r.person_name))
    return rows


def build_company_contact_rows(
    companies: list[CompanyCandidateRead],
    people: list[LeadCandidateRead],
    *,
    fit_threshold: int,
) -> list[CompanyContactRowRead]:
    company_rows = [
        c for c in companies if c.result_kind == "company"
    ]
    company_rows.sort(key=lambda c: -(c.icp_relevance_score or 0))
    real_people = filter_pipeline_people(people)
    generic_people = filter_generic_contacts(people)

    rows: list[CompanyContactRowRead] = []
    for c in company_rows:
        ckeys = _company_match_keys(c)
        if (c.domain_trust or "") == "doubtful":
            rows.append(
                CompanyContactRowRead(
                    company_external_id=c.external_id,
                    company_name=c.name,
                    website=c.website_url,
                    icp_score=c.icp_relevance_score,
                    status_message=f"Dominio dudoso ({c.company_domain or '—'})",
                )
            )
            continue
        if not c.company_domain or is_directory_host(c.company_domain):
            rows.append(
                CompanyContactRowRead(
                    company_external_id=c.external_id,
                    company_name=c.name,
                    website=c.website_url,
                    icp_score=c.icp_relevance_score,
                    status_message="Sin dominio corporativo",
                )
            )
            continue
        reals = [p for p in real_people if _lead_matches_company(p, ckeys)]
        generics = [p for p in generic_people if _lead_matches_company(p, ckeys)]
        if reals:
            for p in reals:
                rows.append(
                    CompanyContactRowRead(
                        company_external_id=c.external_id,
                        company_name=c.name,
                        website=c.website_url,
                        icp_score=c.icp_relevance_score,
                        contact_external_id=p.external_id,
                        person_name=p.name,
                        role=p.role,
                        email=p.email,
                        phone=(p.mobile_phone or p.phone or "").strip() or None,
                        whatsapp_number=(p.whatsapp_number or p.whatsapp or "").strip() or None,
                        landline_phone=(getattr(p, "landline_phone", None) or "").strip() or None,
                        linkedin_url=p.linkedin_url,
                        confidence=p.enrichment_confidence,
                        source=p.enrichment_source or p.provider,
                        status_message="Contacto real",
                    )
                )
        if generics:
            for p in generics:
                rows.append(
                    CompanyContactRowRead(
                        company_external_id=c.external_id,
                        company_name=c.name,
                        website=c.website_url,
                        icp_score=c.icp_relevance_score,
                        contact_external_id=p.external_id,
                        person_name=p.name,
                        role=p.role,
                        email=p.email,
                        phone=(p.mobile_phone or p.phone or "").strip() or None,
                        whatsapp_number=(p.whatsapp_number or p.whatsapp or "").strip() or None,
                        landline_phone=(getattr(p, "landline_phone", None) or "").strip() or None,
                        linkedin_url=p.linkedin_url,
                        confidence=p.enrichment_confidence,
                        source="generic_pattern",
                        status_message="Email genérico / no verificado",
                    )
                )
        if not reals and not generics:
            rows.append(
                CompanyContactRowRead(
                    company_external_id=c.external_id,
                    company_name=c.name,
                    website=c.website_url,
                    icp_score=c.icp_relevance_score,
                    status_message="Sin contacto encontrado",
                )
            )
    return rows


def run_mvp_company_enrichment(
    *,
    companies: list[CompanyCandidateRead],
    people: list[LeadCandidateRead],
    campaign: Campaign,
    fit_threshold: int,
    log_fn=None,
    company_offset: int = 0,
    max_companies_per_run: int | None = None,
    on_checkpoint: Callable[[list[CompanyCandidateRead], list[LeadCandidateRead], dict[str, Any]], None]
    | None = None,
    skip_person_detail_enrich: bool = True,
    skip_company_firmographics: bool = True,
    cached_prospeo_health: dict[str, Any] | None = None,
    db: Session | None = None,
) -> tuple[list[CompanyCandidateRead], list[LeadCandidateRead], dict[str, Any]]:
    prospeo = get_contact_enrichment_provider()
    people_clean = _strip_placeholders(people)

    if not prospeo.is_configured():
        return companies, people_clean, {"skipped": True, "reason": "prospeo_not_configured"}

    from app.services.lead_sourcing.prospeo_api_health import (
        SEARCH_OUTCOME_NO_RESULTS,
        SEARCH_OUTCOME_OK,
        effective_prospeo_search_blocked,
        fetch_prospeo_account_health,
        is_search_blocked_outcome,
        merge_health_from_api_error,
        outcome_status_message,
        sanitize_prospeo_health_dict,
        search_outcome_from_health,
    )

    if isinstance(cached_prospeo_health, dict) and cached_prospeo_health.get("configured"):
        from app.services.lead_sourcing.prospeo_api_health import ProspeoHealth

        prospeo_health = ProspeoHealth(
            configured=True,
            remaining_credits=cached_prospeo_health.get("remaining_credits"),
            used_credits=cached_prospeo_health.get("used_credits"),
            current_plan=cached_prospeo_health.get("current_plan"),
            rate_limited=bool(cached_prospeo_health.get("rate_limited")),
            insufficient_credits=bool(cached_prospeo_health.get("insufficient_credits")),
            search_blocked=bool(cached_prospeo_health.get("search_blocked")),
            error_code=cached_prospeo_health.get("error_code"),
            banner_message=cached_prospeo_health.get("banner_message"),
            detail=cached_prospeo_health.get("detail"),
        )
        _cached_health_extra = {
            k: cached_prospeo_health.get(k)
            for k in ("rate_limited_until",)
            if cached_prospeo_health.get(k)
        }
    else:
        prospeo_health = fetch_prospeo_account_health()
        _cached_health_extra = {}
    global_search_block: dict[str, Any] | None = None
    if effective_prospeo_search_blocked(
        error_code=prospeo_health.error_code,
        remaining_credits=prospeo_health.remaining_credits,
        insufficient_credits=prospeo_health.insufficient_credits,
        rate_limited=prospeo_health.rate_limited,
        search_blocked=prospeo_health.search_blocked,
    ):
        from app.services.lead_sourcing.prospeo_api_health import stamp_prospeo_rate_limit

        blocked = sanitize_prospeo_health_dict(
            {**prospeo_health.to_dict(), **_cached_health_extra}
        )
        if prospeo_health.rate_limited or "RATE_LIMIT" in str(
            prospeo_health.error_code or ""
        ).upper():
            blocked = stamp_prospeo_rate_limit(blocked)
        global_search_block = blocked
        if log_fn:
            log_fn(
                blocked.get("banner_message")
                or prospeo_health.banner_message
                or prospeo_health.detail
                or "Prospeo no pudo ejecutar búsquedas"
            )

    enrich_deadline = time.monotonic() + PROSPEO_ENRICH_MAX_SEC
    batch_cap = max_companies_per_run if max_companies_per_run is not None else _batch_size()
    offset = max(0, company_offset)

    # Floor de empresa: con industria ICP pedida, exigir relevancia estricta (70).
    # Sin industria (rol-first / soft), mantener 55 para no vaciar el pool.
    from app.services import campaign_icp as campaign_icp_mod
    from app.services.lead_sourcing.company_relevance import (
        MIN_COMPANY_RELEVANCE,
        MIN_COMPANY_RELEVANCE_STRICT,
    )

    industry_set = not campaign_icp_mod.is_icp_token_empty(
        getattr(campaign, "target_industry", None)
    )
    company_floor = (
        MIN_COMPANY_RELEVANCE_STRICT
        if industry_set
        else min(int(fit_threshold), MIN_COMPANY_RELEVANCE)
    )
    all_targets = [
        c
        for c in companies
        if c.result_kind == "company" and (c.icp_relevance_score or 0) >= company_floor
    ]
    # Priorizar dominios ASCII corporativos: LinkedIn/IDN queman cuota y dan 400.
    all_targets.sort(
        key=lambda c: (
            0 if is_prospeo_searchable_domain(c.company_domain) else 1,
            -(c.icp_relevance_score or 0),
        )
    )
    total_eligible = len(all_targets)
    batch_rows = all_targets[offset : offset + batch_cap]

    domain_stats: dict[str, Any] = {"skipped": 0, "resolved": 0, "unresolved": 0}
    if batch_rows and not global_search_block:
        batch_ids = {c.external_id for c in batch_rows}
        to_resolve = [c for c in companies if c.external_id in batch_ids]
        resolved_subset, domain_stats = resolve_corporate_domains_for_companies(
            to_resolve,
            campaign,
            fit_threshold=fit_threshold,
            max_resolve=min(len(batch_rows), DOMAIN_RESOLVE_MAX_PER_ENRICH),
            per_company_sec=DOMAIN_RESOLVE_PER_COMPANY_SEC,
            total_deadline=enrich_deadline,
            fast_mode=True,
            log_fn=log_fn,
        )
        by_id = {c.external_id: c for c in resolved_subset}
        companies = [
            refresh_domain_trust_on_company(by_id.get(c.external_id, c))
            if c.external_id in batch_ids
            else c
            for c in companies
        ]
        batch_rows = [
            refresh_domain_trust_on_company(by_id.get(c.external_id, c))
            if c.external_id in by_id
            else c
            for c in batch_rows
        ]

    deadline = enrich_deadline
    _health_for_stats = (
        global_search_block
        if isinstance(global_search_block, dict)
        else sanitize_prospeo_health_dict(
            {**prospeo_health.to_dict(), **_cached_health_extra}
        )
    )
    stats: dict[str, Any] = {
        "companies_enriched": 0,
        "companies_searched": 0,
        "people_discovered": 0,
        "people_enriched": 0,
        "people_discarded": 0,
        "companies_without_contacts": 0,
        "companies_skipped_no_domain": 0,
        "errors": [],
        "contact_validation_debug": [],
        "prospeo_search_debug": [],
        "prospeo_health": _health_for_stats,
        "domain_resolution": domain_stats,
        "enrich_progress": _build_enrich_progress(
            cursor=offset, total=total_eligible, last_batch=0
        ),
    }

    company_rows = batch_rows
    stats["companies_skipped_no_domain"] = int(domain_stats.get("unresolved") or 0)

    enriched_companies = {c.external_id: c for c in companies}
    people_by_key = {p.external_id: p for p in people_clean}
    global_person_keys: set[str] = set()
    debug_log: list[dict[str, Any]] = stats["contact_validation_debug"]
    prospeo_debug: list[dict[str, Any]] = stats["prospeo_search_debug"]
    excl_em: set[str] = set()
    excl_li: set[str] = set()
    excl_phones: set[str] = set()
    if db is not None:
        try:
            from app.services.nexus_contact_cache import (
                merge_exclusion_sets,
                tenant_delivered_exclusion_sets,
            )
            from app.services.prospect_ingestion import company_contact_exclusion_sets

            excl_em, excl_li, excl_phones = company_contact_exclusion_sets(
                db, int(campaign.company_id)
            )
            excl_em, excl_li, excl_phones = merge_exclusion_sets(
                (excl_em, excl_li, excl_phones),
                tenant_delivered_exclusion_sets(db, int(campaign.company_id)),
            )
        except Exception:  # noqa: BLE001
            pass

    def _record_discard(v, *, company_name: str, stage: str = "filtro_enrich") -> None:
        stats["people_discarded"] += 1
        entry = v.to_debug_dict()
        entry["company_target"] = company_name
        entry["stage"] = stage
        debug_log.append(entry)
        if log_fn:
            log_fn(
                f"Descartado {v.person_name or '?'} @ {company_name}: {v.reason} "
                f"(objetivo={v.target_domain or '—'}, detectado={v.detected_company or '—'}, "
                f"email=@{v.email_domain or '—'})"
            )

    batch_processed = 0

    def _finish_company_batch() -> None:
        nonlocal batch_processed
        batch_processed += 1
        stats["enrich_progress"] = _build_enrich_progress(
            cursor=offset + batch_processed,
            total=total_eligible,
            last_batch=batch_processed,
        )
        if on_checkpoint:
            on_checkpoint(
                list(enriched_companies.values()),
                list(people_by_key.values()),
                dict(stats),
            )

    for company in company_rows:
        if time.monotonic() > deadline:
            stats["partial"] = True
            break
        # Si hay gente del pipeline ya ligada a esta empresa, priorizar otras (cupo).
        already_have = sum(
            1
            for p in people_by_key.values()
            if (p.company_name or "").strip().lower() == (company.name or "").strip().lower()
            and (p.linkedin_url or p.email)
        )
        if already_have >= 1:
            # Ya hay candidatos para esta empresa; buscar otras para completar cupo.
            stats.setdefault("companies_skipped_already_have", 0)
            stats["companies_skipped_already_have"] += 1
            _finish_company_batch()
            continue
        # En rate limit: menos tiempo por empresa (solo web+enrich).
        per_co = (
            min(12.0, PROSPEO_ENRICH_PER_COMPANY_SEC)
            if global_search_block
            else PROSPEO_ENRICH_PER_COMPANY_SEC
        )
        company_deadline = min(deadline, time.monotonic() + per_co)
        try:
            domain = (company.company_domain or "").strip().lower()
            if not is_prospeo_searchable_domain(domain):
                if not domain or is_directory_host(domain):
                    reason = "Sin dominio corporativo"
                else:
                    reason = "Dominio no usable en Prospeo (LinkedIn/IDN)"
                prospeo_debug.append(
                    {
                        "company_name": company.name,
                        "domain": domain or None,
                        "domain_sent": domain or "",
                        "request_executed": False,
                        "prospeo_results": 0,
                        "valid_results": 0,
                        "discarded_count": 0,
                        "discard_reason": reason,
                        "status_message": f"Prospeo omitido — {reason.lower()}",
                    }
                )
                stats["companies_without_contacts"] += 1
                stats["companies_skipped_no_domain"] = (
                    int(stats.get("companies_skipped_no_domain") or 0) + 1
                )
                continue

            stats["companies_searched"] += 1
            updated = company
            skip_firmo = bool(global_search_block) or skip_company_firmographics
            if not skip_firmo and time.monotonic() < company_deadline:
                try:
                    firmo = enrich_company_domain(domain=domain, company_name=company.name)
                    updated = _apply_company_enrichment(company, firmo)
                    updated = updated.model_copy(
                        update={
                            "company_domain": domain,
                            "domain_source": company.domain_source or updated.domain_source,
                        }
                    )
                    enriched_companies[company.external_id] = updated
                    stats["companies_enriched"] += 1
                except ProviderAPIError as e:
                    err_msg = str(e)[:200]
                    stats["errors"].append(f"{company.name}: company {err_msg}"[:200])
                    updated = company
                    prospeo_health = merge_health_from_api_error(
                        prospeo_health,
                        error_code=e.error_code,
                        message=err_msg,
                    )
                    stats["prospeo_health"] = sanitize_prospeo_health_dict(
                        prospeo_health.to_dict()
                    )
                    if e.error_code == "INSUFFICIENT_CREDITS" or "insufficient credit" in err_msg.lower():
                        global_search_block = stats["prospeo_health"]
            else:
                updated = company.model_copy(update={"company_domain": domain})
                enriched_companies[company.external_id] = updated

            # Gate post-firmographics: solo descartar mismatch conocido grave.
            # Desconocido / parcial = "casi perfecto" → sigue para poder llenar cupo.
            from app.services.lead_sourcing.icp_import_gate import (
                ICP_TIER_REJECT,
                assess_icp_identity,
            )

            company_tier, _ident, company_icp_reject = assess_icp_identity(
                campaign_industry=campaign.target_industry,
                campaign_country=campaign.target_country,
                campaign_company_size=campaign.target_company_size,
                prospect_industry=updated.industry,
                prospect_country=updated.country,
                company_size=updated.company_size,
                employee_count=updated.employee_count,
            )
            if company_tier == ICP_TIER_REJECT:
                stats["people_discarded"] = int(stats.get("people_discarded") or 0) + 1
                stats.setdefault("companies_rejected_icp", 0)
                stats["companies_rejected_icp"] = int(stats["companies_rejected_icp"]) + 1
                if log_fn:
                    log_fn(f"{company.name}: descartada — {company_icp_reject}")
                prospeo_debug.append(
                    {
                        "company_name": company.name,
                        "domain": domain,
                        "request_executed": False,
                        "discard_reason": company_icp_reject,
                        "status_message": f"ICP estricto: {company_icp_reject}",
                    }
                )
                stats["companies_without_contacts"] += 1
                continue

            found_for_company = 0
            company_diag: dict[str, Any] = {
                "company_name": company.name,
                "domain": domain,
                "domain_sent": domain,
                "request_executed": False,
                "prospeo_results": 0,
                "valid_results": 0,
                "discarded_count": 0,
                "discard_reason": "",
                "status_message": "",
                "search_blocked": False,
                "search_outcome": None,
                "error_code": None,
            }

            if db is not None:
                try:
                    from app.services.nexus_contact_cache import find_cached_contacts_for_company

                    cache_leads, cache_diag = find_cached_contacts_for_company(
                        db,
                        campaign,
                        company_domain=domain,
                        company_name=company.name,
                        limit=_people_per_company(),
                        exclude_emails=excl_em,
                        exclude_linkedin=excl_li,
                        exclude_phones=excl_phones,
                    )
                except Exception:  # noqa: BLE001
                    cache_leads, cache_diag = [], {}
                if cache_leads:
                    for lead in cache_leads:
                        gkey = (
                            normalize_linkedin_url(lead.linkedin_url)
                            or (lead.email or "").strip().lower()
                            or lead.external_id
                        )
                        if gkey in global_person_keys:
                            continue
                        global_person_keys.add(gkey)
                        lead = lead.model_copy(
                            update={"linked_company_key": company.canonical_key or company.external_id}
                        )
                        people_by_key[lead.external_id] = lead
                        stats["people_discovered"] += 1
                        found_for_company += 1
                    company_diag.update(
                        {
                            "valid_results": found_for_company,
                            "status_message": f"Nexus cache ({found_for_company})",
                            "nexus_cache": cache_diag,
                        }
                    )
                    stats["nexus_cache_contacts"] = int(
                        stats.get("nexus_cache_contacts") or 0
                    ) + found_for_company
                    _upsert_prospeo_debug(prospeo_debug, company_diag)
                    if found_for_company >= 1:
                        if log_fn:
                            log_fn(
                                f"{company.name}: {found_for_company} contacto(s) "
                                "desde cache Nexus (sin Prospeo search)"
                            )
                        _finish_company_batch()
                        continue

            if global_search_block and effective_prospeo_search_blocked(
                error_code=global_search_block.get("error_code"),
                remaining_credits=global_search_block.get("remaining_credits"),
                insufficient_credits=bool(global_search_block.get("insufficient_credits")),
                rate_limited=bool(global_search_block.get("rate_limited")),
                search_blocked=bool(global_search_block.get("search_blocked")),
            ):
                # search-person bloqueado: Brave + enrich-person (otro endpoint) sigue vivo.
                rate_blocked = bool(global_search_block.get("rate_limited")) or (
                    "RATE_LIMIT" in str(global_search_block.get("error_code") or "").upper()
                )
                if rate_blocked and time.monotonic() < company_deadline:
                    from app.services.lead_sourcing.web_executive_fallback import (
                        find_executives_via_web_enrich,
                    )

                    search_hits, fb_diag = find_executives_via_web_enrich(
                        company=updated,
                        role_hint=campaign.target_role,
                        campaign_country=campaign.target_country,
                        limit=_people_per_company(),
                    )
                    company_diag = {**company_diag, **fb_diag}
                    enrich_discards: list[dict[str, Any]] = []
                    for idx, hit in enumerate(search_hits):
                        if time.monotonic() > company_deadline:
                            stats["partial"] = True
                            break
                        lead = _lead_from_prospeo_person(
                            person=hit,
                            company=updated,
                            campaign_id=campaign.id,
                            fit_score=max(
                                updated.icp_relevance_score or 0,
                                company.icp_relevance_score or 0,
                            ),
                            fit_threshold=fit_threshold,
                            idx=idx,
                            icp_target_role=campaign.target_role,
                            icp_target_industry=campaign.target_industry,
                            icp_target_country=campaign.target_country,
                            icp_target_company_size=campaign.target_company_size,
                        )
                        if lead is None:
                            continue
                        gkey = (
                            normalize_linkedin_url(lead.linkedin_url)
                            or (lead.email or "").strip().lower()
                            or lead.external_id
                        )
                        if gkey in global_person_keys:
                            continue
                        global_person_keys.add(gkey)
                        people_by_key[lead.external_id] = lead
                        stats["people_discovered"] += 1
                        found_for_company += 1
                    company_diag["valid_results"] = found_for_company
                    _upsert_prospeo_debug(prospeo_debug, company_diag)
                    if found_for_company == 0:
                        stats["companies_without_contacts"] += 1
                    elif log_fn:
                        log_fn(
                            f"{company.name}: {found_for_company} via web+enrich "
                            "(Prospeo search en rate limit)"
                        )
                else:
                    blocked_outcome = search_outcome_from_health(global_search_block)
                    company_diag.update(
                        {
                            "request_executed": False,
                            "search_blocked": True,
                            "search_outcome": blocked_outcome,
                            "error_code": global_search_block.get("error_code"),
                            "api_error": global_search_block.get("detail"),
                            "status_message": outcome_status_message(
                                blocked_outcome,
                                error_code=global_search_block.get("error_code"),
                            ),
                            "discard_reason": global_search_block.get("detail")
                            or global_search_block.get("banner_message"),
                        }
                    )
                    prospeo_debug.append(company_diag)
                    stats["companies_without_contacts"] += 1
            else:
                try:
                    search_hits, search_diag = search_people_at_company_with_diagnostic(
                        domain=domain,
                        company_name=company.name,
                        role_hint=campaign.target_role,
                        limit=_people_per_company(),
                    )
                    if PROSPEO_SEARCH_THROTTLE_SEC > 0:
                        time.sleep(PROSPEO_SEARCH_THROTTLE_SEC)
                    company_diag = {**company_diag, **search_diag}
                    enrich_discards: list[dict[str, Any]] = list(
                        company_diag.get("person_discards") or []
                    )
                    if is_search_blocked_outcome(company_diag.get("search_outcome")):
                        ec = company_diag.get("error_code")
                        rate_hit = company_diag.get("search_outcome") == (
                            "blocked_rate_limit"
                        ) or (
                            "RATE_LIMIT" in str(ec or "").upper()
                        )
                        prospeo_health = merge_health_from_api_error(
                            prospeo_health,
                            error_code=company_diag.get("error_code"),
                            message=company_diag.get("api_error") or "",
                        )
                        if rate_hit:
                            from app.services.lead_sourcing.prospeo_api_health import (
                                stamp_prospeo_rate_limit,
                            )
                            from app.services.lead_sourcing.web_executive_fallback import (
                                find_executives_via_web_enrich,
                            )

                            global_search_block = stamp_prospeo_rate_limit(
                                sanitize_prospeo_health_dict(prospeo_health.to_dict()),
                                error_code=str(ec or "RATE_LIMITED"),
                                detail=company_diag.get("api_error")
                                or company_diag.get("discard_reason"),
                            )
                            stats["prospeo_health"] = global_search_block
                            # Misma empresa: no gastar search-person; usar web+enrich.
                            search_hits, fb_diag = find_executives_via_web_enrich(
                                company=updated,
                                role_hint=campaign.target_role,
                                campaign_country=campaign.target_country,
                                limit=_people_per_company(),
                            )
                            company_diag = {**company_diag, **fb_diag, "rate_limited_fallback": True}
                            for idx, hit in enumerate(search_hits):
                                if time.monotonic() > company_deadline:
                                    stats["partial"] = True
                                    break
                                lead = _lead_from_prospeo_person(
                                    person=hit,
                                    company=updated,
                                    campaign_id=campaign.id,
                                    fit_score=max(
                                        updated.icp_relevance_score or 0,
                                        company.icp_relevance_score or 0,
                                    ),
                                    fit_threshold=fit_threshold,
                                    idx=idx,
                                    icp_target_role=campaign.target_role,
                                    icp_target_industry=campaign.target_industry,
                                    icp_target_country=campaign.target_country,
                                    icp_target_company_size=campaign.target_company_size,
                                )
                                if lead is None:
                                    continue
                                gkey = (
                                    normalize_linkedin_url(lead.linkedin_url)
                                    or (lead.email or "").strip().lower()
                                    or lead.external_id
                                )
                                if gkey in global_person_keys:
                                    continue
                                global_person_keys.add(gkey)
                                people_by_key[lead.external_id] = lead
                                stats["people_discovered"] += 1
                                found_for_company += 1
                            company_diag["valid_results"] = found_for_company
                            _upsert_prospeo_debug(prospeo_debug, company_diag)
                            if found_for_company == 0:
                                stats["companies_without_contacts"] += 1
                        else:
                            stats["prospeo_health"] = sanitize_prospeo_health_dict(
                                prospeo_health.to_dict()
                            )
                            prospeo_debug.append(company_diag)
                            stats["companies_without_contacts"] += 1
                    else:
                        for idx, hit in enumerate(search_hits):
                            if time.monotonic() > company_deadline:
                                stats["partial"] = True
                                break
                            if not skip_person_detail_enrich and time.monotonic() < company_deadline:
                                person_id = hit.get("person_id") or hit.get("id")
                                try:
                                    if person_id:
                                        detail = enrich_person_by_id(str(person_id))
                                    else:
                                        detail = enrich_person_record(
                                            first_name=hit.get("first_name"),
                                            last_name=hit.get("last_name"),
                                            full_name=hit.get("full_name") or hit.get("name"),
                                            company_name=company.name,
                                            company_website=updated.website_url,
                                            linkedin_url=hit.get("linkedin_url"),
                                        )
                                    if detail:
                                        hit = {**hit, **detail}
                                except ProviderAPIError:
                                    pass

                            email_pre, _ = extract_email_phone(hit)
                            validation = validate_prospeo_contact(
                                target_company_name=company.name,
                                target_domain=domain,
                                person=hit,
                                email=email_pre,
                                person_name=_person_display_name(hit),
                            )
                            if not validation.ok or is_forbidden_email(email_pre):
                                _record_discard(
                                    validation,
                                    company_name=company.name,
                                    stage="filtro_enrich",
                                )
                                enrich_discards.append(
                                    {
                                        "person_name": validation.person_name
                                        or _person_display_name(hit),
                                        "reason": validation.reason,
                                        "stage": "filtro_enrich",
                                        "email_domain": validation.email_domain,
                                    }
                                )
                                continue

                            lead = _lead_from_prospeo_person(
                                person=hit,
                                company=updated,
                                campaign_id=campaign.id,
                                fit_score=max(
                                    updated.icp_relevance_score or 0,
                                    company.icp_relevance_score or 0,
                                ),
                                fit_threshold=fit_threshold,
                                idx=idx,
                                icp_target_role=campaign.target_role,
                                icp_target_industry=campaign.target_industry,
                                icp_target_country=campaign.target_country,
                                icp_target_company_size=campaign.target_company_size,
                            )
                            if lead is None:
                                enrich_discards.append(
                                    {
                                        "person_name": _person_display_name(hit),
                                        "reason": "No pasó validación de persona",
                                        "stage": "lead_build",
                                        "email_domain": email_domain(email_pre),
                                    }
                                )
                                continue
                            gkey = _person_global_key(
                                lead.email, lead.linkedin_url, lead.name
                            )
                            if gkey in global_person_keys:
                                dup = replace(
                                    validation,
                                    ok=False,
                                    reason="Persona ya asignada a otra empresa del pipeline",
                                )
                                _record_discard(
                                    dup, company_name=company.name, stage="duplicate"
                                )
                                enrich_discards.append(
                                    {
                                        "person_name": lead.name,
                                        "reason": dup.reason,
                                        "stage": "duplicate",
                                        "email_domain": email_domain(lead.email),
                                    }
                                )
                                continue
                            global_person_keys.add(gkey)
                            people_by_key[lead.external_id] = lead
                            stats["people_discovered"] += 1
                            found_for_company += 1

                        company_diag["person_discards"] = enrich_discards
                        company_diag = _sync_prospeo_company_diag(
                            company_diag,
                            search_hits=search_hits,
                            found_valid=found_for_company,
                            enrich_discards=enrich_discards,
                        )
                        _upsert_prospeo_debug(prospeo_debug, company_diag)

                        if (
                            found_for_company == 0
                            and not (campaign.target_role or "").strip()
                            and company_has_verified_domain(updated)
                            and not is_search_blocked_outcome(
                                company_diag.get("search_outcome")
                            )
                            and company_diag.get("search_outcome")
                            in (SEARCH_OUTCOME_NO_RESULTS, None, SEARCH_OUTCOME_OK)
                        ):
                            stats.setdefault("generic_fallback_added", 0)
                            for glead in build_generic_email_leads(
                                company=updated,
                                domain=domain,
                                campaign=campaign,
                                fit_score=max(
                                    updated.icp_relevance_score or 0,
                                    company.icp_relevance_score or 0,
                                ),
                                fit_threshold=fit_threshold,
                            ):
                                people_by_key[glead.external_id] = glead
                                stats["generic_fallback_added"] += 1
                            if log_fn:
                                log_fn(
                                    f"{company.name}: Prospeo 0 — fallback genéricos @{domain}"
                                )
                        elif found_for_company == 0:
                            stats["companies_without_contacts"] += 1
                            if log_fn:
                                log_fn(
                                    f"Sin contacto Prospeo para {company.name} ({domain}): "
                                    f"{company_diag.get('status_message', '')}"
                                )
                except ProviderAPIError as e:
                    err_msg = str(e)[:200]
                    stats["errors"].append(f"{company.name}: search {err_msg}")
                    blocked_outcome = search_outcome_from_health(
                        {
                            "error_code": e.error_code,
                            "detail": err_msg,
                            "search_blocked": True,
                        }
                    )
                    company_diag["api_error"] = err_msg
                    company_diag["error_code"] = e.error_code
                    company_diag["search_outcome"] = blocked_outcome
                    company_diag["search_blocked"] = True
                    company_diag["status_message"] = outcome_status_message(
                        blocked_outcome, error_code=e.error_code
                    )
                    company_diag["discard_reason"] = err_msg
                    prospeo_debug.append(company_diag)
                    if effective_prospeo_search_blocked(error_code=e.error_code):
                        global_search_block = stats["prospeo_health"]
                    prospeo_health = merge_health_from_api_error(
                        prospeo_health, error_code=e.error_code, message=err_msg
                    )
                    stats["prospeo_health"] = sanitize_prospeo_health_dict(
                        prospeo_health.to_dict()
                    )
                    stats["companies_without_contacts"] += 1
        finally:
            _finish_company_batch()

    merged: list[LeadCandidateRead] = []
    merged_ids: set[str] = set()
    # Emails genéricos: siempre persistir (el loop de empresas puede agotar el deadline).
    for p in people_by_key.values():
        if not is_pipeline_generic_contact(p):
            continue
        merged.append(p)
        merged_ids.add(p.external_id)

    has_more = bool(stats.get("enrich_progress", {}).get("has_more"))
    if not skip_person_detail_enrich and not has_more:
        for p in people_by_key.values():
            if p.external_id in merged_ids:
                continue
            if time.monotonic() > deadline:
                stats["partial"] = True
                break
            if not is_pipeline_contact(p):
                continue
            score = p.compatibility_score or 0
            if score >= fit_threshold and not (p.email or "").strip() and (p.linkedin_url or p.name):
                try:
                    p = prospeo.enrich_contact(p)
                    stats["people_enriched"] += 1
                except ProviderAPIError as e:
                    if log_fn:
                        log_fn(f"Prospeo omitido {p.name}: {e}")
            if not is_pipeline_contact(p):
                stats["people_discarded"] += 1
                continue
            merged.append(p)
            merged_ids.add(p.external_id)
    else:
        for p in people_by_key.values():
            if p.external_id in merged_ids:
                continue
            if not is_pipeline_contact(p):
                continue
            merged.append(p)
            merged_ids.add(p.external_id)
    stats["people_purged"] = len(people_by_key) - len(merged)
    merged = refresh_prospeo_people_scores(
        merged,
        list(enriched_companies.values()),
        fit_threshold=fit_threshold,
        icp_target_role=campaign.target_role,
        icp_target_industry=campaign.target_industry,
        icp_target_country=campaign.target_country,
        icp_target_company_size=campaign.target_company_size,
    )

    stats["metrics"] = compute_mvp_metrics(
        list(enriched_companies.values()), merged, fit_threshold=fit_threshold
    ).model_dump()
    stats["prospeo_health"] = sanitize_prospeo_health_dict(
        stats.get("prospeo_health") if isinstance(stats.get("prospeo_health"), dict) else None
    )
    if "enrich_progress" not in stats:
        stats["enrich_progress"] = _build_enrich_progress(
            cursor=offset + batch_processed,
            total=total_eligible,
            last_batch=batch_processed,
        )
    return list(enriched_companies.values()), merged, stats
