"""Modelos internos para extracción desde directorios."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedCompanyRow:
    name: str
    profile_url: str
    website_url: str | None = None
    location: str | None = None
    industry: str | None = None
    tags: list[str] = field(default_factory=list)
    employee_count: int | None = None
    funding_stage: str | None = None
    source_platform: str = ""
    source_directory_url: str = ""


@dataclass
class ExtractionSourceResult:
    directory_url: str
    platform: str
    companies_found: int = 0
    pages_fetched: int = 0
    avg_icp_score: int = 0
    status: str = "ok"  # ok | requires_phantombuster | error
    message: str = ""
    error: str | None = None


@dataclass
class ExtractionRunResult:
    companies: list[ExtractedCompanyRow] = field(default_factory=list)
    by_source: list[ExtractionSourceResult] = field(default_factory=list)
    total_extracted: int = 0
    total_after_dedupe: int = 0

    def to_meta(self) -> dict:
        return {
            "total_extracted": self.total_extracted,
            "total_after_dedupe": self.total_after_dedupe,
            "sources": [
                {
                    "directory_url": s.directory_url,
                    "platform": s.platform,
                    "companies_found": s.companies_found,
                    "pages_fetched": s.pages_fetched,
                    "avg_icp_score": s.avg_icp_score,
                    "status": s.status,
                    "message": s.message,
                    "error": s.error,
                }
                for s in self.by_source
            ],
        }
