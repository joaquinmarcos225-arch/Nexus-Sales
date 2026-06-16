"""ICP estructurado para company sourcing — más allá del keyword literal."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.campaign import Campaign
from app.services.lead_sourcing.icp_mapper import _clean

_DEFAULT_NEGATIVES = (
    "university",
    "college",
    "recruiter",
    "recruiting",
    "recruitment",
    "staffing",
    "headhunter",
    "agency",
    "agencies",
    "consultant",
    "consulting",
    "consultancy",
    "freelance marketplace",
    "job board",
    "jobs board",
    "career fair",
    "accelerator program",
    "online course",
    "bootcamp",
    "nonprofit",
    "ngo",
    "government",
    "ministry",
    "school district",
)


@dataclass
class CompanyIcpProfile:
    industry: str
    country: str | None = None
    company_size: str | None = None
    company_stage: str | None = None
    buyer_persona: str | None = None
    company_type: str | None = None
    positive_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)

    def all_negatives(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for w in list(_DEFAULT_NEGATIVES) + self.negative_keywords:
            k = w.lower().strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def primary_target_phrase(self) -> str:
        """Ej: mid market B2B SaaS companies USA"""
        parts: list[str] = []
        if self.company_stage:
            parts.append(self.company_stage)
        if self.industry:
            parts.append(self.industry)
        parts.append(self.company_type or "companies")
        if self.country:
            parts.append(self.country)
        return " ".join(parts).strip()

    def secondary_phrases(self) -> list[str]:
        """Frases adicionales orientadas a empresa objetivo (no keyword suelta)."""
        phrases: list[str] = []
        ind = self.industry
        loc = self.country or ""
        stage = self.company_stage or ""

        if ind and loc:
            phrases.append(f"{ind} software companies {loc}".strip())
        if stage and ind:
            phrases.append(f"{stage} {ind} startups {loc}".strip())
        if self.buyer_persona and ind:
            phrases.append(f"{ind} companies for {self.buyer_persona} {loc}".strip())

        if ind and "saas" in ind.lower():
            phrases.append(f"sales automation SaaS companies {loc}".strip())
            phrases.append(f"B2B software startups {loc}".strip())

        seen: set[str] = set()
        out: list[str] = []
        for p in phrases:
            k = p.lower().strip()
            if k and k not in seen and len(k) > 8:
                seen.add(k)
                out.append(p.strip())
        return out[:3]


def _infer_stage(size: str | None) -> str | None:
    if not size:
        return None
    s = size.lower()
    if any(w in s for w in ("startup", "start-up", "early", "seed", "1-10", "1-50", "micro")):
        return "startup"
    if any(w in s for w in ("mid", "mediana", "51-100", "51-200", "100-500")):
        return "mid market"
    if any(w in s for w in ("enterprise", "large", "1000", "grande", "corporate")):
        return "enterprise"
    return None


def _infer_company_type(industry: str | None, size: str | None) -> str:
    ind = (industry or "").lower()
    if "saas" in ind or "software" in ind or "tech" in ind:
        return "software companies"
    if _infer_stage(size) == "startup":
        return "startups"
    return "companies"


def _positive_keywords_from_campaign(
    industry: str | None,
    size: str | None,
    country: str | None,
    role: str | None,
    stage: str | None,
) -> list[str]:
    words: list[str] = []
    if industry:
        words.append(industry)
        words.extend(t for t in re.split(r"[\s,/\-]+", industry) if len(t) > 2)
    if stage:
        words.append(stage)
    if size:
        words.append(size)
    if country:
        words.append(country)
    if role:
        words.extend(t for t in re.split(r"[\s,/\-]+", role) if len(t) > 2)

    extras = ("b2b", "software", "platform", "cloud", "technology")
    blob = " ".join(words).lower()
    for e in extras:
        if e in blob or (industry and e in industry.lower()):
            words.append(e)

    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        k = w.lower().strip()
        if k and k not in seen and len(k) > 2:
            seen.add(k)
            out.append(k)
    return out


def _negative_keywords_from_icp(industry: str | None, role: str | None) -> list[str]:
    """Keywords negativos según ICP (ej. buscamos SaaS product, no agencies)."""
    neg = ["blog", "news", "magazine", "media outlet"]
    ind = (industry or "").lower()
    if "saas" in ind or "software" in ind or "b2b" in ind:
        neg.extend(
            [
                "digital agency",
                "marketing agency",
                "web design agency",
                "staffing firm",
                "executive search",
                "venture capital",
                "vc firm",
                "private equity",
            ]
        )
    if role and "ceo" in role.lower():
        neg.extend(["recruiting firm", "talent acquisition"])
    return neg


def parse_company_icp(campaign: Campaign) -> CompanyIcpProfile:
    industry = _clean(campaign.target_industry) or ""
    country = _clean(campaign.target_country)
    size = _clean(campaign.target_company_size)
    role = _clean(campaign.target_role)
    stage = _infer_stage(size)
    company_type = _infer_company_type(industry, size)

    positive = _positive_keywords_from_campaign(industry, size, country, role, stage)
    negative = _negative_keywords_from_icp(industry, role)

    ai = getattr(campaign, "icp_ai_last_analysis", None)
    if isinstance(ai, dict):
        notes = " ".join(
            str(ai.get(k) or "")
            for k in ("icp_quality", "recommendations", "notes", "icp_scope")
        ).lower()
        if "estrecho" in notes or "niche" in notes:
            positive.append("niche")
        if "amplio" in notes or "broad" in notes:
            pass

    return CompanyIcpProfile(
        industry=industry or "B2B software",
        country=country,
        company_size=size,
        company_stage=stage,
        buyer_persona=role,
        company_type=company_type,
        positive_keywords=positive,
        negative_keywords=negative,
    )
