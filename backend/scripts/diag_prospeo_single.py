"""Diagnóstico Prospeo search-person — una empresa a la vez (sin pipeline).

Uso:
  cd backend
  python scripts/diag_prospeo_single.py aidetic.com "Aidetic"
  python scripts/diag_prospeo_single.py venturefarmers.com "Venture Farmers"
"""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from typing import Any

import httpx

# Allow running from backend/
sys.path.insert(0, ".")

from app.services.lead_sourcing.contact_identity import ICP_TARGET_ROLES
from app.services.lead_sourcing.env_config import getenv, refresh_lead_sourcing_env
from app.services.lead_sourcing.prospeo_contact_validation import (
    is_forbidden_email,
    validate_prospeo_contact,
)
from app.services.lead_sourcing.providers.prospeo_mvp import (
    _extract_search_results,
    _normalize_search_row,
    extract_email_phone,
)
from app.services.lead_sourcing.timeouts_config import PROSPEO_HTTP_TIMEOUT

SEARCH_PERSON = "https://api.prospeo.io/search-person"
ENRICH_COMPANY = "https://api.prospeo.io/enrich-company"
ACCOUNT_INFO = "https://api.prospeo.io/account-information"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k.lower() in ("x-key", "api_key", "authorization"):
                out[k] = "***REDACTED***"
                continue
            out[k] = _sanitize(v)
        return out
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, str):
        if EMAIL_RE.search(obj) and "@" in obj:
            # Keep domain visible, mask local part in free text
            parts = obj.split("@", 1)
            if len(parts) == 2 and "." in parts[1]:
                return f"***@{parts[1]}"
        return obj
    return obj


def _person_label(row: dict[str, Any]) -> str:
    p = _normalize_search_row(row)
    first = (p.get("first_name") or "").strip()
    last = (p.get("last_name") or "").strip()
    if first or last:
        return f"{first} {last}".strip()
    return (p.get("full_name") or p.get("name") or "?").strip()


def _account_snapshot(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    resp = client.get(ACCOUNT_INFO, headers=headers)
    body = resp.json() if resp.text else {}
    info = body.get("response") if isinstance(body, dict) else {}
    return {
        "endpoint": ACCOUNT_INFO,
        "method": "GET",
        "status_code": resp.status_code,
        "remaining_credits": info.get("remaining_credits") if isinstance(info, dict) else None,
        "used_credits": info.get("used_credits") if isinstance(info, dict) else None,
        "current_plan": info.get("current_plan") if isinstance(info, dict) else None,
        "raw": _sanitize(body),
    }


def _call_search(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    req_type: str,
    filters: dict[str, Any],
    page: int = 1,
) -> dict[str, Any]:
    payload = {"page": page, "filters": filters}
    resp = client.post(SEARCH_PERSON, headers=headers, json=payload)
    try:
        body = resp.json() if resp.text else {}
    except Exception:
        body = {"_raw_text": resp.text[:4000]}
    hits = _extract_search_results(body) if isinstance(body, dict) else []
    credit_free = body.get("free") if isinstance(body, dict) else None
    error_code = body.get("error_code") if isinstance(body, dict) else None
    pagination = body.get("pagination") if isinstance(body, dict) else None
    return {
        "request_type": req_type,
        "endpoint": SEARCH_PERSON,
        "method": "POST",
        "headers": _sanitize(headers),
        "payload": payload,
        "filters_applied": deepcopy(filters),
        "status_code": resp.status_code,
        "response_body": _sanitize(body),
        "credit_free_dedup": credit_free,
        "error_code": error_code,
        "pagination": pagination,
        "raw_results_count": len(hits),
        "raw_person_samples": [
            {
                "person_id": h.get("person_id") or h.get("id"),
                "name": _person_label(h),
                "job_title": h.get("current_job_title") or h.get("job_title"),
                "linkedin": bool(h.get("linkedin_url")),
                "email_in_search": extract_email_phone(h)[0],
            }
            for h in hits[:5]
        ],
    }


def _build_filter_sets(domain: str, company_name: str | None) -> list[tuple[str, dict[str, Any]]]:
    domain = domain.strip().lower().removeprefix("www.")
    company_filter: dict[str, Any] = {"websites": {"include": [domain]}}
    with_name = dict(company_filter)
    if company_name:
        with_name["names"] = {"include": [company_name.strip()]}

    sets: list[tuple[str, dict[str, Any]]] = [
        ("broad_domain_only", {"company": dict(company_filter)}),
        ("broad_with_company_name", {"company": dict(with_name)}),
        (
            "seniority_with_name",
            {
                "company": dict(with_name),
                "person_seniority": {
                    "include": [
                        "Founder/Owner",
                        "C-Suite",
                        "Vice President",
                        "Director",
                        "Manager",
                    ]
                },
            },
        ),
    ]
    for role in ICP_TARGET_ROLES[:3]:
        sets.append(
            (
                f"job_title_{role.replace(' ', '_').lower()}",
                {
                    "company": dict(with_name),
                    "person_job_title": {"include": [role]},
                },
            )
        )
    return sets


def diagnose(domain: str, company_name: str | None) -> dict[str, Any]:
    refresh_lead_sourcing_env()
    key = getenv("PROSPEO_API_KEY")
    if not key:
        return {"error": "PROSPEO_API_KEY no configurada"}

    headers = {"X-KEY": key, "Content-Type": "application/json"}
    domain = domain.strip().lower().removeprefix("www.")
    target_name = (company_name or domain).strip()

    report: dict[str, Any] = {
        "company_name": target_name,
        "domain": domain,
        "api_key_present": True,
        "api_key_prefix": key[:6] + "…" if len(key) > 6 else "***",
    }

    with httpx.Client(timeout=PROSPEO_HTTP_TIMEOUT) as client:
        before = _account_snapshot(client, headers)
        report["account_before"] = before

        # enrich-company sanity check (endpoint usado en pipeline)
        ec_payload = {"data": {"company_website": domain, **({"company_name": target_name} if target_name else {})}}
        ec_resp = client.post(ENRICH_COMPANY, headers=headers, json=ec_payload)
        try:
            ec_body = ec_resp.json() if ec_resp.text else {}
        except Exception:
            ec_body = {"_raw_text": ec_resp.text[:2000]}
        report["enrich_company_probe"] = {
            "endpoint": ENRICH_COMPANY,
            "method": "POST",
            "payload": ec_payload,
            "status_code": ec_resp.status_code,
            "response_body": _sanitize(ec_body),
            "company_found": bool(isinstance(ec_body, dict) and ec_body.get("company")),
        }

        requests_log: list[dict[str, Any]] = []
        all_hits: list[dict[str, Any]] = []
        seen: set[str] = set()

        for req_type, filters in _build_filter_sets(domain, company_name):
            entry = _call_search(client, headers, req_type=req_type, filters=filters)
            requests_log.append(entry)
            for row in _extract_search_results(entry.get("response_body") or {}):
                p = _normalize_search_row(row)
                pid = str(p.get("person_id") or p.get("id") or "").strip()
                dedupe = pid or f"{p.get('linkedin_url')}|{p.get('first_name')}|{p.get('last_name')}"
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                all_hits.append(p)

        after = _account_snapshot(client, headers)
        report["account_after"] = after

        rem_before = before.get("remaining_credits")
        rem_after = after.get("remaining_credits")
        if isinstance(rem_before, int) and isinstance(rem_after, int):
            report["credits_consumed_session"] = rem_before - rem_after
        else:
            report["credits_consumed_session"] = None

        discards: list[dict[str, Any]] = []
        validated: list[dict[str, Any]] = []
        for person in all_hits:
            email, _ = extract_email_phone(person)
            pname = _person_label(person)
            if is_forbidden_email(email):
                discards.append(
                    {
                        "person_name": pname,
                        "reason": f"Email prohibido en search: {email}",
                        "stage": "filtro_nexus",
                    }
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
                validated.append(
                    {
                        "person_name": pname,
                        "email": email,
                        "person_id": person.get("person_id") or person.get("id"),
                    }
                )
            else:
                discards.append(
                    {
                        "person_name": pname or check.person_name,
                        "reason": check.reason,
                        "stage": "filtro_nexus",
                        "email_domain": check.email_domain,
                        "detected_company": check.detected_company,
                    }
                )

        report["search_requests"] = requests_log
        report["summary"] = {
            "total_api_requests": len(requests_log),
            "total_raw_results_all_requests": sum(r.get("raw_results_count", 0) for r in requests_log),
            "unique_people_after_dedupe": len(all_hits),
            "validated_after_nexus_filters": len(validated),
            "discarded_after_nexus_filters": len(discards),
            "discard_reasons": discards,
            "validated_people": validated,
        }

        # Veredicto
        raw_total = report["summary"]["total_raw_results_all_requests"]
        if raw_total == 0:
            report["verdict"] = "PROSPEO_DEVUELVE_0 — la API no encontró personas con estos filtros"
        elif len(validated) == 0 and discards:
            report["verdict"] = "PROSPEO_DEVUELVE_DATOS — Nexus los descarta en validación"
        elif len(validated) > 0:
            report["verdict"] = "PROSPEO_OK — hay personas válidas tras filtros Nexus"
        else:
            report["verdict"] = "INDETERMINADO"

    return report


def main() -> None:
    cases = [
        ("aidetic.com", "Aidetic"),
        ("venturefarmers.com", "Venture Farmers"),
    ]
    if len(sys.argv) >= 2:
        dom = sys.argv[1]
        name = sys.argv[2] if len(sys.argv) >= 3 else None
        cases = [(dom, name)]

    for domain, name in cases:
        print("\n" + "=" * 72)
        print(f"DIAGNÓSTICO PROSPEO — {name or domain} ({domain})")
        print("=" * 72)
        result = diagnose(domain, name)
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
