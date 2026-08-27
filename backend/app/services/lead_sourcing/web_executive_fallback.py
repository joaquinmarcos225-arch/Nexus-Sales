"""Fallback cuando Prospeo search-person está en rate limit.

Brave encuentra perfiles /in/ del rol ICP; enrich-person (otro endpoint) completa
email/teléfono. No usa search-person.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.icp_import_gate import MIN_ROLE_MATCH_FOR_IMPORT
from app.services.lead_sourcing.linkedin_identity import (
    is_personal_linkedin_url,
    normalize_linkedin_url,
)
from app.services.lead_sourcing.providers.prospeo_mvp import enrich_person_record
from app.services.lead_sourcing.providers.web_search_backends import search_web
from app.services.lead_sourcing.role_alignment import (
    best_icp_role_match,
    person_role_from_hit,
    prospeo_role_title_includes,
)

_logger = logging.getLogger(__name__)

_ROLE_QUERY_ALIASES: tuple[str, ...] = (
    "CEO",
    "Fundador",
    "Founder",
    "Director General",
    "Gerente General",
)


def _country_hint(company: CompanyCandidateRead, campaign_country: str | None) -> str | None:
    raw = (company.country or campaign_country or "").strip()
    if not raw:
        return None
    # Brave country codes are 2-letter; our ICP uses labels — backends map some.
    return raw[:40]


def _role_query_bits(role_hint: str | None) -> list[str]:
    titles = prospeo_role_title_includes(role_hint)[:4]
    bits = [t for t in titles if t]
    for alias in _ROLE_QUERY_ALIASES:
        if alias not in bits:
            bits.append(alias)
    return bits[:5]


def _build_queries(company: CompanyCandidateRead, role_hint: str | None) -> list[str]:
    name = (company.name or "").strip()
    domain = (company.company_domain or "").strip()
    roles = _role_query_bits(role_hint)
    role_or = " OR ".join(f'"{r}"' if " " in r else r for r in roles[:4])
    qs: list[str] = []
    if name:
        qs.append(f'site:linkedin.com/in "{name}" ({role_or})')
    if domain:
        qs.append(f'site:linkedin.com/in "{domain}" ({role_or})')
    if name and roles:
        qs.append(f'{roles[0]} "{name}" site:linkedin.com/in')
    # Cap Brave: 1 query/empresa (antes hasta 3).
    return qs[:1]


def _hit_tuple(row: Any) -> tuple[str, str, str]:
    if isinstance(row, tuple) and len(row) >= 2:
        return str(row[0] or ""), str(row[1] or ""), str(row[2] if len(row) > 2 else "")
    if isinstance(row, dict):
        return (
            str(row.get("url") or row.get("link") or ""),
            str(row.get("title") or ""),
            str(row.get("description") or row.get("snippet") or ""),
        )
    return "", "", ""


_STOP_TOKENS = frozenset(
    {
        "inmobiliaria",
        "inmobiliario",
        "inmobiliarias",
        "consultoria",
        "consultoría",
        "mexico",
        "méxico",
        "colombia",
        "argentina",
        "chile",
        "peru",
        "perú",
        "group",
        "grupo",
        "company",
        "compania",
        "compañia",
        "real",
        "estate",
        "property",
        "properties",
        "the",
        "and",
        "para",
        "con",
        "del",
        "los",
        "las",
        "una",
        "ceo",
        "founder",
        "fundador",
        "director",
        "general",
        "gerente",
    }
)


def _distinctive_company_tokens(company_name: str | None, domain: str | None) -> list[str]:
    raw = f"{company_name or ''} {domain or ''}"
    parts = re.split(r"[^\wáéíóúñü]+", raw.lower(), flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        t = (p or "").strip().lower()
        if len(t) < 5 or t in _STOP_TOKENS:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    if domain:
        stem = domain.split(".")[0].strip().lower()
        if len(stem) >= 5 and stem not in _STOP_TOKENS and stem not in seen:
            out.append(stem)
    return out[:8]


def _blob_matches_company(
    blob: str,
    *,
    company_name: str | None,
    domain: str | None,
) -> bool:
    text = (blob or "").lower()
    if not text:
        return False
    tokens = _distinctive_company_tokens(company_name, domain)
    if not tokens:
        # Sin token distintivo: exigir dominio completo o nombre casi exacto.
        name = (company_name or "").strip().lower()
        if domain and domain.lower() in text:
            return True
        return bool(name) and name in text
    hits = sum(1 for t in tokens if t in text)
    return hits >= 1


def find_executives_via_web_enrich(
    *,
    company: CompanyCandidateRead,
    role_hint: str | None,
    campaign_country: str | None = None,
    limit: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Devuelve (personas enriquecidas estilo Prospeo, diagnóstico).
    Cada persona es un dict usable por _lead_from_prospeo_person.
    """
    diag: dict[str, Any] = {
        "company_name": company.name,
        "domain": company.company_domain,
        "fallback": "web_linkedin_enrich",
        "request_executed": False,
        "queries": [],
        "linkedin_candidates": 0,
        "enriched": 0,
        "prospeo_results": 0,
        "valid_results": 0,
        "search_outcome": None,
        "discard_reason": "",
        "status_message": "",
    }
    people: list[dict[str, Any]] = []
    seen_li: set[str] = set()
    country = _country_hint(company, campaign_country)
    enrich_attempts = 0

    for q in _build_queries(company, role_hint):
        if len(people) >= limit:
            break
        diag["queries"].append(q)
        try:
            rows = search_web(q, limit=8, country=country)
        except Exception as e:  # noqa: BLE001
            _logger.info("web executive search failed company=%s: %s", company.name, e)
            continue
        diag["request_executed"] = True
        for row in rows:
            if len(people) >= limit:
                break
            url, title, snip = _hit_tuple(row)
            if "linkedin.com/in/" not in url.lower():
                continue
            li = normalize_linkedin_url(url)
            if not li or not is_personal_linkedin_url(li):
                continue
            if li.lower() in seen_li:
                continue
            seen_li.add(li.lower())
            diag["linkedin_candidates"] += 1
            blob_pre = f"{title} {snip} {url}".lower()
            if not _blob_matches_company(
                blob_pre,
                company_name=company.name,
                domain=company.company_domain,
            ):
                continue
            detail: dict[str, Any] = {}
            # Máx. 1 enrich-person por empresa (créditos Prospeo).
            if enrich_attempts < 1:
                enrich_attempts += 1
                try:
                    detail = enrich_person_record(
                        first_name=None,
                        last_name=None,
                        full_name=None,
                        company_name=company.name,
                        company_website=company.website_url
                        or (
                            f"https://{company.company_domain}"
                            if company.company_domain
                            else None
                        ),
                        linkedin_url=li,
                        job_title=(role_hint or "").strip() or None,
                    )
                except Exception as e:  # noqa: BLE001
                    _logger.info("enrich-person fallback failed li=%s: %s", li, e)
                    detail = {}
            if not detail:
                # Al menos anclar LinkedIn del título Brave (sin email).
                name_guess = re.split(r"\s[-|–—]\s", title, maxsplit=1)[0].strip()
                if not name_guess or len(name_guess) < 3:
                    continue
                # Evitar páginas que parecen empresa en /in/
                if (company.name or "").strip().lower() in name_guess.lower():
                    continue
                detail = {
                    "full_name": name_guess[:120],
                    "linkedin_url": li,
                    "current_job_title": (snip or title)[:120],
                    "headline": snip[:200] if snip else None,
                }
            else:
                detail = {**detail, "linkedin_url": detail.get("linkedin_url") or li}
                diag["enriched"] += 1

            role = person_role_from_hit(detail) or title
            enrich_company = str(
                detail.get("company_name")
                or detail.get("organization_name")
                or ""
            )
            blob_post = f"{title} {snip} {role} {enrich_company}".lower()
            if not _blob_matches_company(
                blob_post,
                company_name=company.name,
                domain=company.company_domain,
            ):
                continue
            if (role_hint or "").strip():
                score, _ = best_icp_role_match(role_hint, role)
                if score < MIN_ROLE_MATCH_FOR_IMPORT:
                    # Título Brave a veces alcanza (Fundador / CEO en title).
                    if not any(
                        tok in blob_post
                        for tok in (
                            "ceo",
                            "founder",
                            "fundador",
                            "director general",
                            "gerente general",
                            "owner",
                            "propietario",
                        )
                    ):
                        continue
            people.append(detail)

    diag["prospeo_results"] = len(people)
    diag["valid_results"] = len(people)
    if people:
        diag["search_outcome"] = "ok"
        diag["status_message"] = f"fallback web+enrich: {len(people)} contacto(s)"
    elif diag["request_executed"]:
        diag["search_outcome"] = "no_results"
        diag["status_message"] = "fallback web+enrich: 0"
        diag["discard_reason"] = "Sin perfiles LinkedIn enriquecibles"
    else:
        diag["search_outcome"] = "no_results"
        diag["status_message"] = "fallback web no ejecutado"
    return people[:limit], diag
