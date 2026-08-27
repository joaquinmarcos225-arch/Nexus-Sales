"""Términos de búsqueda por industria ICP (más específicos que el label de ventas)."""

from __future__ import annotations

import re


_INDUSTRY_SEARCH_MAP: dict[str, list[str]] = {
    "saas": [
        "B2B SaaS",
        "sales software",
        "revenue platform",
        "cloud software startup",
    ],
    "sales enablement": [
        "sales enablement SaaS",
        "sales engagement platform",
        "outbound sales software",
    ],
    "revops": [
        "RevOps software",
        "revenue operations platform",
        "CRM automation",
    ],
    "hr tech": [
        "HR tech SaaS",
        "human resources software",
        "talent management platform",
    ],
    "martech": [
        "marketing technology SaaS",
        "MarTech platform",
        "marketing automation software",
    ],
    "fintech": [
        "fintech startup",
        "payments platform",
        "financial technology",
    ],
    "pagos": [
        "payments fintech",
        "payment processing startup",
    ],
    "insurtech": [
        "insurtech startup",
        "insurance technology platform",
    ],
    "e-commerce": [
        "ecommerce startup",
        "D2C brand",
        "online retail platform",
    ],
    "logística": [
        "logistics software",
        "supply chain startup",
        "last mile delivery",
    ],
    "supply chain": [
        "supply chain software",
        "logistics technology",
    ],
    "salud": [
        "healthtech startup",
        "digital health platform",
    ],
    "healthtech": [
        "healthtech SaaS",
        "healthcare software",
    ],
    "edtech": [
        "EdTech startup",
        "education technology platform",
    ],
    "educación": [
        "EdTech company",
        "online learning platform",
    ],
    "retail": [
        "retail technology",
        "retail software startup",
    ],
    "manufactura": [
        "manufacturing software",
        "industrial technology startup",
    ],
    "consultoría": [
        "B2B software consultancy product",
    ],
}


def _is_empty_industry(value: str | None) -> bool:
    if not value or not value.strip():
        return True
    return value.strip().lower() in {"no importante", "no_importante", "any"}


def industry_search_terms(industry: str | None) -> list[str]:
    """Devuelve 1–4 frases para queries web, priorizando el label ICP de ventas."""
    if _is_empty_industry(industry):
        return ["B2B software companies"]

    label = industry.strip()
    low = label.lower()
    out: list[str] = [label]

    for key, extras in _INDUSTRY_SEARCH_MAP.items():
        if key in low:
            for term in extras:
                if term.lower() not in {t.lower() for t in out}:
                    out.append(term)
            break

    if len(out) == 1:
        tokens = [t for t in re.split(r"[\s,/\-—]+", label) if len(t) > 2]
        if tokens:
            out.append(f"{' '.join(tokens[:3])} software")
        out.append(f"{label} companies")

    seen: set[str] = set()
    deduped: list[str] = []
    for term in out:
        k = term.lower().strip()
        if k and k not in seen:
            seen.add(k)
            deduped.append(term.strip())
    return deduped[:4]
