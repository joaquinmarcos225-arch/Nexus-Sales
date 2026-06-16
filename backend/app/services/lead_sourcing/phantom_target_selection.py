"""Selección de empresas target para Phantom — marcas reales vs. nombres genéricos SaaS."""

from __future__ import annotations

import re
from typing import Any

from app.services.lead_sourcing.lead_sourcing_company_targeting import (
    GENERIC_COMPANY_WORDS,
    TargetCompany,
    _company_priority,
    _norm,
    _norm_company_key,
    is_generic_company_name,
)

# Frases bloqueadas (substring en nombre normalizado).
BLOCKED_PHANTOM_NAME_PHRASES: tuple[str, ...] = (
    "saas industries",
    "saas startups",
    "saas production",
    "saas browser",
    "saas capital",
    "the saas co",
    "go saas startup kit",
    "m saas solutions",
    "saas industry",
    "saas startup kit",
    "saas template",
    "saas directory",
)

# Segunda palabra tras "SaaS …" que indica categoría, no marca.
SAAS_PREFIX_CATEGORY_WORDS = frozenset(
    {
        "industries",
        "industry",
        "startups",
        "startup",
        "production",
        "browser",
        "capital",
        "solutions",
        "solution",
        "software",
        "services",
        "service",
        "consulting",
        "development",
        "kit",
        "kits",
        "co",
        "company",
        "companies",
        "platform",
        "platforms",
        "tools",
        "tool",
        "hub",
        "cloud",
        "digital",
        "global",
        "world",
        "group",  # solo si es "saas group" genérico — ver heurística
    }
)

# Sufijos que suelen ser marca real aunque el nombre lleve "saas".
BRAND_SUFFIX_ALLOWLIST = frozenset(
    {
        "labs",
        "lab",
        "group",
        "ventures",
        "venture",
        "farmers",
        "careers",
        "plus",
        "io",
        "ai",
        "hq",
    }
)

# Modo test: solo estas empresas si aparecen en el pipeline.
PHANTOM_TEST_PREFERRED_NAMES: tuple[str, ...] = (
    "Compa Careers",
    "Aidetic",
    "Saas Labs",
)

_LAST_PHANTOM_TARGET_AUDIT: list[dict[str, Any]] = []


def get_phantom_target_selection_audit() -> list[dict[str, Any]]:
    return list(_LAST_PHANTOM_TARGET_AUDIT)


def _words(name: str) -> list[str]:
    return [w for w in _norm(name).split() if w]


def is_blocked_phantom_target_name(name: str) -> bool:
    n = _norm(name)
    if not n or len(n) < 2:
        return True
    for phrase in BLOCKED_PHANTOM_NAME_PHRASES:
        if phrase in n:
            return True
    if n in GENERIC_COMPANY_WORDS:
        return True
    if re.match(r"^saas\s+\w+$", n) and _words(name)[-1] in SAAS_PREFIX_CATEGORY_WORDS:
        return True
    if n.startswith("saas ") and _is_saas_category_trap(n):
        return True
    if n.startswith("the saas ") or n.startswith("go saas ") or n.startswith("m saas "):
        return True
    return False


def _is_saas_category_trap(normalized: str) -> bool:
    """«SaaS Industries» / «SaaS Startups» — no «Saas Labs»."""
    if normalized.startswith("saas."):
        return False
    if not normalized.startswith("saas "):
        return False
    parts = normalized.split()
    if len(parts) < 2:
        return True
    tail = parts[1:]
    if len(tail) == 1:
        w = tail[0]
        if w in SAAS_PREFIX_CATEGORY_WORDS:
            return True
        if w in BRAND_SUFFIX_ALLOWLIST:
            return False
    if len(tail) >= 2:
        if tail[0] in SAAS_PREFIX_CATEGORY_WORDS:
            return True
        if tail[-1] in BRAND_SUFFIX_ALLOWLIST and not all(
            x in GENERIC_COMPANY_WORDS | SAAS_PREFIX_CATEGORY_WORDS for x in tail[:-1]
        ):
            return False
    category_hits = sum(1 for w in tail if w in GENERIC_COMPANY_WORDS | SAAS_PREFIX_CATEGORY_WORDS)
    if category_hits >= len(tail):
        return True
    return False


def _distinctive_word_count(name: str) -> int:
    words = _words(name)
    generic = GENERIC_COMPANY_WORDS | SAAS_PREFIX_CATEGORY_WORDS | {"the", "go", "m", "a", "an"}
    return sum(1 for w in words if w not in generic and len(w) > 2)


def score_phantom_target_brand(tc: TargetCompany) -> tuple[int, str, bool]:
    """
    (puntuación, motivo, elegible).
    Mayor = mejor marca; elegible=False excluye de Phantom.
    """
    name = (tc.name or "").strip()
    if not name:
        return -999, "sin nombre", False

    if is_blocked_phantom_target_name(name):
        return -500, "nombre genérico/bloqueado (SaaS categoría)", False

    if is_generic_company_name(name):
        url = (tc.url or "").lower()
        if "linkedin.com/company" not in url and "crunchbase.com" not in url:
            return -400, "nombre genérico sin URL de perfil verificable", False

    score = _company_priority(tc)
    reasons: list[str] = []

    distinctive = _distinctive_word_count(name)
    if distinctive >= 2:
        score += 55
        reasons.append("marca con tokens distintivos")
    elif distinctive == 1:
        score += 35
        reasons.append("marca reconocible")
    else:
        score -= 40
        reasons.append("solo palabras categoría")

    if re.search(r"[.]", name) and not name.lower().startswith("saas "):
        score += 15
        reasons.append("dominio/marca puntada")

    words = _words(name)
    if words and words[0] == "saas" and len(words) >= 2:
        if words[-1] in BRAND_SUFFIX_ALLOWLIST:
            score += 25
            reasons.append("saas + sufijo de marca")
        else:
            score -= 30
            reasons.append("prefijo SaaS sin marca clara")

    if (tc.url or "").lower().find("linkedin.com/company") >= 0:
        score += 20
        reasons.append("LinkedIn company")

    if (tc.source_type or "") == "crunchbase_company":
        score += 15
        reasons.append("Crunchbase org")

    if score < 50:
        return score, "; ".join(reasons) or "puntuación baja", False

    return score, "; ".join(reasons) or "marca apta para Phantom", True


def _matches_preferred_test_name(name: str, preferred: str) -> bool:
    n = _norm_company_key(name)
    p = _norm_company_key(preferred)
    if not n or not p:
        return False
    return n == p or p in n or n in p


def select_phantom_target_companies(
    candidates: list[TargetCompany],
    *,
    test_mode: bool = False,
    max_companies: int = 3,
) -> tuple[list[TargetCompany], list[dict[str, Any]]]:
    global _LAST_PHANTOM_TARGET_AUDIT

    audit: list[dict[str, Any]] = []
    ranked: list[tuple[int, TargetCompany, str]] = []

    for tc in candidates:
        brand_score, reason, eligible = score_phantom_target_brand(tc)
        audit.append(
            {
                "name": tc.name,
                "url": tc.url,
                "source_type": tc.source_type or None,
                "brand_score": brand_score,
                "eligible": eligible,
                "reason": reason,
                "selected_for_phantom": False,
            }
        )
        if eligible:
            ranked.append((brand_score, tc, reason))

    ranked.sort(key=lambda x: -x[0])
    selected: list[TargetCompany] = []
    selection_notes: list[str] = []

    if test_mode:
        for pref in PHANTOM_TEST_PREFERRED_NAMES:
            for score, tc, reason in ranked:
                if _matches_preferred_test_name(tc.name, pref):
                    if not any(_norm_company_key(s.name) == _norm_company_key(tc.name) for s in selected):
                        selected.append(tc)
                        selection_notes.append(f"test whitelist: {pref} ({reason})")
                    break
        if len(selected) < max_companies:
            for score, tc, reason in ranked:
                if len(selected) >= max_companies:
                    break
                if any(_norm_company_key(s.name) == _norm_company_key(tc.name) for s in selected):
                    continue
                selected.append(tc)
                selection_notes.append(f"test fill: {reason}")
    else:
        for score, tc, reason in ranked[:max_companies]:
            selected.append(tc)
            selection_notes.append(reason)

    selected_keys = {_norm_company_key(t.name) for t in selected}
    for row in audit:
        if _norm_company_key(str(row.get("name") or "")) in selected_keys:
            row["selected_for_phantom"] = True
            idx = next(
                (i for i, t in enumerate(selected) if _norm_company_key(t.name) == _norm_company_key(str(row["name"]))),
                -1,
            )
            if idx >= 0:
                row["selection_note"] = selection_notes[idx] if idx < len(selection_notes) else "elegida"

    _LAST_PHANTOM_TARGET_AUDIT = audit
    return selected, audit
