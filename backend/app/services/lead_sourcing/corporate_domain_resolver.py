"""Resuelve dominio corporativo real antes de Prospeo (no Crunchbase/Wellfound/LinkedIn)."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from app.services.lead_sourcing.timeouts_config import (
    DOMAIN_RESOLVE_MAX_PER_ENRICH,
    DOMAIN_RESOLVE_PER_COMPANY_SEC,
)

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_name_normalizer import normalize_company_name
from app.services.lead_sourcing.domain_semantic_validation import (
    classify_domain_trust,
    domain_semantically_matches_company,
)
from app.services.lead_sourcing.icp_region import brave_country_for_query, resolve_region_search_context
from app.services.lead_sourcing.prospeo_contact_validation import (
    company_names_match,
    is_directory_host,
    resolve_target_company_domain,
)
from app.services.lead_sourcing.providers.base import ProviderAPIError
from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain
from app.services.lead_sourcing.providers.prospeo_mvp import enrich_company_domain
from app.services.lead_sourcing.providers.web_search_backends import configured_backend, search_web

_logger = logging.getLogger(__name__)

_SEARCH_EXCLUDES = (
    "-site:crunchbase.com -site:wellfound.com -site:linkedin.com "
    "-site:angellist.com -site:angel.co -site:g2.com -site:clutch.co "
    "-site:producthunt.com -site:facebook.com"
)

_NAME_STOPWORDS = frozenset(
    {
        "careers",
        "inc",
        "ltd",
        "llc",
        "corp",
        "co",
        "company",
        "group",
        "go",
        "the",
        "and",
        "saudi",
        "arabia",
    }
)

_RESOLUTION_REJECT_FRAGMENTS: tuple[str, ...] = (
    "wikipedia.org",
    "wikidata.org",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "tiktok.com",
    "reddit.com",
    "medium.com",
    "github.com",
    "apps.apple.com",
    "play.google.com",
    "bloomberg.com",
    "forbes.com",
    "techcrunch.com",
)


@dataclass(frozen=True)
class CorporateDomainResolution:
    domain: str | None
    website_url: str | None
    source: str | None
    message: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.domain and not is_directory_host(self.domain))


def _max_resolve_per_enrich() -> int:
    try:
        return max(1, min(int(os.getenv("DOMAIN_RESOLVE_MAX_PER_ENRICH", str(DOMAIN_RESOLVE_MAX_PER_ENRICH))), 10))
    except ValueError:
        return DOMAIN_RESOLVE_MAX_PER_ENRICH


def _per_domain_timeout_sec() -> float:
    try:
        return max(
            3.0,
            min(float(os.getenv("DOMAIN_RESOLVE_PER_COMPANY_SEC", str(DOMAIN_RESOLVE_PER_COMPANY_SEC))), 15.0),
        )
    except ValueError:
        return DOMAIN_RESOLVE_PER_COMPANY_SEC


def _min_hit_score() -> int:
    try:
        return max(8, min(int(os.getenv("DOMAIN_RESOLVE_MIN_SCORE", "12")), 30))
    except ValueError:
        return 12


def _is_reject_resolution_host(host: str | None) -> bool:
    if not host:
        return True
    if is_directory_host(host):
        return True
    h = host.lower()
    return any(frag in h for frag in _RESOLUTION_REJECT_FRAGMENTS)


def _website_from_domain(domain: str) -> str:
    d = domain.strip().lower().removeprefix("www.")
    return f"https://{d}"


def _lookup_domain_from_nexus_company_cache(company_name: str) -> CorporateDomainResolution | None:
    """Lee dominio ya pagado/resuelto en nexus_company_cache (sesión corta propia)."""
    try:
        from app.database.session import SessionLocal
        from app.services.nexus_contact_cache import find_company_domain_by_name

        db = SessionLocal()
        try:
            hit = find_company_domain_by_name(db, company_name)
        finally:
            db.close()
    except Exception:
        return None
    if not hit:
        return None
    dom, web = hit
    if not dom or is_directory_host(dom) or _is_reject_resolution_host(dom):
        return None
    sem_ok, _ = domain_semantically_matches_company(company_name, dom)
    if not sem_ok:
        return None
    return CorporateDomainResolution(
        dom,
        web or _website_from_domain(dom),
        "nexus_company_cache",
        message="ok",
    )


def _remember_domain_in_nexus_company_cache(
    company_name: str, res: CorporateDomainResolution
) -> None:
    if not res.resolved or not res.domain:
        return
    try:
        from app.database.session import SessionLocal
        from app.services.nexus_contact_cache import remember_company_domain

        db = SessionLocal()
        try:
            remember_company_domain(
                db,
                name=company_name,
                domain=res.domain,
                website_url=res.website_url,
                source_provider=res.source or "domain_resolver",
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        _logger.debug("nexus company domain remember failed", exc_info=True)


def _root_domain_bonus(host: str) -> int:
    """Priorizar dominio raíz (cube.dev) sobre subdominios profundos."""
    parts = host.lower().split(".")
    if len(parts) == 2:
        return 18
    if len(parts) == 3 and parts[0] in ("www", "app"):
        return 14
    if len(parts) >= 3:
        return 4
    return 0


def _name_tokens(company_name: str) -> list[str]:
    norm = normalize_company_name(company_name) or (company_name or "").strip()
    raw = re.sub(r"[^\w\s]", " ", norm.lower())
    return [t for t in raw.split() if len(t) > 2 and t not in _NAME_STOPWORDS]


def _domain_slug_matches_company(domain: str, company_name: str) -> bool:
    slug = (domain or "").split(".")[0].lower().replace("-", "")
    if not slug or len(slug) < 3:
        return False
    tokens = _name_tokens(company_name)
    if slug in tokens:
        return True
    compact = "".join(tokens)
    if not compact:
        return False
    return slug in compact or compact.startswith(slug[: min(6, len(slug))]) or slug.startswith(compact[:6])


def _guess_domain_candidates(company_name: str) -> list[str]:
    tokens = _name_tokens(company_name)
    if not tokens:
        return []
    guesses: list[str] = []
    compact = "".join(tokens)
    if len(compact) >= 3:
        guesses.append(f"{compact}.com")
        for tld in ("io", "dev", "co"):
            guesses.append(f"{compact}.{tld}")
    if len(tokens) >= 2:
        hyphen = "-".join(tokens)
        guesses.append(f"{hyphen}.com")
    if len(tokens) == 1:
        guesses.append(f"{tokens[0]}.com")
        guesses.append(f"{tokens[0]}.dev")
    seen: set[str] = set()
    out: list[str] = []
    for g in guesses:
        h = _website_domain(g)
        if h and h not in seen and not _is_reject_resolution_host(h):
            seen.add(h)
            out.append(h)
    return out


def _build_domain_search_queries(
    company_name: str,
    *,
    region_phrase: str | None = None,
) -> list[str]:
    norm = (normalize_company_name(company_name) or company_name or "").strip()
    if not norm:
        return []
    region_bit = f" {region_phrase}" if region_phrase else ""
    queries = [
        f'"{norm}" official website{region_bit} {_SEARCH_EXCLUDES}',
        f'"{norm}" homepage{region_bit} {_SEARCH_EXCLUDES}',
        f"{norm} company website{region_bit} {_SEARCH_EXCLUDES}",
    ]
    short_tokens = _name_tokens(norm)
    if short_tokens:
        short = " ".join(short_tokens)
        if short.lower() != norm.lower():
            queries.append(f'"{short}" official site {_SEARCH_EXCLUDES}')
        primary = short_tokens[0]
        if len(primary) >= 4:
            queries.append(f"{primary} startup company website {_SEARCH_EXCLUDES}")
    return queries[:5]


def _score_resolution_hit(
    *,
    url: str,
    title: str,
    snippet: str,
    company_name: str,
) -> int:
    host = _website_domain(url)
    if not host or _is_reject_resolution_host(host):
        return -1
    score = 10 + _root_domain_bonus(host)
    blob = f"{title} {snippet} {url}".lower()
    name_l = (normalize_company_name(company_name) or company_name or "").lower()
    if name_l and name_l in blob:
        score += 22
    if company_names_match(company_name, title):
        score += 18
    sem_ok, _ = domain_semantically_matches_company(company_name, host)
    if not sem_ok:
        return -1
    if _domain_slug_matches_company(host, company_name):
        score += 50
    if any(w in blob for w in ("official", "homepage", "inicio", "sitio web", "website", "home")):
        score += 10
    if any(w in blob for w in ("review", "pricing page", "top 10", "directory", "jobs at")):
        score -= 15
    return score


def _pick_best_from_hits(
    hits: list[tuple[str, str, str]],
    company_name: str,
    *,
    min_score: int,
) -> CorporateDomainResolution | None:
    best_url: str | None = None
    best_score = -1
    for url, title, snippet in hits:
        sc = _score_resolution_hit(url=url, title=title, snippet=snippet, company_name=company_name)
        if sc > best_score:
            best_score = sc
            best_url = url
    if not best_url or best_score < min_score:
        return None
    dom = _website_domain(best_url)
    if not dom or _is_reject_resolution_host(dom):
        return None
    return CorporateDomainResolution(dom, _website_from_domain(dom), "web_search", message="ok")


def _resolve_via_web_search(
    company_name: str,
    *,
    region_phrase: str | None = None,
    region_ctx=None,
    deadline: float | None = None,
    max_queries: int = 5,
) -> CorporateDomainResolution:
    if not configured_backend():
        return CorporateDomainResolution(
            None,
            None,
            "unresolved",
            message="Web Search no configurado",
        )

    all_hits: list[tuple[str, str, str]] = []
    seen_urls: set[str] = set()
    min_score = _min_hit_score()

    for i, query in enumerate(
        _build_domain_search_queries(company_name, region_phrase=region_phrase)[:max_queries]
    ):
        if deadline and time.monotonic() > deadline:
            break
        brave_country = brave_country_for_query(region_ctx, i)
        try:
            batch = search_web(query, limit=6, country=brave_country, provider="domain_resolver")
        except ProviderAPIError as e:
            _logger.debug("domain search failed %s: %s", company_name, e)
            continue
        for url, title, snippet in batch:
            key = (url or "").strip().lower()
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            all_hits.append((url, title, snippet))
        # Early-exit: primer hit usable → no seguir quemando Brave.
        picked = _pick_best_from_hits(all_hits, company_name, min_score=min_score)
        if picked:
            return picked

    if all_hits:
        picked_relaxed = _pick_best_from_hits(all_hits, company_name, min_score=max(8, min_score - 4))
        if picked_relaxed:
            return picked_relaxed

    return CorporateDomainResolution(
        None,
        None,
        "unresolved",
        message="Sin resultado web corporativo claro",
    )


def _resolve_via_prospeo(company_name: str, *, domain_hint: str | None = None) -> CorporateDomainResolution:
    try:
        firmo = enrich_company_domain(
            domain=(domain_hint or "").strip().lower().removeprefix("www."),
            company_name=company_name,
        )
    except ProviderAPIError as e:
        return CorporateDomainResolution(None, None, "unresolved", message=f"Prospeo: {e}"[:120])
    dom = resolve_target_company_domain(
        website_url=None,
        company_domain=domain_hint,
        firmo=firmo if firmo else None,
    )
    if not dom:
        return CorporateDomainResolution(None, None, "unresolved", message="Prospeo sin website")
    source = "domain_guess" if domain_hint else "prospeo"
    return CorporateDomainResolution(dom, _website_from_domain(dom), source, message="ok")


def _resolve_via_domain_guesses(
    company_name: str,
    *,
    deadline: float | None = None,
    max_guesses: int = 6,
) -> CorporateDomainResolution:
    from app.services.lead_sourcing.nexus_public_fetch import fetch_company_page_signals

    for guess in _guess_domain_candidates(company_name)[:max_guesses]:
        if deadline and time.monotonic() > deadline:
            break
        if _domain_slug_matches_company(guess, company_name):
            sig = fetch_company_page_signals(guess)
            if sig is not None:
                res = CorporateDomainResolution(
                    guess,
                    sig.url or _website_from_domain(guess),
                    "nexus_fetch",
                    message="ok",
                )
                _remember_domain_in_nexus_company_cache(company_name, res)
                return res
        pros = _resolve_via_prospeo(company_name, domain_hint=guess)
        if pros.resolved:
            return pros
        if _domain_slug_matches_company(guess, company_name):
            return CorporateDomainResolution(guess, _website_from_domain(guess), "domain_guess", message="ok")
    return CorporateDomainResolution(None, None, "unresolved", message="Sin guess válido")


def resolve_corporate_domain_for_company(
    company: CompanyCandidateRead,
    *,
    campaign: Campaign | None = None,
    try_web_search: bool = True,
    try_prospeo: bool = True,
    deadline: float | None = None,
    fast_mode: bool = False,
) -> CorporateDomainResolution:
    """Orden: web propia → búsqueda web → Prospeo → guess (con límite de tiempo)."""
    name = (company.normalized_company_name or company.name or "").strip()
    if not name:
        return CorporateDomainResolution(None, None, "unresolved", message="Sin nombre")

    def _expired() -> bool:
        return deadline is not None and time.monotonic() > deadline

    existing_dom = (company.company_domain or "").strip().lower() or None
    if existing_dom and not is_directory_host(existing_dom):
        web = company.website_url or _website_from_domain(existing_dom)
        if _website_domain(web) and is_directory_host(_website_domain(web)):
            web = _website_from_domain(existing_dom)
        sem_ok, sem_msg = domain_semantically_matches_company(name, existing_dom)
        if not sem_ok:
            return CorporateDomainResolution(
                existing_dom, web, "doubtful", message=sem_msg
            )
        return CorporateDomainResolution(existing_dom, web, "own_website", message="ok")

    web_dom = _website_domain(company.website_url)
    if web_dom and not is_directory_host(web_dom):
        sem_ok, sem_msg = domain_semantically_matches_company(name, web_dom)
        if not sem_ok:
            return CorporateDomainResolution(
                web_dom,
                company.website_url or _website_from_domain(web_dom),
                "doubtful",
                message=sem_msg,
            )
        return CorporateDomainResolution(
            web_dom,
            company.website_url or _website_from_domain(web_dom),
            "own_website",
            message="ok",
        )

    # Reusar dominio ya conocido en base propia Nexus (evita Brave).
    cached = _lookup_domain_from_nexus_company_cache(name)
    if cached is not None:
        return cached

    region_label = (campaign.target_country if campaign else None) or company.country
    region_ctx = resolve_region_search_context(region_label)
    region_phrase = region_ctx.query_phrase if region_ctx else region_label
    max_queries = 3 if fast_mode else 5
    max_guesses = 3 if fast_mode else 4

    if try_web_search and not _expired():
        web = _resolve_via_web_search(
            name,
            region_phrase=region_phrase,
            region_ctx=region_ctx,
            deadline=deadline,
            max_queries=max_queries,
        )
        if web.resolved:
            _remember_domain_in_nexus_company_cache(name, web)
            return web

    if try_prospeo and not _expired():
        pros = _resolve_via_prospeo(name)
        if pros.resolved:
            _remember_domain_in_nexus_company_cache(name, pros)
            return pros
        if not _expired():
            guessed = _resolve_via_domain_guesses(name, deadline=deadline, max_guesses=max_guesses)
            if guessed.resolved:
                _remember_domain_in_nexus_company_cache(name, guessed)
                return guessed

    if _expired():
        return CorporateDomainResolution(None, None, "unresolved", message="Timeout resolución dominio")
    return CorporateDomainResolution(
        None,
        None,
        "unresolved",
        message="No se pudo resolver dominio corporativo",
    )


def apply_corporate_domain_resolution(
    company: CompanyCandidateRead,
    resolution: CorporateDomainResolution,
) -> CompanyCandidateRead:
    name = company.normalized_company_name or company.name or ""
    if not resolution.resolved:
        return company.model_copy(
            update={
                "domain_source": resolution.source or "unresolved",
                "domain_trust": "unresolved",
            }
        )

    trust = classify_domain_trust(name, resolution.domain, source=resolution.source)
    directory_url = company.source_directory_url
    current_web = company.website_url
    if current_web and is_directory_host(_website_domain(current_web)):
        directory_url = directory_url or current_web

    if trust == "doubtful" or resolution.source == "doubtful":
        return company.model_copy(
            update={
                "company_domain": resolution.domain,
                "website_url": resolution.website_url,
                "domain_source": "doubtful",
                "domain_trust": "doubtful",
                "source_directory_url": directory_url,
            }
        )

    return company.model_copy(
        update={
            "company_domain": resolution.domain,
            "website_url": resolution.website_url,
            "domain_source": resolution.source,
            "domain_trust": "verified",
            "source_directory_url": directory_url,
        }
    )


def compute_domain_resolution_metrics(
    companies: list[CompanyCandidateRead],
    *,
    fit_threshold: int | None = None,
) -> dict[str, Any]:
    """Métricas para UI: empresas, dominios resueltos, tasa %."""
    rows = [c for c in companies if c.result_kind == "company"]
    if fit_threshold is not None:
        rows = [c for c in rows if (c.icp_relevance_score or 0) >= fit_threshold]
    found = len(rows)
    verified = sum(1 for c in rows if (c.domain_trust or "") == "verified")
    doubtful = sum(1 for c in rows if (c.domain_trust or "") == "doubtful")
    resolved = verified + doubtful
    rate = int(round(100 * verified / found)) if found else 0
    return {
        "companies_found": found,
        "domains_resolved": verified,
        "domains_doubtful": doubtful,
        "domain_resolution_rate_pct": rate,
    }


def resolve_corporate_domains_for_companies(
    companies: list[CompanyCandidateRead],
    campaign: Campaign,
    *,
    fit_threshold: int = 70,
    max_resolve: int | None = None,
    per_company_sec: float | None = None,
    total_deadline: float | None = None,
    fast_mode: bool = True,
    log_fn=None,
) -> tuple[list[CompanyCandidateRead], dict[str, Any]]:
    """Resuelve dominios (solo en enrich): máx N empresas, timeout por empresa."""
    cap = max_resolve if max_resolve is not None else _max_resolve_per_enrich()
    per_co = per_company_sec if per_company_sec is not None else _per_domain_timeout_sec()
    stats: dict[str, Any] = {
        "attempted": 0,
        "resolved": 0,
        "unresolved": 0,
        "skipped_timeout": 0,
        "resolution_debug": [],
    }
    by_id = {c.external_id: c for c in companies}

    targets = [c for c in companies if c.result_kind == "company"]
    targets.sort(
        key=lambda c: (
            0
            if not (c.company_domain and not is_directory_host(c.company_domain or ""))
            else 1,
            -(c.icp_relevance_score or 0),
        )
    )
    targets = targets[:cap]

    for company in targets:
        if total_deadline and time.monotonic() > total_deadline:
            stats["partial"] = True
            break
        company_deadline = time.monotonic() + per_co
        if total_deadline:
            company_deadline = min(company_deadline, total_deadline)

        stats["attempted"] += 1
        res = resolve_corporate_domain_for_company(
            company,
            campaign=campaign,
            deadline=company_deadline,
            fast_mode=fast_mode,
        )
        updated = apply_corporate_domain_resolution(company, res)
        by_id[company.external_id] = updated

        sem_ok, sem_reason = (
            domain_semantically_matches_company(company.name, updated.company_domain)
            if updated.company_domain
            else (False, res.message)
        )
        entry = {
            "company_name": company.name,
            "target_company": company.name,
            "website": updated.website_url,
            "domain": updated.company_domain,
            "domain_source": updated.domain_source,
            "domain_trust": updated.domain_trust,
            "directory_source": updated.source_directory_url,
            "ok": res.resolved and (updated.domain_trust or "") == "verified",
            "semantic_ok": sem_ok,
            "message": res.message if res.resolved else res.message,
        }
        if (updated.domain_trust or "") == "doubtful":
            entry["message"] = sem_reason
        stats["resolution_debug"].append(entry)

        if res.resolved and (updated.domain_trust or "") == "verified":
            stats["resolved"] += 1
            if log_fn:
                log_fn(
                    f"{company.name}: dominio {res.domain} ({res.source}) "
                    f"web={res.website_url}"
                )
        elif res.resolved and (updated.domain_trust or "") == "doubtful":
            stats.setdefault("domains_doubtful", 0)
            stats["domains_doubtful"] += 1
            if log_fn:
                log_fn(f"{company.name}: dominio dudoso {updated.company_domain} — {sem_reason}")
        else:
            stats["unresolved"] += 1
            if log_fn:
                src = _website_domain(company.website_url) or "sin web"
                log_fn(f"{company.name}: sin dominio corporativo (fuente previa={src})")

    merged = [by_id.get(c.external_id, c) for c in companies]
    metrics = compute_domain_resolution_metrics(merged, fit_threshold=fit_threshold)
    stats.update(metrics)
    return merged, stats


def companies_ready_for_prospeo(
    companies: list[CompanyCandidateRead], *, fit_threshold: int
) -> list[CompanyCandidateRead]:
    return [
        c
        for c in companies
        if c.result_kind == "company"
        and (c.icp_relevance_score or 0) >= fit_threshold
        and c.company_domain
        and not is_directory_host(c.company_domain)
        and (c.domain_trust or "") == "verified"
    ]


def refresh_domain_trust_on_company(company: CompanyCandidateRead) -> CompanyCandidateRead:
    """Re-clasifica dominio existente (p. ej. tras cargar pipeline)."""
    dom = company.company_domain
    if not dom or is_directory_host(dom):
        return company.model_copy(update={"domain_trust": "unresolved"})
    trust = classify_domain_trust(
        company.normalized_company_name or company.name or "",
        dom,
        source=company.domain_source,
    )
    source = company.domain_source
    if trust == "doubtful":
        source = "doubtful"
    return company.model_copy(update={"domain_trust": trust, "domain_source": source})


def company_has_verified_domain(c: CompanyCandidateRead) -> bool:
    return bool(
        c.company_domain
        and not is_directory_host(c.company_domain)
        and (c.domain_trust or "") == "verified"
    )
