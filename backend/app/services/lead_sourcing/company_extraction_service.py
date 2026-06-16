"""Company Extraction Engine — directorios → empresas reales."""

from __future__ import annotations

import hashlib
import logging

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_extraction.extractors import (
    detect_platform,
    extract_from_directory,
)
from app.services.lead_sourcing.company_extraction.models import (
    ExtractedCompanyRow,
    ExtractionRunResult,
)
from app.services.lead_sourcing.company_relevance import (
    canonical_company_key,
    merge_company_candidates,
    passes_relevance_threshold,
    score_company_relevance,
)
from app.services.lead_sourcing.company_search_classifier import classify_company_hit
from app.services.lead_sourcing.icp_intelligence import parse_company_icp
from app.services.lead_sourcing.pipeline_runtime import run_with_timeout
from app.services.lead_sourcing.phantombuster_queue import PHANTOM_ONLY_PLATFORMS
from app.services.lead_sourcing.timeouts_config import PER_DIRECTORY_SOURCE_TIMEOUT_SEC

_logger = logging.getLogger(__name__)


class CompanyExtractionService:
    """Extrae empresas reales desde URLs de directorios/listados."""

    def extract_from_directories(
        self,
        campaign: Campaign,
        directory_sources: list[CompanyCandidateRead],
        existing_companies: list[CompanyCandidateRead] | None = None,
        *,
        max_per_source: int = 25,
        max_pages: int = 3,
    ) -> tuple[list[CompanyCandidateRead], ExtractionRunResult]:
        profile = parse_company_icp(campaign)
        run = ExtractionRunResult()
        extracted_rows: list[CompanyCandidateRead] = []

        for src in directory_sources:
            url = (src.website_url or "").strip()
            if not url:
                continue
            platform = detect_platform(url)
            if platform in PHANTOM_ONLY_PLATFORMS:
                from app.services.lead_sourcing.company_extraction.models import (
                    ExtractionSourceResult,
                )

                run.by_source.append(
                    ExtractionSourceResult(
                        directory_url=url,
                        platform=platform,
                        status="requires_phantombuster",
                        message="Fuente bloqueada para crawling directo — usar PhantomBuster",
                    )
                )
                continue
            try:
                rows, source_result = run_with_timeout(
                    lambda u=url, p=platform: extract_from_directory(
                        u,
                        platform=p,
                        max_companies=max_per_source,
                        max_pages=max_pages,
                    ),
                    PER_DIRECTORY_SOURCE_TIMEOUT_SEC,
                    f"Extracción {platform} ({url[:60]})",
                )
                run.by_source.append(source_result)
                run.total_extracted += len(rows)

                source_candidates: list[CompanyCandidateRead] = []
                for row in rows:
                    candidate = self._row_to_candidate(
                        row,
                        campaign,
                        profile,
                    )
                    if candidate and passes_relevance_threshold(candidate):
                        extracted_rows.append(candidate)
                        source_candidates.append(candidate)

                if source_candidates:
                    source_result.avg_icp_score = round(
                        sum(c.icp_relevance_score for c in source_candidates)
                        / len(source_candidates)
                    )
            except Exception as e:
                _logger.warning("[company-extraction] %s failed: %s", url, e)
                from app.services.lead_sourcing.company_extraction.models import (
                    ExtractionSourceResult,
                )
                err_text = str(e)
                is_blocked = "403" in err_text or "HTTP 403" in err_text
                run.by_source.append(
                    ExtractionSourceResult(
                        directory_url=url,
                        platform=platform,
                        status="requires_phantombuster" if is_blocked else "error",
                        message=(
                            "Fuente bloqueada — enviar a PhantomBuster"
                            if is_blocked
                            else err_text
                        ),
                        error=None if is_blocked else err_text,
                    )
                )

        direct = [
            c
            for c in (existing_companies or [])
            if c.result_kind == "company"
        ]
        merged = merge_company_candidates(direct + extracted_rows)
        run.total_after_dedupe = len(merged)

        _logger.info(
            "[company-extraction] sources=%s extracted=%s merged=%s",
            len(directory_sources),
            run.total_extracted,
            run.total_after_dedupe,
        )
        return merged, run

    def _row_to_candidate(
        self,
        row: ExtractedCompanyRow,
        campaign: Campaign,
        profile,
    ) -> CompanyCandidateRead | None:

        classified = classify_company_hit(row.profile_url, row.name)
        if classified is None:
            classified_hit_url = row.profile_url
            classified_name = row.name
            result_kind = "company"
            quality = 70
        else:
            classified_hit_url = classified.url
            classified_name = classified.name
            result_kind = classified.kind.value
            quality = classified.quality_score

        if result_kind != "company":
            return None

        relevance = score_company_relevance(
            profile,
            name=classified_name,
            url=classified_hit_url,
            title=row.name,
            snippet=" ".join(row.tags),
            result_kind="company",
        )

        canonical = canonical_company_key(classified_hit_url, classified_name)
        ext = hashlib.sha256(classified_hit_url.encode()).hexdigest()[:16]

        return CompanyCandidateRead(
            external_id=f"extract-{ext}",
            provider="company_extraction",
            name=classified_name,
            website_url=classified_hit_url,
            industry=row.industry or campaign.target_industry,
            country=row.location or campaign.target_country,
            city=row.location,
            result_kind="company",
            quality_score=quality,
            icp_relevance_score=relevance,
            description=", ".join(row.tags) if row.tags else None,
            canonical_key=canonical,
            source_directory_url=row.source_directory_url,
            tags=row.tags,
            extracted_from=row.source_platform,
        )
