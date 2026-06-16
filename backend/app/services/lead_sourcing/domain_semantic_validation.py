"""Validación semántica: dominio resuelto debe corresponder al nombre de empresa."""

from __future__ import annotations

import re

from app.services.lead_sourcing.company_name_normalizer import normalize_company_name
from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

# Dominios de directorios/analytics que suelen aparecer en búsqueda pero no son la empresa.
_HIGH_RISK_MISMATCH_HOSTS: frozenset[str] = frozenset(
    {
        "kickstarter.com",
        "tracxn.com",
        "getlatka.com",
        "crunchbase.com",
        "wellfound.com",
        "linkedin.com",
        "g2.com",
        "clutch.co",
        "pitchbook.com",
        "owler.com",
        "zoominfo.com",
        "apollo.io",
        "lusha.com",
        "rocketreach.co",
    }
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
        "global",
        "saudi",
        "arabia",
    }
)


def _name_tokens(company_name: str) -> list[str]:
    norm = normalize_company_name(company_name) or (company_name or "").strip()
    raw = re.sub(r"[^\w\s]", " ", norm.lower())
    return [t for t in raw.split() if len(t) > 2 and t not in _NAME_STOPWORDS]


def domain_semantically_matches_company(company_name: str, domain: str | None) -> tuple[bool, str]:
    """
    True si el dominio es plausible para la empresa (ej. Kinde ≠ kickstarter.com).
    """
    host = (domain or "").strip().lower().removeprefix("www.")
    if not host:
        return False, "Sin dominio"
    slug = host.split(".")[0].replace("-", "")
    tokens = _name_tokens(company_name)
    if not tokens:
        return False, "Nombre de empresa sin tokens válidos"

    compact = "".join(tokens)
    if not slug or len(slug) < 2:
        return False, "Slug de dominio inválido"

    if host in _HIGH_RISK_MISMATCH_HOSTS:
        return False, f"Dominio de directorio/analytics ({host})"

    if slug == compact:
        return True, "Coincidencia exacta"

    if len(compact) >= 4 and compact in slug:
        return True, f"Nombre en dominio ({compact})"

    if len(slug) >= 4 and slug in compact:
        return True, f"Dominio en nombre ({slug})"

    significant = [t for t in tokens if len(t) >= 4]
    if not significant:
        significant = [t for t in tokens if len(t) >= 3]

    matched = [t for t in significant if t in slug]
    if matched:
        return True, f"Token «{matched[0]}» en dominio"

    for t in significant:
        if len(t) >= 5 and slug.startswith(t):
            if len(slug) <= len(t) + 3:
                return True, f"Dominio empieza con {t}"

    if len(significant) == 1 and len(significant[0]) >= 6:
        t = significant[0]
        overlap = 0
        for i in range(min(len(t), len(slug))):
            if t[i] == slug[i]:
                overlap += 1
            else:
                break
        if overlap >= min(5, len(t) - 1) and len(slug) <= len(t) + 4:
            return True, "Prefijo fuerte"

    return (
        False,
        f"Dominio dudoso: «{slug}» no coincide con «{company_name}» ({compact or '—'})",
    )


def classify_domain_trust(
    company_name: str,
    domain: str | None,
    *,
    source: str | None = None,
) -> str:
    """verified | doubtful | unresolved"""
    if not domain or not _website_domain(domain):
        return "unresolved"
    ok, _ = domain_semantically_matches_company(company_name, domain)
    if ok:
        return "verified"
    if source == "own_website":
        ok2, _ = domain_semantically_matches_company(company_name, _website_domain(domain))
        if ok2:
            return "verified"
    return "doubtful"
