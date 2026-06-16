"""Cola de extracción PhantomBuster — semillas Web Search sin crawling directo."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_extraction.extractors import detect_platform
from app.services.lead_sourcing.icp_intelligence import parse_company_icp
from app.services.lead_sourcing.linkedin_phantom_query import sanitize_icp_keywords

# Plataformas que bloquean bots — no crawlear desde backend.
PHANTOM_ONLY_PLATFORMS = frozenset(
    {
        "wellfound",
        "g2",
        "clutch",
        "crunchbase",
        "producthunt",
        "capterra",
        "getapp",
    }
)


@dataclass
class SourceClassification:
    directory_url: str
    platform: str
    name: str
    status: str  # requires_phantombuster | usable_linkedin | company_direct
    message: str = ""
    icp_relevance_score: int = 0


@dataclass
class PhantomQueueItem:
    kind: str  # company | directory_seed | linkedin_company
    name: str
    url: str | None = None
    platform: str = ""
    icp_relevance_score: int = 0
    external_id: str | None = None


@dataclass
class PhantomQueueResult:
    items: list[PhantomQueueItem] = field(default_factory=list)
    source_classifications: list[SourceClassification] = field(default_factory=list)
    icp_target_phrase: str = ""
    role_hint: str = ""
    location: str = ""
    icp_keywords: list[str] = field(default_factory=list)

    def to_meta(self) -> dict:
        companies = [i for i in self.items if i.kind in ("company", "linkedin_company")]
        seeds = [i for i in self.items if i.kind == "directory_seed"]
        blocked = [s for s in self.source_classifications if s.status == "requires_phantombuster"]
        return {
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "icp_target_phrase": self.icp_target_phrase,
            "role_hint": self.role_hint,
            "location": self.location,
            "icp_keywords": self.icp_keywords,
            "company_count": len(companies),
            "directory_seed_count": len(seeds),
            "blocked_count": len(blocked),
            "total_items": len(self.items),
            "items": [
                {
                    "kind": i.kind,
                    "name": i.name,
                    "url": i.url,
                    "platform": i.platform,
                    "icp_relevance_score": i.icp_relevance_score,
                    "external_id": i.external_id,
                }
                for i in self.items
            ],
            "sources": [
                {
                    "directory_url": s.directory_url,
                    "platform": s.platform,
                    "name": s.name,
                    "status": s.status,
                    "message": s.message,
                    "icp_relevance_score": s.icp_relevance_score,
                    "companies_found": 0,
                    "pages_fetched": 0,
                    "avg_icp_score": s.icp_relevance_score,
                    "error": None,
                }
                for s in self.source_classifications
            ],
            "total_extracted": 0,
            "total_after_dedupe": len(companies),
        }


def _platform_from_url(url: str) -> str:
    return detect_platform(url)


def _is_linkedin_company(url: str) -> bool:
    low = url.lower()
    return "linkedin.com" in low and "/company/" in low


class PhantomBusterQueueService:
    """Arma cola PhantomBuster desde empresas + fuentes semilla (sin HTTP)."""

    def prepare_queue(
        self,
        campaign: Campaign,
        companies: list[CompanyCandidateRead],
    ) -> PhantomQueueResult:
        profile = parse_company_icp(campaign)
        result = PhantomQueueResult(
            icp_target_phrase=profile.primary_target_phrase(),
            role_hint=(campaign.target_role or "").strip(),
            location=(campaign.target_country or "").strip(),
            icp_keywords=sanitize_icp_keywords(profile.positive_keywords),
        )
        seen_urls: set[str] = set()
        seen_names: set[str] = set()

        company_rows = [c for c in companies if c.result_kind == "company"]
        directory_rows = [c for c in companies if c.result_kind == "directory_source"]

        for c in sorted(company_rows, key=lambda x: -(x.icp_relevance_score or 0)):
            url = (c.website_url or "").strip()
            key = url.lower() if url else (c.name or "").lower()
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            platform = _platform_from_url(url) if url else "company"
            kind = "linkedin_company" if _is_linkedin_company(url) else "company"
            result.items.append(
                PhantomQueueItem(
                    kind=kind,
                    name=c.name,
                    url=url or None,
                    platform=platform,
                    icp_relevance_score=c.icp_relevance_score or 0,
                    external_id=c.external_id,
                )
            )
            seen_names.add((c.name or "").lower())

        for src in directory_rows:
            url = (src.website_url or "").strip()
            if not url:
                continue
            platform = _platform_from_url(url)
            icp = src.icp_relevance_score or 0

            if platform in PHANTOM_ONLY_PLATFORMS or _is_directory_listing(url):
                result.source_classifications.append(
                    SourceClassification(
                        directory_url=url,
                        platform=platform,
                        name=src.name or platform,
                        status="requires_phantombuster",
                        message="Fuente bloqueada para crawling directo — enviar a PhantomBuster",
                        icp_relevance_score=icp,
                    )
                )
                seed_key = url.lower()
                if seed_key not in seen_urls:
                    seen_urls.add(seed_key)
                    result.items.append(
                        PhantomQueueItem(
                            kind="directory_seed",
                            name=src.name or f"Listado {platform}",
                            url=url,
                            platform=platform,
                            icp_relevance_score=icp,
                            external_id=src.external_id,
                        )
                    )
            elif _is_linkedin_company(url):
                result.source_classifications.append(
                    SourceClassification(
                        directory_url=url,
                        platform=platform,
                        name=src.name or "LinkedIn",
                        status="usable_linkedin",
                        message="URL LinkedIn — usable en PhantomBuster",
                        icp_relevance_score=icp,
                    )
                )
            else:
                result.source_classifications.append(
                    SourceClassification(
                        directory_url=url,
                        platform=platform,
                        name=src.name or platform,
                        status="requires_phantombuster",
                        message="Semilla de directorio — extracción vía PhantomBuster",
                        icp_relevance_score=icp,
                    )
                )
                seed_key = url.lower()
                if seed_key not in seen_urls:
                    seen_urls.add(seed_key)
                    result.items.append(
                        PhantomQueueItem(
                            kind="directory_seed",
                            name=src.name or f"Listado {platform}",
                            url=url,
                            platform=platform,
                            icp_relevance_score=icp,
                            external_id=src.external_id,
                        )
                    )

        return result


def _is_directory_listing(url: str) -> bool:
    """Heurística: URL de listado (no perfil individual)."""
    path = (urlparse(url).path or "").lower()
    profile_markers = ("/company/", "/organization/", "/profile/", "/products/")
    if any(m in path for m in profile_markers):
        return False
    list_markers = (
        "/startups/",
        "/industry/",
        "/categories/",
        "/developers",
        "/agencies",
        "/software/",
        "/browse",
        "/search",
    )
    return any(m in path for m in list_markers)
