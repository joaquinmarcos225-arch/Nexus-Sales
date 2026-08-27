"""Targeting ICP por empresa real del pipeline (no búsqueda global genérica)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.services.lead_sourcing.company_extraction_policy import (
    display_company_name,
    passes_phantom_company_gate,
)
from app.services.lead_sourcing.env_config import getenv
MIN_COMPANY_FILTER_SCORE = 70

# Solo tokens sueltos de 1 palabra que generan ruido como keyword global (no nombres de empresa multi-palabra).
SINGLE_TOKEN_BLOCKLIST = frozenset(
    {
        "deel",
        "law",
        "saas",
        "software",
        "consulting",
        "recruitment",
        "recruiter",
        "staffing",
    }
)

DEFAULT_PRIMARY_ROLE = "Founder"
DEFAULT_SECONDARY_ROLE = "CEO"

# Orden LinkedIn Search Export — startups: Founder/CEO antes que Head of Sales.
LINKEDIN_SEARCH_ROLES: tuple[str, ...] = (
    "Founder",
    "CEO",
    "Co-Founder",
    "VP Sales",
    "Head of Sales",
    "GTM",
    "Revenue",
)

GENERIC_COMPANY_PHRASES = (
    "saas development",
    "logiciel solutions",
    "software development",
    "software solutions",
    "development solutions",
    "digital solutions",
    "technology solutions",
    "it services",
    "consulting services",
    "business solutions",
)

GENERIC_COMPANY_WORDS = frozenset(
    {
        "saas",
        "software",
        "development",
        "solutions",
        "logiciel",
        "consulting",
        "services",
        "technology",
        "technologies",
        "digital",
        "cloud",
        "platform",
        "systems",
    }
)

CONTAMINANT_SURNAMES = frozenset(
    {
        "deel",
        "law",
        "consulting",
        "solutions",
        "development",
        "logiciel",
    }
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def is_contaminated_person_name(name: str) -> bool:
    parts = [p for p in (name or "").strip().split() if p]
    if len(parts) < 2:
        return False
    last = _norm(parts[-1])
    if last in CONTAMINANT_SURNAMES:
        return True
    if len(parts) == 2 and last in SINGLE_TOKEN_BLOCKLIST:
        return True
    return False


def _norm_company_key(name: str) -> str:
    s = _norm(name)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_generic_company_name(name: str) -> bool:
    """Nombres SEO/genéricos (ej. SaaS Development) — no usar como target Phantom."""
    n = _norm(name)
    if not n:
        return True
    for phrase in GENERIC_COMPANY_PHRASES:
        if phrase in n:
            return True
    words = [w for w in n.split() if w]
    if len(words) <= 3 and words and all(w in GENERIC_COMPANY_WORDS for w in words):
        return True
    if n in GENERIC_COMPANY_WORDS:
        return True
    if n.startswith("saas ") and len(words) >= 2:
        if words[1] in {
            "industries",
            "startups",
            "startup",
            "production",
            "browser",
            "capital",
            "solutions",
        }:
            return True
    return False


def _plain_role_term(term: str) -> str:
    return term.strip().strip('"').strip("'")


def linkedin_role_try_order(campaign: Campaign | None = None) -> list[str]:
    """Roles a probar por empresa (máx. 2 en Phantom), con fallback automático."""
    campaign_roles = _title_terms_from_role(
        campaign.target_role if campaign else None,
        use_startup_defaults=False,
    )
    out: list[str] = []
    seen: set[str] = set()

    def add(role: str) -> None:
        key = _norm(role)
        if not key or key in seen:
            return
        seen.add(key)
        out.append(_plain_role_term(role))

    for r in campaign_roles:
        add(r)
    for r in LINKEDIN_SEARCH_ROLES:
        add(r)
    if not out:
        out = [_plain_role_term(r) for r in LINKEDIN_SEARCH_ROLES[:4]]
    return out


def _title_terms_from_role(
    role: str | None,
    *,
    use_startup_defaults: bool = True,
) -> list[str]:
    if not role or not role.strip():
        if use_startup_defaults:
            return list(LINKEDIN_SEARCH_ROLES[:4])
        return []

    chunks = re.split(r"\s*(?:,|/|;|\bor\b)\s*", role.strip(), flags=re.IGNORECASE)
    terms: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        p = chunk.strip()
        if not p or len(p) < 3:
            continue
        key = _norm(p)
        if key in seen:
            continue
        seen.add(key)
        low = p.lower()
        if any(
            tok in low
            for tok in (
                "head of",
                "director",
                " vp ",
                "vice president",
                "chief",
                "founder",
                "co-founder",
                "cofounder",
                "ceo",
                "cro",
                "sales",
                "revenue",
            )
        ) or low in ("founder", "ceo", "cro"):
            terms.append(_plain_role_term(p))
    if not terms:
        return list(LINKEDIN_SEARCH_ROLES[:4]) if use_startup_defaults else []
    return terms[:6]


def _company_name_usable(name: str, url: str | None = None) -> bool:
    n = (name or "").strip()
    if len(n) < 2:
        return False
    if is_generic_company_name(n):
        return bool(url and ("linkedin.com/company" in url.lower() or "crunchbase.com" in url.lower()))
    parts = n.split()
    if len(parts) == 1 and _norm(n) in SINGLE_TOKEN_BLOCKLIST:
        return bool(url and "linkedin.com/company" in url.lower())
    return True


@dataclass
class TargetCompany:
    name: str
    url: str | None = None
    icp_relevance_score: int = 0
    canonical_key: str = ""
    source_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "icp_relevance_score": self.icp_relevance_score,
            "canonical_key": self.canonical_key,
            "source_type": self.source_type or None,
        }


def _company_priority(tc: TargetCompany) -> int:
    score = tc.icp_relevance_score or 0
    url = (tc.url or "").lower()
    st = (tc.source_type or "").strip()
    if "linkedin.com/company" in url or st == "linkedin_company":
        score += 45
    elif "crunchbase.com" in url or st == "crunchbase_company":
        score += 38
    elif st == "startup_card":
        score += 28
    if is_generic_company_name(tc.name):
        score -= 80
    return score


def _default_max_target_companies() -> int:
    raw = (getenv("LEAD_SOURCING_MAX_TARGET_COMPANIES") or "8").strip()
    try:
        return max(1, min(50, int(raw)))
    except ValueError:
        return 8


def collect_target_companies(
    companies: list[CompanyCandidateRead],
    _legacy_queue: dict | None = None,
    *,
    test_mode: bool = False,
    max_companies: int | None = None,
) -> list[TargetCompany]:
    del test_mode
    out: list[TargetCompany] = []
    seen: set[str] = set()

    def _add_candidate(c: CompanyCandidateRead) -> None:
        if not passes_phantom_company_gate(c):
            return
        name = display_company_name(c)
        if not _company_name_usable(name, c.website_url):
            return
        ck = c.canonical_key or _norm_company_key(name)
        if not ck or ck in seen:
            return
        seen.add(ck)
        score = max(c.confidence or 0, c.icp_relevance_score or 0)
        out.append(
            TargetCompany(
                name=name,
                url=(c.website_url or "").strip() or None,
                icp_relevance_score=score,
                canonical_key=ck,
                source_type=(c.source_type or "").strip(),
            )
        )

    for c in companies:
        _add_candidate(c)

    limit = max_companies if max_companies is not None else _default_max_target_companies()
    ranked = sorted(out, key=lambda tc: -tc.icp_relevance_score)
    return [tc for tc in ranked if not is_generic_company_name(tc.name)][:limit]


def fuzzy_match_target_company(
    lead_company: str | None,
    targets: list[TargetCompany],
) -> tuple[bool, str | None, float, str]:
    """
    Retorna (matched, target_name, ratio 0-1, note).
    """
    lc = _norm_company_key(lead_company or "")
    if not lc or not targets:
        return False, None, 0.0, "sin empresa en lead o sin targets"

    best_name: str | None = None
    best_ratio = 0.0
    for t in targets:
        tk = t.canonical_key or _norm_company_key(t.name)
        if not tk:
            continue
        if lc == tk:
            return True, t.name, 1.0, "match exacto"
        if lc in tk or tk in lc:
            ratio = max(len(lc), len(tk)) / (len(lc) + len(tk) - min(len(lc), len(tk)) + 1)
            ratio = min(1.0, max(0.85, ratio * 0.95))
            if ratio > best_ratio:
                best_ratio = ratio
                best_name = t.name
        r = SequenceMatcher(None, lc, tk).ratio()
        if r > best_ratio:
            best_ratio = r
            best_name = t.name

    if best_ratio >= 0.72 and best_name:
        return True, best_name, best_ratio, "fuzzy match"
    return False, best_name, best_ratio, "empresa no coincide con pipeline"


def _role_matches(lead_role: str | None, campaign_role: str | None) -> bool:
    lr = _norm(lead_role)
    cr = _norm(campaign_role)
    if not lr or not cr:
        return False
    if cr in lr:
        return True
    for token in (
        "founder",
        "co-founder",
        "cofounder",
        "ceo",
        "head of sales",
        "vp sales",
        "chief revenue",
        "cro",
        "gtm",
        "revenue",
        "go-to-market",
    ):
        if token in cr and token in lr:
            return True
    for role in LINKEDIN_SEARCH_ROLES:
        if _norm(role) in cr and _norm(role) in lr:
            return True
    return False


def _saas_industry_signal(lead: LeadCandidateRead, campaign: Campaign) -> bool:
    blob = " ".join(
        [
            lead.industry or "",
            lead.role or "",
            lead.company_name or "",
            campaign.target_industry or "",
        ]
    ).lower()
    return any(k in blob for k in ("saas", "b2b", "software", "cloud", "platform"))


def score_lead_company_targeted(
    lead: LeadCandidateRead,
    campaign: Campaign,
    targets: list[TargetCompany],
) -> tuple[int, str, dict[str, Any]]:
    matched, match_name, ratio, match_note = fuzzy_match_target_company(
        lead.company_name,
        targets,
    )

    pts = 0
    parts: list[str] = [match_note]
    details: dict[str, Any] = {
        "company_matched": matched,
        "matched_icp_company": match_name,
        "company_match_ratio": round(ratio, 3),
        "company_match_note": match_note,
    }

    if matched:
        pts += 40
        parts.append(f"empresa +40 ({match_name})")
    else:
        pts -= 45
        parts.append("empresa no en pipeline -45")

    if _role_matches(lead.role, campaign.target_role):
        pts += 25
        parts.append("rol +25")
        details["role_matched"] = True
    else:
        details["role_matched"] = False
        parts.append("rol 0")

    if _saas_industry_signal(lead, campaign):
        pts += 15
        parts.append("SaaS/B2B/software +15")
        details["industry_signal"] = True
    else:
        details["industry_signal"] = False

    if lead.email:
        pts += 5
        parts.append("email +5")
    if lead.linkedin_url:
        pts += 3
        parts.append("linkedin +3")

    score = max(0, min(100, pts))
    details["total_score"] = score
    return score, "; ".join(parts), details
