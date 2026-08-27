"""Prospeo — búsqueda por empresa y enrich-company (MVP sin Phantom)."""

from __future__ import annotations

import logging
import time
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
            result = ProspeoHttpResult(
                ok=True,
                payload={},
                status_code=resp.status_code,
                error_code=error_code,
                raw_text=raw,
            )
            _record_prospeo_cogs(url)
            return result
        raise ProviderAPIError(
            f"Prospeo {resp.status_code}: {msg}",
            provider="prospeo",
            status_code=resp.status_code,
            error_code=error_code,
        )

    result = ProspeoHttpResult(
        ok=True,
        payload=payload,
        status_code=resp.status_code,
        raw_text=raw,
    )
    _record_prospeo_cogs(url)
    return result


def _record_prospeo_cogs(url: str) -> None:
    try:
        from app.services.lead_sourcing.cogs_runtime_metrics import (
            record_prospeo_enrich_company,
            record_prospeo_search,
        )

        if "search-person" in url:
            record_prospeo_search(1)
        elif "enrich-company" in url:
            record_prospeo_enrich_company(1)
        # enrich-person se cuenta en enrich_person_* vía record_enrich
    except Exception:  # noqa: BLE001
        pass


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


_RATE_LIMIT_BACKOFF_SEC: tuple[float, ...] = ()  # no reintentar 429: empeora el bloqueo


def _is_rate_limit_error(*, status_code: int | None, error_code: str | None, message: str | None = None) -> bool:
    code = (error_code or "").strip().upper().replace(" ", "_")
    msg = (message or "").lower()
    if status_code == 429:
        return True
    if "RATE_LIMIT" in code:
        return True
    if "rate limit" in msg:
        return True
    return False


def _search_person_raw(
    *,
    filters: dict[str, Any],
    page: int = 1,
) -> tuple[list[dict[str, Any]], str | None, str | None, int | None, str]:
    """Devuelve (hits, error_message, error_code, status_code, response_preview)."""
    last_err: str | None = None
    last_code: str | None = None
    last_status: int | None = None

    for attempt in range(len(_RATE_LIMIT_BACKOFF_SEC) + 1):
        try:
            result = _post_json_result(_PROSPEO_SEARCH_PERSON, {"page": page, "filters": filters})
        except ProviderAPIError as e:
            last_err = str(e)[:200]
            last_code = e.error_code or "RATE_LIMITED"
            last_status = e.status_code
            if _is_rate_limit_error(
                status_code=e.status_code, error_code=e.error_code, message=str(e)
            ) and attempt < len(_RATE_LIMIT_BACKOFF_SEC):
                delay = _RATE_LIMIT_BACKOFF_SEC[attempt]
                _logger.warning(
                    "Prospeo rate limit; reintento %s/%s en %.0fs",
                    attempt + 1,
                    len(_RATE_LIMIT_BACKOFF_SEC),
                    delay,
                )
                time.sleep(delay)
                continue
            return [], last_err, last_code, last_status, ""

        payload = result.payload
        preview = _search_response_preview(payload)
        status = result.status_code

        if payload.get("error") is True:
            code = str(payload.get("error_code") or "").strip() or None
            if _is_rate_limit_error(status_code=status, error_code=code) and attempt < len(
                _RATE_LIMIT_BACKOFF_SEC
            ):
                delay = _RATE_LIMIT_BACKOFF_SEC[attempt]
                _logger.warning(
                    "Prospeo rate limit (body); reintento %s/%s en %.0fs",
                    attempt + 1,
                    len(_RATE_LIMIT_BACKOFF_SEC),
                    delay,
                )
                time.sleep(delay)
                last_err = None
                last_code = "RATE_LIMITED"
                last_status = status
                continue
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

    return [], last_err, last_code or "RATE_LIMITED", last_status or 429, ""


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

    from app.services.lead_sourcing.icp_import_gate import MIN_ROLE_MATCH_FOR_IMPORT
    from app.services.lead_sourcing.role_alignment import (
        best_icp_role_match,
        person_role_from_hit,
        prospeo_role_title_includes,
        sort_people_by_icp_role,
    )

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
    icp_titles = prospeo_role_title_includes(role_hint)[:6]
    role_hint_clean = (role_hint or "").strip()

    # Un solo request con varios títulos (antes: hasta 10 requests = rate limit).
    if icp_titles and not search_blocked:
        role_filter: dict[str, Any] = {
            "company": {"websites": {"include": [domain]}},
            "person_job_title": {"include": icp_titles, "match_mode": "CONTAINS"},
        }
        if _run_request(
            f"icp_roles:{','.join(t[:24] for t in icp_titles[:4])}",
            role_filter,
        ):
            diag.prospeo_results = raw_total
            diag.after_dedupe = len(merged)
            diag.valid_results = 0
            diag.discarded_count = 0
            return [], diag.to_dict()

    # Sin rol ICP: ampliar. Con rol: no rellenar con SEO/DevOps/etc.
    if not role_hint_clean:
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
        if role_hint_clean:
            hit_role = person_role_from_hit(person)
            role_score, _ = best_icp_role_match(role_hint_clean, hit_role)
            if role_score < MIN_ROLE_MATCH_FOR_IMPORT:
                diag.discards.append(
                    ProspeoPersonDiscard(
                        person_name=pname,
                        reason=f"Rol no alinea con ICP ({hit_role or 'sin título'})",
                        stage="filtro_search",
                        email_domain=email_domain(email),
                    )
                )
                continue
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


def enrich_person_by_id(
    person_id: str,
    *,
    require_mobile: bool = False,
    enrich_mobile: bool | None = None,
) -> dict[str, Any]:
    """Enrich-person por id.

    ``enrich_mobile`` default = ``require_mobile``: solo pedimos móvil (10 créditos
    Prospeo) cuando la campaña/canal lo necesita. Pasá ``enrich_mobile=True`` con
    ``require_mobile=False`` para reintentos sin filtro ``only_verified_mobile``.
    """
    do_mobile = bool(require_mobile) if enrich_mobile is None else bool(enrich_mobile)
    body: dict[str, Any] = {
        "only_verified_email": False,
        "enrich_mobile": do_mobile,
        "data": {"person_id": person_id},
    }
    if require_mobile:
        # Solo cobra/devuelve si hay móvil; evita gastar enrich en gente sin celular.
        body["only_verified_mobile"] = True
    try:
        from app.services.lead_sourcing.cogs_runtime_metrics import record_enrich

        record_enrich(enrich_mobile=do_mobile)
    except Exception:  # noqa: BLE001
        pass
    payload = _post_json(_PROSPEO_ENRICH_PERSON, body)
    return payload.get("person") if isinstance(payload.get("person"), dict) else {}


def enrich_person_record(
    *,
    first_name: str | None,
    last_name: str | None,
    full_name: str | None,
    company_name: str | None,
    company_website: str | None,
    linkedin_url: str | None = None,
    job_title: str | None = None,
    email: str | None = None,
    mobile: str | None = None,
    enrich_mobile: bool = False,
    require_mobile: bool = False,
) -> dict[str, Any]:
    """
    Identidades válidas Prospeo (docs):
    - linkedin_url
    - email
    - full_name / first+last + company_*
    Mobile solo no está documentado; se manda como señal extra si hay nombre.

    ``enrich_mobile`` default False: no gastar 10 créditos de móvil salvo que el
    caller lo pida (p. ej. falta teléfono y el plan tiene WhatsApp).
    """
    data: dict[str, Any] = {}
    em = (email or "").strip()
    mob = (mobile or "").strip()
    li = (linkedin_url or "").strip() or None

    if li:
        data["linkedin_url"] = li
    elif em and "@" in em:
        data["email"] = em
    elif first_name and last_name and (
        (company_name or "").strip()
        or (company_website or "").strip()
    ):
        data["first_name"] = first_name
        data["last_name"] = last_name
    elif full_name and (
        (company_name or "").strip()
        or (company_website or "").strip()
    ):
        data["full_name"] = full_name
    elif full_name and mob:
        # Mejor esfuerzo: nombre + móvil (API puede ignorar mobile).
        data["full_name"] = full_name
        data["mobile"] = mob
    elif first_name and last_name and mob:
        data["first_name"] = first_name
        data["last_name"] = last_name
        data["mobile"] = mob
    elif mob:
        # Solo WhatsApp/teléfono: best-effort (API puede rechazar o no resolver).
        data["mobile"] = mob
    else:
        return {}

    if company_name:
        data["company_name"] = company_name
    if job_title and str(job_title).strip():
        data["current_job_title"] = str(job_title).strip()
    if em and "@" in em and "email" not in data:
        data["email"] = em
    if mob and "mobile" not in data:
        data["mobile"] = mob
    # Si el ancla es LinkedIn/email, igual sumamos nombre si hay (mejora match).
    if "full_name" not in data and "first_name" not in data and full_name:
        data["full_name"] = full_name

    dom = _website_domain(company_website)
    if dom:
        data["company_website"] = dom
    do_mobile = bool(enrich_mobile) or bool(require_mobile)
    body: dict[str, Any] = {
        "only_verified_email": False,
        "enrich_mobile": do_mobile,
        "data": data,
    }
    if require_mobile:
        body["only_verified_mobile"] = True
    try:
        from app.services.lead_sourcing.cogs_runtime_metrics import record_enrich

        record_enrich(enrich_mobile=do_mobile)
    except Exception:  # noqa: BLE001
        pass
    payload = _post_json(_PROSPEO_ENRICH_PERSON, body)
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
    from app.services.whatsapp_cloud_service import sanitize_stored_email, sanitize_stored_phone
    from app.services.whatsapp_phone_validation import sanitize_whatsapp_mobile

    email = sanitize_stored_email(email)
    phone = None
    mobile_obj = person.get("mobile") or {}
    if isinstance(mobile_obj, dict):
        mob = mobile_obj.get("mobile") or mobile_obj.get("mobile_international")
        if mob:
            phone = sanitize_whatsapp_mobile(str(mob))
    return email, phone
