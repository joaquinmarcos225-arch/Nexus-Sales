"""Web Search — multi-query por plataforma + scoring ICP + deduplicación."""

from __future__ import annotations

import hashlib
import logging

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.company_extraction_policy import (
    compute_extraction_confidence,
    passes_web_search_company_row,
)
from app.services.lead_sourcing.company_relevance import (
    canonical_company_key,
    merge_company_candidates,
    passes_relevance_threshold,
    score_company_relevance,
)
from app.services.lead_sourcing.company_search_classifier import classify_company_hit
from app.services.lead_sourcing.company_search_queries import build_company_search_queries
from app.services.lead_sourcing.icp_intelligence import parse_company_icp
from app.services.lead_sourcing.providers.base import (
    CompanySearchProvider,
    ProviderAPIError,
    ProviderNotConfiguredError,
)
from app.services.lead_sourcing.providers.web_search_backends import (
    configured_backend,
    missing_keys_hint,
    search_web,
)

_logger = logging.getLogger(__name__)


class WebSearchCompanyProvider(CompanySearchProvider):
    """CompanySearchProvider — ICP-aware, whitelist + relevancia + merge."""

    name: str = "web_search"

    def active_backend_label(self) -> str | None:
        backend = configured_backend()
        return backend.label if backend else None

    def is_configured(self) -> bool:
        return configured_backend() is not None

    def search_companies(
        self,
        campaign: Campaign,
        *,
        query: str | None = None,
        limit: int = 20,
    ) -> list[CompanyCandidateRead]:
        if not self.is_configured():
            raise ProviderNotConfiguredError(
                f"Web Search no configurado. {missing_keys_hint()}"
            )

        profile = parse_company_icp(campaign)
        queries = build_company_search_queries(campaign, profile=profile, max_queries=5)
        if not queries:
            raise ValueError("No hay ICP suficiente para armar la búsqueda web.")

        country = (campaign.target_country or "").strip() or None
        per_query = 10

        raw_candidates: list[CompanyCandidateRead] = []
        raw_total = 0
        query_errors = 0

        for q in queries:
            try:
                hits = search_web(
                    q,
                    limit=per_query,
                    country=country,
                    provider=self.name,
                )
            except ProviderAPIError as e:
                query_errors += 1
                _logger.warning("[web-search] query failed (%s): %s", q[:80], e)
                continue

            for link, title, snippet in hits:
                raw_total += 1
                classified = classify_company_hit(link, title)
                if classified is None:
                    continue

                relevance = score_company_relevance(
                    profile,
                    name=classified.normalized_name or classified.name,
                    url=classified.url,
                    title=title,
                    snippet=snippet,
                    result_kind=classified.kind.value,
                )

                confidence = compute_extraction_confidence(
                    source_type=classified.source_type,
                    icp_relevance_score=relevance,
                    quality_score=classified.quality_score,
                    normalized_name=classified.normalized_name,
                    raw_title=title,
                )

                canonical = canonical_company_key(
                    classified.url,
                    classified.normalized_name or classified.name,
                )
                ext = hashlib.sha256(classified.url.encode()).hexdigest()[:16]
                display = classified.normalized_name or classified.name
                raw_candidates.append(
                    CompanyCandidateRead(
                        external_id=f"websearch-{ext}",
                        provider="web_search",
                        name=display,
                        website_url=classified.url,
                        industry=campaign.target_industry,
                        country=campaign.target_country,
                        city=None,
                        result_kind=classified.kind.value,
                        quality_score=classified.quality_score,
                        icp_relevance_score=relevance,
                        normalized_company_name=classified.normalized_name,
                        source_type=classified.source_type,
                        confidence=confidence,
                        description=snippet or None,
                        canonical_key=canonical,
                    )
                )

        merged = merge_company_candidates(raw_candidates)
        companies = [c for c in merged if passes_web_search_company_row(c)]
        directories = [
            c
            for c in merged
            if c.result_kind == "directory_source" and passes_relevance_threshold(c)
        ]
        filtered = companies + directories
        result = filtered[:limit]

        if not result and query_errors >= len(queries):
            raise ProviderAPIError(
                f"Web Search: todas las queries fallaron ({query_errors}/{len(queries)}). "
                "Revisá BRAVE_SEARCH_API_KEY o conectividad.",
                provider=self.name,
            )

        _logger.info(
            "[web-search] icp=%r queries=%s raw=%s merged=%s kept=%s returned=%s query_errors=%s",
            profile.primary_target_phrase(),
            len(queries),
            raw_total,
            len(merged),
            len(filtered),
            len(result),
            query_errors,
        )
        return result
