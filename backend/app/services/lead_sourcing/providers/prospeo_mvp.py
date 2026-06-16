"""Prospeo — búsqueda por empresa y enrich-company (MVP sin Phantom)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.prospeo_api_health import (
    SEARCH_OUTCOME_NO_RESULTS,
    classify_prospeo_error,
)
from app.services.lead_sourcing.prospeo_contact_validation import (
    is_directory_host,
    is_forbidden_email,
    validate_prospeo_contact,
)
from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
from app.services.lead_sourcing.timeouts_config import PROSPEO_HTTP_TIMEOUT

_logger = logging.getLogger(__name__)

_PROSPEO_SEARCH_PERSON = "https://api.prospeo.io/search-person"
_PROSPEO_ENRICH_COMPANY = "https://api.prospeo.io/enrich-company"
_PROSPEO_ENRICH_PERSON = "https://api.prospeo.io/enrich-person"


@dataclass
class ProspeoHttpResult:
    ok: bool
    payload: dict[str, Any]
    status_code: int
    error_code: str | None = None
    error_message: str | None = None
    raw_text: str = ""


def _api_key() -> str:
    key = getenv("PROSPEO_API_KEY")
    if not key:
        raise ProviderNotConfiguredError("Prospeo no configurado. Definí PROSPEO_API_KEY.")
    return key


def _post_json_result(url: str, body: dict) -> ProspeoHttpResult:
    headers = {"X-KEY": _api_key(), "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=PROSPEO_HTTP_TIMEOUT) as client:
            resp = client.post(url, headers=headers, json=body)
    except httpx.RequestError as e:
        raise ProviderAPIError(f"Prospeo: {e}", provider="prospeo") from e

    raw = resp.text or ""
    try:
        payload = resp.json() if raw else {}
    except Exception:
        payload = {"_raw_text": raw[:4000]}

    if not isinstance(payload, dict):
        payload = {}

    error_code = str(payload.get("error_code") or "").strip() or None
    api_error = payload.get("error") is True

    if resp.status_code == 401:
        raise ProviderAPIError(
            "Prospeo: API key inválida (401).",
            provider="prospeo",
            status_code=401,
            error_code=error_code or "INVALID_API_KEY",
        )

    if resp.status_code >= 400 or api_error:
        msg = raw[:300] or error_code or f"HTTP {resp.status_code}"
        if error_code in ("NO_MATCH", "INVALID_DATAPOINTS", "NO_RESULTS"):
            return ProspeoHttpResult(
                ok=True,
                payload={},
                status_code=resp.status_code,
                error_code=error_code,
                raw_text=raw,
            )
        raise ProviderAPIError(
            f"Prospeo {resp.status_code}: {msg}",
            provider="prospeo",
            status_code=resp.status_code,
            error_code=error_code,
        )

    return ProspeoHttpResult(
        ok=True,
        payload=payload,
        status_code=resp.status_code,
        raw_text=raw,
    )


def _post_json(url: str, body: dict) -> dict[str, Any]:
    return _post_json_result(url, body).payload


def _normalize_search_row(row: dict[str, Any]) -> dict[str, Any]:
    person = row.get("person") if isinstance(row.get("person"), dict) else row
    if not isinstance(person, dict):
        return {}
    out = dict(person)
    if row.get("person_id") and not out.get("person_id"):
        out["person_id"] = row.get("person_id")
    if row.get("id") and not out.get("person_id"):
        out["person_id"] = row.get("id")
    job = out.get("current_job")
    if isinstance(job, dict):
        out.setdefault("current_job_title", job.get("title") or job.get("job_title"))
        out.setdefault("job_title", job.get("title"))
    linkedin = (
        out.get("linkedin_url")
        or out.get("linkedin")
        or out.get("person_linkedin_url")
        or row.get("linkedin_url")
    )
    if linkedin:
        out["linkedin_url"] = str(linkedin).strip()
    return out


def _extract_search_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "persons", "data", "items"):
        raw = payload.get(key)
        if isinstance(raw, list):
            if not raw:
                return []
            return [_normalize_search_row(r) for r in raw if isinstance(r, dict) and _normalize_search_row(r)]
    return []


def _search_response_preview(payload: dict[str, Any], *, max_len: int = 500) -> str:
    """Resumen del body Prospeo para debug."""
    import json

    if not payload:
        return ""
    preview: dict[str, Any] = {
        "error": payload.get("error"),
        "error_code": payload.get("error_code"),
        "free": payload.get("free"),
    }
    results = payload.get("results")
    if isinstance(results, list):
        preview["results_count"] = len(results)
        if results and isinstance(results[0], dict):
            row = results[0]
            person = row.get("person") if isinstance(row.get("person"), dict) else row
            if isinstance(person, dict):
                preview["sample_person_keys"] = list(person.keys())[:12]
    pag = payload.get("pagination")
    if isinstance(pag, dict):
        preview["pagination"] = pag
    try:
        text = json.dumps(preview, ensure_ascii=False)
    except Exception:
        text = str(preview)
    return text[:max_len]


def _search_person_raw(
    *,
    filters: dict[str, Any],
    page: int = 1,
) -> tuple[list[dict[str, Any]], str | None, str | None, int | None, str]:
    """Devuelve (hits, error_message, error_code, status_code, response_preview)."""
    try:
        result = _post_json_result(_PROSPEO_SEARCH_PERSON, {"page": page, "filters": filters})
    except ProviderAPIError as e:
        return [], str(e)[:200], e.error_code, e.status_code, ""

    payload = result.payload
    preview = _search_response_preview(payload)
    status = result.status_code

    if payload.get("error") is True:
        code = str(payload.get("error_code") or "").strip() or None
        return [], None, code, status, preview

    if 200 <= status < 300 and payload.get("error") is False:
        hits = _extract_search_results(payload)
        if not hits:
            code = str(payload.get("error_code") or "").strip() or "NO_RESULTS"
            _logger.debug(
                "Prospeo search-person 0 results filters=%s pagination=%s",
                list(filters.keys()),
                payload.get("pagination"),
            )
            return [], None, code, status, preview
        return hits, None, None, status, preview

    if result.error_code in ("NO_RESULTS", "NO_MATCH", "INVALID_DATAPOINTS"):
        return [], None, result.error_code, status, preview

    hits = _extract_search_results(payload)
    if not hits and 200 <= status < 300:
        return [], None, "NO_RESULTS", status, preview

    if not hits:
        _logger.debug(
            "Prospeo search-person empty keys=%s filter_keys=%s status=%s",
            list(payload.keys())[:12],
            list(filters.keys()),
            status,
        )
    return hits, None, result.error_code, status, preview


def _filter_summary(filters: dict[str, Any]) -> str:
    company = filters.get("company") if isinstance(filters.get("company"), dict) else {}
    sites = company.get("websites") if isinstance(company.get("websites"), dict) else {}
    inc = sites.get("include") if isinstance(sites.get("include"), list) else []
    dom = inc[0] if inc else "?"
    parts = [f"domain={dom}"]
    if company.get("names"):
        parts.append("name_filter=1")
    if filters.get("person_seniority"):
        parts.append("seniority=1")
    if filters.get("person_job_title"):
        parts.append("title=1")
    return " ".join(parts)


def enrich_company_domain(*, domain: str, company_name: str | None = None) -> dict[str, Any]:
    domain = (domain or "").strip().lower().removeprefix("www.")
    if not domain:
        return {}
    data: dict[str, Any] = {"company_website": domain}
    if company_name:
        data["company_name"] = company_name.strip()
    payload = _post_json(_PROSPEO_ENRICH_COMPANY, {"data": data})
    return payload.get("company") if isinstance(payload.get("company"), dict) else {}


def _person_display(person: dict[str, Any]) -> str:
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return (person.get("full_name") or person.get("name") or "?").strip()


def search_people_at_company_with_diagnostic(
    *,
    domain: str,
    company_name: str | None = None,
    role_hint: str | None = None,
    limit: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Búsqueda Prospeo + diagnóstico completo (requests, descartes, etapas)."""
    from app.services.lead_sourcing.prospeo_api_health import (
        SEARCH_OUTCOME_NO_RESULTS,
        SEARCH_OUTCOME_OK,
        classify_prospeo_error,
        is_http_success_error_code,
        is_search_blocked_outcome,
        outcome_discard_reason,
        outcome_status_message,
    )
    from app.services.lead_sourcing.prospeo_contact_validation import email_domain
    from app.services.lead_sourcing.prospeo_search_diagnostic import (
        ProspeoCompanySearchDiagnostic,
        ProspeoPersonDiscard,
        ProspeoSearchRequestLog,
    )

    from app.services.lead_sourcing.role_alignment import icp_role_titles, sort_people_by_icp_role

    target_name = (company_name or "").strip() or "Empresa"
    domain = (domain or "").strip().lower().removeprefix("www.")
    diag = ProspeoCompanySearchDiagnostic(company_name=target_name, domain_sent=domain or "")

    if not domain or is_directory_host(domain):
        diag.status_message = "Dominio inválido o directorio — búsqueda omitida"
        diag.api_error = diag.status_message
        return [], diag.to_dict()

    seen_ids: set[str] = set()
    merged: list[dict[str, Any]] = []
    raw_total = 0
    search_blocked = False

    def _run_request(req_type: str, filters: dict[str, Any]) -> bool:
        nonlocal raw_total, search_blocked
        if search_blocked or len(merged) >= limit * 3:
            return search_blocked
        hits, err, err_code, status_code, response_preview = _search_person_raw(filters=filters)
        outcome, norm_code = classify_prospeo_error(
            error_code=err_code,
            status_code=status_code,
            message=err,
        )
        if err and not err_code:
            outcome, norm_code = classify_prospeo_error(message=err)

        if is_http_success_error_code(norm_code or err_code):
            norm_code = None
            outcome = SEARCH_OUTCOME_OK if hits else SEARCH_OUTCOME_NO_RESULTS

        if not hits and 200 <= (status_code or 0) < 300 and not is_search_blocked_outcome(outcome):
            outcome = SEARCH_OUTCOME_NO_RESULTS
            norm_code = err_code or "NO_RESULTS"

        if is_search_blocked_outcome(outcome):
            search_blocked = True
            diag.search_blocked = True
            diag.search_outcome = outcome
            diag.error_code = norm_code or err_code
            diag.api_error = err or outcome_discard_reason(outcome, error_code=norm_code)
            diag.status_message = outcome_status_message(outcome, error_code=norm_code or err_code)
            diag.requests.append(
                ProspeoSearchRequestLog(
                    request_type=req_type,
                    executed=True,
                    results_count=0,
                    error=err,
                    error_code=norm_code or err_code,
                    status_code=status_code,
                    filter_summary=_filter_summary(filters),
                    search_outcome=outcome,
                )
            )
            diag.request_executed = True
            return True

        raw_total += len(hits)
        if hits:
            req_outcome = SEARCH_OUTCOME_OK
        elif outcome == SEARCH_OUTCOME_NO_RESULTS:
            req_outcome = SEARCH_OUTCOME_NO_RESULTS
        else:
            req_outcome = SEARCH_OUTCOME_OK if 200 <= (status_code or 0) < 300 else outcome

        diag.requests.append(
            ProspeoSearchRequestLog(
                request_type=req_type,
                executed=True,
                results_count=len(hits),
                error=err,
                error_code=norm_code or err_code,
                status_code=status_code,
                filter_summary=_filter_summary(filters),
                search_outcome=req_outcome,
                response_preview=response_preview if req_type == "broad" else None,
            )
        )
        diag.request_executed = True
        for person in hits:
            pid = str(person.get("person_id") or person.get("id") or "").strip()
            dedupe = pid or (
                f"{person.get('linkedin_url')}|{person.get('first_name')}|{person.get('last_name')}"
            )
            if dedupe in seen_ids:
                continue
            seen_ids.add(dedupe)
            merged.append(person)
        return False

    broad_filter: dict[str, Any] = {"company": {"websites": {"include": [domain]}}}
    icp_titles = icp_role_titles(role_hint)

    for title in icp_titles[:4]:
        if search_blocked or len(merged) >= limit * 3:
            break
        role_filter: dict[str, Any] = {
            "company": {"websites": {"include": [domain]}},
            "person_job_title": {"include": [title]},
        }
        if _run_request(f"icp_role:{title[:40]}", role_filter):
            diag.prospeo_results = raw_total
            diag.after_dedupe = len(merged)
            diag.valid_results = 0
            diag.discarded_count = 0
            return [], diag.to_dict()

    if _run_request("broad", broad_filter):
        diag.prospeo_results = raw_total
        diag.after_dedupe = len(merged)
        diag.valid_results = 0
        diag.discarded_count = 0
        return [], diag.to_dict()

    if (
        len(merged) < limit
        and not search_blocked
        and company_name
        and (company_name or "").strip()
    ):
        named_filter: dict[str, Any] = {
            "company": {
                "websites": {"include": [domain]},
                "names": {"include": [(company_name or "").strip()]},
            }
        }
        if _run_request("company_name", named_filter):
            diag.prospeo_results = raw_total
            diag.after_dedupe = len(merged)
            diag.valid_results = 0
            diag.discarded_count = 0
            return [], diag.to_dict()

    if search_blocked:
        diag.prospeo_results = raw_total
        diag.after_dedupe = len(merged)
        diag.valid_results = 0
        diag.discarded_count = 0
        return [], diag.to_dict()

    if raw_total == 0 and diag.request_executed:
        diag.search_outcome = SEARCH_OUTCOME_NO_RESULTS
        diag.error_code = diag.error_code or "NO_RESULTS"

    diag.prospeo_results = raw_total
    diag.after_dedupe = len(merged)

    merged = sort_people_by_icp_role(merged, role_hint)

    validated: list[dict[str, Any]] = []
    for person in merged:
        if len(validated) >= limit:
            break
        email, _ = extract_email_phone(person)
        pname = _person_display(person)
        if is_forbidden_email(email):
            diag.discards.append(
                ProspeoPersonDiscard(
                    person_name=pname,
                    reason=f"Email prohibido @{email_domain(email) or '?'}",
                    stage="filtro_search",
                    email_domain=email_domain(email),
                )
            )
            continue
        check = validate_prospeo_contact(
            target_company_name=target_name,
            target_domain=domain,
            person=person,
            email=email,
            person_name=pname,
        )
        if check.ok:
            validated.append(person)
        else:
            diag.discards.append(
                ProspeoPersonDiscard(
                    person_name=pname or check.person_name,
                    reason=check.reason,
                    stage="filtro_search",
                    email_domain=check.email_domain,
                )
            )

    diag.valid_results = len(validated)
    diag.discarded_count = len(diag.discards)
    if not diag.search_outcome and validated:
        diag.search_outcome = SEARCH_OUTCOME_OK
    return validated[:limit], diag.to_dict()


def search_people_at_company(
    *,
    domain: str,
    company_name: str | None = None,
    role_hint: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    people, _ = search_people_at_company_with_diagnostic(
        domain=domain,
        company_name=company_name,
        role_hint=role_hint,
        limit=limit,
    )
    return people


def enrich_person_by_id(person_id: str) -> dict[str, Any]:
    payload = _post_json(
        _PROSPEO_ENRICH_PERSON,
        {
            "only_verified_email": False,
            "enrich_mobile": True,
            "data": {"person_id": person_id},
        },
    )
    return payload.get("person") if isinstance(payload.get("person"), dict) else {}


def enrich_person_record(
    *,
    first_name: str | None,
    last_name: str | None,
    full_name: str | None,
    company_name: str | None,
    company_website: str | None,
    linkedin_url: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if linkedin_url:
        data["linkedin_url"] = linkedin_url.strip()
    elif first_name and last_name:
        data["first_name"] = first_name
        data["last_name"] = last_name
    elif full_name:
        data["full_name"] = full_name
    else:
        return {}
    if company_name:
        data["company_name"] = company_name
    dom = _website_domain(company_website)
    if dom:
        data["company_website"] = dom
    payload = _post_json(
        _PROSPEO_ENRICH_PERSON,
        {"only_verified_email": False, "enrich_mobile": True, "data": data},
    )
    return payload.get("person") if isinstance(payload.get("person"), dict) else {}


def confidence_from_person(person: dict[str, Any]) -> int:
    email_obj = person.get("email") or {}
    if isinstance(email_obj, dict) and email_obj.get("email"):
        if email_obj.get("revealed") is True or email_obj.get("verification") == "verified":
            return 92
        return 78
    if person.get("linkedin_url"):
        return 62
    if person.get("current_job_title") or person.get("job_title"):
        return 48
    return 35


def extract_email_phone(person: dict[str, Any]) -> tuple[str | None, str | None]:
    email = None
    email_obj = person.get("email") or {}
    if isinstance(email_obj, dict):
        revealed = email_obj.get("email") or email_obj.get("work_email")
        if revealed and "@" in str(revealed):
            email = str(revealed).strip()
    elif isinstance(email_obj, str) and "@" in email_obj:
        email = email_obj.strip()
    if not email and isinstance(person.get("work_email"), str):
        email = person["work_email"].strip()
    phone = None
    mobile_obj = person.get("mobile") or {}
    if isinstance(mobile_obj, dict):
        mob = mobile_obj.get("mobile") or mobile_obj.get("mobile_international")
        if mob:
            phone = str(mob).strip()
    return email, phone
