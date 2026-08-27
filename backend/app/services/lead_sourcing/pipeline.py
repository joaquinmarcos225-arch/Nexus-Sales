"""Pipeline MVP: ICP → Web Search → Prospeo → Nexus Outreach."""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from pydantic import ValidationError

from app.schemas.lead_sourcing import (
    CompanyCandidateRead,
    LeadCandidateRead,
    DiscardedLeadRead,
    LeadScoreAuditRead,
    LeadSourcingPipelineRead,
    PipelineRunRead,
    PipelineRunStateRead,
    PipelineStageLogRead,
)

from app.services import prospect_scoring as scoring
from app.services.lead_sourcing import pipeline_store as store
from app.services.lead_sourcing.pipeline_runtime import (
    PipelineTimeoutError,
    run_with_timeout,
    stage_timeout_sec,
)
from app.services.lead_sourcing.lead_sourcing_company_targeting import (
    TargetCompany,
    collect_target_companies,
    score_lead_company_targeted,
)
from app.services.lead_sourcing.mvp_pipeline import (
    MVP_PIPELINE_STEPS,
    PHANTOM_PIPELINE_STEPS,
    get_min_lead_display_score,
    is_phantom_related_message,
    mvp_substeps_full,
    sanitize_panel_last_error,
)
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
from app.services.lead_sourcing.diag_trace import DiagTrace
from app.services.lead_sourcing.pipeline_stage_log import (
    append_log,
    mark_error,
    mark_finished,
    mark_running,
    recover_stale_run,
    run_state_for_read,
    run_state_read,
)
from app.services.lead_sourcing.timeouts_config import PROSPEO_ENRICH_MAX_SEC
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
from app.services.lead_sourcing.providers.registry import (
    get_company_search_provider,
    get_contact_enrichment_provider,
)

_logger = logging.getLogger(__name__)

STAGE_LABELS: dict[str, str] = {
    "idle": "En espera",
    "searching_companies": "Buscando empresas",
    "companies_found": "Empresas encontradas",
    "searching_people": "Buscando personas",
    "preparing_phantom": "Preparando pipeline",
    "phantom_ready": "Empresas listas",
    "extracting_people": "Extrayendo personas",
    "leads_detected": "Leads detectados",
    "enriching_contacts": "Enriqueciendo contactos",
    "ready_to_import": "Listos para importar",
    "error": "Error",
}


def _score_lead(
    lead: LeadCandidateRead,
    campaign: Campaign,
    *,
    fit_threshold: int,
    target_companies: list | None = None,
) -> LeadCandidateRead:
    targets = target_companies or []
    if targets:
        compat, breakdown_text, breakdown_details = score_lead_company_targeted(
            lead,
            campaign,
            targets,
        )
    else:
        fields = {
            "country": lead.country,
            "industry": lead.industry,
            "role": lead.role,
            "email": lead.email,
            "linkedin_url": lead.linkedin_url,
        }
        compat, breakdown_text, breakdown_details = scoring.score_prospect_breakdown(
            fields,
            campaign_country=campaign.target_country,
            campaign_industry=campaign.target_industry,
            campaign_role=campaign.target_role,
        )
    display_min = get_min_lead_display_score()
    if compat >= fit_threshold:
        fit_tier = "good"
    else:
        fit_tier = "low_fit"
    matched_co = breakdown_details.get("matched_icp_company") if isinstance(
        breakdown_details, dict
    ) else None
    ratio = breakdown_details.get("company_match_ratio") if isinstance(
        breakdown_details, dict
    ) else None
    return lead.model_copy(
        update={
            "compatibility_score": compat,
            "fit_tier": fit_tier,
            "score_breakdown": breakdown_text,
            "score_details": breakdown_details,
            "matched_icp_company": matched_co if isinstance(matched_co, str) else None,
            "company_match_ratio": float(ratio) if isinstance(ratio, (int, float)) else None,
            "discard_reason": None,
        }
    )


def _build_discarded_leads(meta: dict) -> list[DiscardedLeadRead]:
    out: list[DiscardedLeadRead] = []
    seen: set[str] = set()

    def _add(
        *,
        name: str | None,
        company_name: str | None,
        reason: str,
        score: int | None = None,
        breakdown: str | None = None,
        sample: dict | None = None,
    ) -> None:
        key = f"{reason}|{name or ''}|{score or ''}"
        if key in seen:
            return
        seen.add(key)
        out.append(
            DiscardedLeadRead(
                name=name,
                company_name=company_name,
                reason=reason,
                compatibility_score=score,
                score_breakdown=breakdown,
                sample=sample,
            )
        )

    for item in meta.get("phantom_parse_discards") or []:
        if not isinstance(item, dict):
            continue
        _add(
            name=item.get("name") if isinstance(item.get("name"), str) else None,
            company_name=item.get("company_name") if isinstance(item.get("company_name"), str) else None,
            reason=str(item.get("reason") or "parse_discard"),
            sample=item.get("sample") if isinstance(item.get("sample"), dict) else None,
        )

    return out[:120]


def _build_lead_score_audit(
    people: list[LeadCandidateRead],
    *,
    fit_threshold: int,
) -> list[LeadScoreAuditRead]:
    display_min = get_min_lead_display_score()
    audit: list[LeadScoreAuditRead] = []
    for p in people:
        score = p.compatibility_score or 0
        audit.append(
            LeadScoreAuditRead(
                external_id=p.external_id,
                name=p.name,
                company_name=p.company_name,
                compatibility_score=score,
                fit_tier=p.fit_tier or ("good" if score >= fit_threshold else "low_fit"),
                score_breakdown=p.score_breakdown,
                score_details=p.score_details if isinstance(p.score_details, dict) else None,
                visible_in_panel=True,
                discard_reason=p.discard_reason,
            )
        )
    return audit


def _sanitize_companies(
    companies: list[CompanyCandidateRead],
    campaign: Campaign,
    db: Session | None = None,
) -> list[CompanyCandidateRead]:
    profile = parse_company_icp(campaign)
    refreshed: list[CompanyCandidateRead] = []
    for c in companies:
        url = (c.website_url or "").strip()
        if not url:
            continue
        hit = classify_company_hit(url, c.name or "")
        if hit is None:
            continue
        relevance = score_company_relevance(
            profile,
            name=hit.normalized_name or hit.name,
            url=hit.url,
            title=c.name or "",
            snippet=c.description or "",
            result_kind=hit.kind.value,
        )
        confidence = compute_extraction_confidence(
            source_type=hit.source_type,
            icp_relevance_score=relevance,
            quality_score=hit.quality_score,
            normalized_name=hit.normalized_name,
            raw_title=c.name or "",
        )
        canonical = c.canonical_key or canonical_company_key(hit.url, hit.normalized_name or hit.name)
        display = hit.normalized_name or hit.name
        refreshed.append(
            c.model_copy(
                update={
                    "name": display,
                    "website_url": hit.url,
                    "result_kind": hit.kind.value,
                    "quality_score": hit.quality_score,
                    "icp_relevance_score": relevance,
                    "normalized_company_name": hit.normalized_name,
                    "source_type": hit.source_type,
                    "confidence": confidence,
                    "canonical_key": canonical,
                }
            )
        )
    merged = merge_company_candidates(refreshed)
    companies = [c for c in merged if passes_web_search_company_row(c)]
    directories = [
        c for c in merged if c.result_kind == "directory_source" and passes_relevance_threshold(c)
    ]
    out = companies + directories
    if db is not None:
        out = _filter_crm_excluded_companies(db, campaign.company_id, out)
    return out


def _filter_crm_excluded_companies(
    db: Session,
    company_id: int,
    companies: list[CompanyCandidateRead],
) -> list[CompanyCandidateRead]:
    from app.services.crm import exclusions as crm_exclusions

    kept: list[CompanyCandidateRead] = []
    for c in companies:
        blocked = crm_exclusions.is_crm_excluded(
            db,
            company_id,
            company_name=c.name or c.normalized_company_name,
            company_website=c.website_url,
            company_domain=c.company_domain,
        )
        if blocked is None:
            kept.append(c)
    return kept


def _filter_crm_excluded_people(
    db: Session,
    company_id: int,
    people: list[LeadCandidateRead],
) -> list[LeadCandidateRead]:
    """Quita personas cuyo email/dominio/empresa ya está en exclusiones pre-Nexus."""
    from app.services.crm import exclusions as crm_exclusions

    kept: list[LeadCandidateRead] = []
    for p in people:
        blocked = crm_exclusions.is_crm_excluded(
            db,
            company_id,
            email=getattr(p, "email", None),
            company_name=getattr(p, "company_name", None),
            company_website=getattr(p, "company_website", None),
            company_domain=getattr(p, "company_domain", None),
        )
        if blocked is None:
            kept.append(p)
    return kept


def _sanitize_stage_logs(logs: list) -> list[PipelineStageLogRead]:
    """Evita 500 si hay entradas viejas/corruptas en meta.stage_logs."""
    from app.services.lead_sourcing.pipeline_stage_log import _utc_now_iso

    out: list[PipelineStageLogRead] = []
    for log in logs:
        if not isinstance(log, dict):
            continue
        try:
            out.append(
                PipelineStageLogRead(
                    step=str(log.get("step") or "unknown"),
                    stage=str(log.get("stage") or "idle"),
                    event=str(log.get("event") or "info"),
                    message=str(log.get("message") or ""),
                    at=str(log.get("at") or _utc_now_iso()),
                    duration_ms=log.get("duration_ms"),
                    result_count=log.get("result_count"),
                )
            )
        except (ValidationError, TypeError, ValueError):
            continue
    return out


def _prospeo_health_read(meta: dict) -> "ProspeoHealthRead | None":
    from app.schemas.lead_sourcing import ProspeoHealthRead
    from app.services.lead_sourcing.prospeo_api_health import sanitize_prospeo_health_dict

    raw = meta.get("prospeo_health")
    if isinstance(raw, dict):
        try:
            return ProspeoHealthRead.model_validate(sanitize_prospeo_health_dict(raw))
        except ValidationError:
            pass
    return None


def _enrich_progress_read(meta: dict) -> "EnrichProgressRead | None":
    from app.schemas.lead_sourcing import EnrichProgressRead

    raw = meta.get("enrich_progress")
    if isinstance(raw, dict):
        try:
            return EnrichProgressRead.model_validate(raw)
        except ValidationError:
            pass
    return None


def _run_state_safe(meta: dict, *, for_read: bool = False) -> PipelineRunStateRead:
    raw = run_state_for_read(meta) if for_read else run_state_read(meta)
    try:
        return PipelineRunStateRead.model_validate(raw)
    except ValidationError:
        return PipelineRunStateRead(running=False)


_READ_MAX_STAGE_LOGS = 12


def read_pipeline(
    db: Session,
    campaign: Campaign,
    *,
    trace: DiagTrace | None = None,
) -> LeadSourcingPipelineRead:
    """GET rápido: sin sanitize pesado, sin commit, sin APIs externas."""
    t0 = time.perf_counter()
    if trace:
        trace.step("read_pipeline.get_row_before")
    row = store.get_row(db, campaign.id)
    if trace:
        trace.step("read_pipeline.get_row_after", found=row is not None)
    if row is None:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _logger.info(
            "[lead-sourcing] GET pipeline campaign=%s empty row elapsed_ms=%s",
            campaign.id,
            elapsed_ms,
        )
        return LeadSourcingPipelineRead(
            campaign_id=campaign.id,
            stage="idle",
            stage_label=STAGE_LABELS["idle"],
        )

    if trace:
        trace.step("read_pipeline.load_meta_before")
    meta = store.load_meta(row)
    from app.services.lead_sourcing.prospeo_api_health import cleanup_stale_prospeo_meta

    meta, meta_stale = cleanup_stale_prospeo_meta(meta)
    if meta_stale:
        store.save_meta(row, meta)
        db.commit()
    if trace:
        trace.step("read_pipeline.load_meta_after")
    try:
        if trace:
            trace.step("read_pipeline.load_companies_before")
        companies = store.load_companies(row)
        if trace:
            trace.step("read_pipeline.load_companies_after", count=len(companies))
        if trace:
            trace.step("read_pipeline.load_people_before")
        from app.services.lead_sourcing.contact_identity import (
            filter_generic_contacts,
            filter_pipeline_people,
            is_outreach_ready_any,
        )

        raw_people = store.load_people(row)
        people = filter_pipeline_people(raw_people) + filter_generic_contacts(raw_people)
        from app.services.lead_sourcing.mvp_enrichment import (
            refresh_prospeo_people_scores,
            refresh_prospecting_contact_fields,
            reconcile_prospeo_search_debug,
        )

        people = refresh_prospeo_people_scores(
            people,
            companies,
            fit_threshold=row.fit_threshold,
            icp_target_role=campaign.target_role,
            icp_target_industry=campaign.target_industry,
            icp_target_country=campaign.target_country,
            icp_target_company_size=campaign.target_company_size,
        )
        people = refresh_prospecting_contact_fields(people)
        people = _filter_crm_excluded_people(db, campaign.company_id, people)
        psd_raw = meta.get("prospeo_search_debug")
        if isinstance(psd_raw, list):
            meta["prospeo_search_debug"] = reconcile_prospeo_search_debug(
                psd_raw, companies, people
            )
        if trace:
            trace.step("read_pipeline.load_people_after", count=len(people))
        extraction_stats = meta.get("extraction_stats")
        extracted_count = sum(
            1
            for c in companies
            if c.result_kind == "company" and (c.extracted_from or c.provider == "company_extraction")
        )
        raw_logs = meta.get("stage_logs") if isinstance(meta.get("stage_logs"), list) else []
        filtered_logs = _filter_stale_logs(raw_logs[-_READ_MAX_STAGE_LOGS:])
        stage_logs = _sanitize_stage_logs(filtered_logs)
        stage_logs = [
            log
            for log in stage_logs
            if log.step not in PHANTOM_PIPELINE_STEPS
            and not is_phantom_related_message(f"{log.message} {log.step}")
        ]
        extraction_stats = meta.get("extraction_stats")
        blocked_count = 0
        if isinstance(extraction_stats, dict):
            sources = extraction_stats.get("sources")
            if isinstance(sources, list):
                blocked_count = sum(
                    1
                    for s in sources
                    if isinstance(s, dict) and s.get("status") == "requires_phantombuster"
                )
        if trace:
            trace.step("read_pipeline.build_response_before")
        targets_for_read: list[TargetCompany] = []
        raw_tc = meta.get("target_companies")
        if isinstance(raw_tc, list):
            for item in raw_tc:
                if isinstance(item, dict) and item.get("name"):
                    targets_for_read.append(
                        TargetCompany(
                            name=str(item["name"]),
                            url=item.get("url") if isinstance(item.get("url"), str) else None,
                            icp_relevance_score=int(item.get("icp_relevance_score") or 0),
                            canonical_key=str(item.get("canonical_key") or ""),
                        )
                    )
        if not targets_for_read:
            try:
                targets_for_read = collect_target_companies(companies, None)
            except Exception as tc_err:
                _logger.warning(
                    "[lead-sourcing] collect_target_companies skipped campaign=%s: %s",
                    campaign.id,
                    tc_err,
                )
        if people and (
            any(p.fit_tier is None for p in people)
            or any(p.score_details is None for p in people)
        ):
            people = [
                _score_lead(
                    p,
                    campaign,
                    fit_threshold=row.fit_threshold,
                    target_companies=targets_for_read,
                )
                for p in people
            ]
        discarded_leads = _build_discarded_leads(meta)
        lead_score_audit = _build_lead_score_audit(people, fit_threshold=row.fit_threshold)
        if not lead_score_audit and isinstance(meta.get("lead_score_audit"), list):
            for item in meta["lead_score_audit"]:
                if isinstance(item, dict):
                    try:
                        lead_score_audit.append(LeadScoreAuditRead.model_validate(item))
                    except ValidationError:
                        continue
        display_min = get_min_lead_display_score()
        from app.services.lead_sourcing.lead_profile import build_profiles

        from app.services.lead_sourcing.mvp_enrichment import (
            build_company_contact_rows,
            build_prospecting_lead_rows,
            compute_mvp_metrics,
        )
        from app.services.lead_sourcing.prospeo_phone import prospeo_phone_capabilities_note

        fit = row.fit_threshold
        lead_profiles = build_profiles(
            people,
            companies,
            meta.get("lead_profiles_cache") or {},
            fit_threshold=fit,
            icp_target_phrase=meta.get("icp_target_phrase") or meta.get("search_query"),
            campaign_target_industry=campaign.target_industry,
            campaign_target_role=campaign.target_role,
            campaign_target_country=campaign.target_country,
            campaign_target_company_size=campaign.target_company_size,
        )
        mvp_metrics = compute_mvp_metrics(companies, people, fit_threshold=fit)
        from app.services.lead_sourcing.corporate_domain_resolver import compute_domain_resolution_metrics
        from app.schemas.mvp_outreach import MvpDomainResolutionMetricsRead

        dr_raw = meta.get("domain_resolution")
        if isinstance(dr_raw, dict) and "domains_resolved" in dr_raw:
            domain_metrics = MvpDomainResolutionMetricsRead.model_validate(
                {
                    "companies_found": dr_raw.get("companies_found", 0),
                    "domains_resolved": dr_raw.get("domains_resolved", 0),
                    "domain_resolution_rate_pct": dr_raw.get("domain_resolution_rate_pct", 0),
                }
            )
        else:
            domain_metrics = MvpDomainResolutionMetricsRead.model_validate(
                compute_domain_resolution_metrics(companies, fit_threshold=fit)
            )
        company_contacts = build_company_contact_rows(companies, people, fit_threshold=fit)
        prospecting_leads = build_prospecting_lead_rows(people, fit_threshold=fit)
        result = LeadSourcingPipelineRead(
            campaign_id=campaign.id,
            stage=row.stage,
            stage_label=STAGE_LABELS.get(row.stage, row.stage),
            fit_threshold=row.fit_threshold,
            display_min_score=display_min,
            companies_count=len([c for c in companies if c.result_kind == "company"]),
            people_count=len(people),
            ready_count=sum(
                1 for p in people if is_outreach_ready_any(p, fit_threshold=row.fit_threshold)
            ),
            companies=companies,
            people=people,
            discarded_leads=discarded_leads,
            lead_score_audit=lead_score_audit,
            search_query=meta.get("search_query") or meta.get("google_query"),
            icp_target_phrase=meta.get("icp_target_phrase") or meta.get("search_query"),
            google_query=meta.get("search_query") or meta.get("google_query"),
            last_error=sanitize_panel_last_error(meta.get("last_error")),
            pipeline_steps=list(MVP_PIPELINE_STEPS),
            extraction_stats=extraction_stats if isinstance(extraction_stats, dict) else None,
            extracted_companies_count=extracted_count,
            blocked_sources_count=blocked_count,
            stage_logs=stage_logs,
            run_state=_run_state_safe(meta, for_read=True),
            lead_profiles=lead_profiles,
            mvp_contact_metrics=mvp_metrics,
            domain_resolution_metrics=domain_metrics,
            company_contacts=company_contacts,
            prospeo_contact_debug=(
                meta.get("prospeo_contact_debug")
                if isinstance(meta.get("prospeo_contact_debug"), list)
                else []
            )[:80],
            domain_resolution_debug=(
                meta.get("domain_resolution_debug")
                if isinstance(meta.get("domain_resolution_debug"), list)
                else []
            )[:80],
            prospeo_search_debug=(
                meta.get("prospeo_search_debug")
                if isinstance(meta.get("prospeo_search_debug"), list)
                else []
            )[:80],
            prospeo_health=_prospeo_health_read(meta),
            enrich_progress=_enrich_progress_read(meta),
            prospecting_leads=prospecting_leads,
            prospeo_phone_info=prospeo_phone_capabilities_note(),
        )
        if trace:
            trace.step("read_pipeline.build_response_after")
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _logger.info(
            "[lead-sourcing] GET pipeline campaign=%s companies=%s people=%s elapsed_ms=%s",
            campaign.id,
            len(companies),
            len(people),
            elapsed_ms,
        )
        return result
    except ValidationError as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _logger.warning(
            "[lead-sourcing] GET pipeline validation failed campaign=%s elapsed_ms=%s err=%s",
            campaign.id,
            elapsed_ms,
            e,
        )
        return LeadSourcingPipelineRead(
            campaign_id=campaign.id,
            stage=row.stage or "error",
            stage_label=STAGE_LABELS.get(row.stage, row.stage or "Error"),
            fit_threshold=row.fit_threshold,
            last_error=f"No se pudo leer el pipeline guardado: {e}",
            run_state=_run_state_safe(meta, for_read=True),
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _logger.exception(
            "[lead-sourcing] GET pipeline failed campaign=%s elapsed_ms=%s",
            campaign.id,
            elapsed_ms,
        )
        return LeadSourcingPipelineRead(
            campaign_id=campaign.id,
            stage=row.stage or "error",
            stage_label=STAGE_LABELS.get(row.stage, row.stage or "Error"),
            fit_threshold=row.fit_threshold,
            last_error=f"Error al cargar pipeline: {e}",
            run_state=PipelineRunStateRead(running=False),
        )


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _audit_from_raw(raw: object) -> list[LeadScoreAuditRead]:
    if not isinstance(raw, list):
        return []
    out: list[LeadScoreAuditRead] = []
    for item in raw[:80]:
        if not isinstance(item, dict):
            continue
        try:
            out.append(LeadScoreAuditRead.model_validate(item))
        except ValidationError:
            continue
    return out


def _filter_stale_logs(logs: list) -> list:
    return logs if logs else []


def _commit_running(db: Session, row, meta: dict, *, step: str, stage: str) -> None:
    mark_running(meta, step=step, stage=stage)
    store.set_stage(row, stage)
    store.save_meta(row, meta)
    db.commit()


def _run_companies(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
    *,
    company_limit: int,
) -> int:
    web = get_company_search_provider()
    profile = parse_company_icp(campaign)
    search_round = int(meta.get("company_search_round") or 0)
    queries = build_company_search_queries(
        campaign,
        profile=profile,
        query_offset=search_round * 3,
    )
    query = profile.primary_target_phrase()
    meta["search_queries"] = queries
    meta["search_query"] = query
    meta["icp_target_phrase"] = query
    meta.pop("google_query", None)
    err = meta.get("last_error") or ""
    if "Google Search" in err or "Custom Search" in err:
        meta.pop("last_error", None)

    cached_co: list = []
    cache_co_diag: dict = {}
    try:
        from app.services.nexus_contact_cache import find_cached_companies_for_campaign

        cached_co, cache_co_diag = find_cached_companies_for_campaign(
            db, campaign, limit=company_limit
        )
    except Exception:  # noqa: BLE001
        cached_co, cache_co_diag = [], {"error": "lookup_failed"}
    meta["nexus_company_cache"] = cache_co_diag

    remain = max(0, company_limit - len(cached_co))
    found_web: list = []
    if remain > 0:
        found_web = _sanitize_companies(
            web.search_companies(
                campaign,
                limit=remain,
                query_offset=search_round * 3,
            ),
            campaign,
            db=db,
        )
    found = list(cached_co) + list(found_web)
    # Merge con el lote anterior: no tirar empresas buenas ya resueltas.
    prev = store.load_companies(row) or []
    by_key: dict[str, Any] = {}
    for c in list(prev) + list(found):
        key = (c.canonical_key or c.external_id or c.name or "").strip().lower()
        if not key:
            continue
        prev_c = by_key.get(key)
        if prev_c is None:
            by_key[key] = c
            continue
        # Preferir el que tenga dominio corporativo usable.
        from app.services.lead_sourcing.prospeo_contact_validation import (
            is_prospeo_searchable_domain,
        )

        prev_ok = is_prospeo_searchable_domain(prev_c.company_domain)
        new_ok = is_prospeo_searchable_domain(c.company_domain)
        if new_ok and not prev_ok:
            by_key[key] = c
        elif (c.icp_relevance_score or 0) > (prev_c.icp_relevance_score or 0):
            by_key[key] = c
    companies = sorted(
        by_key.values(),
        key=lambda c: -(c.icp_relevance_score or 0),
    )[: max(company_limit, 40)]
    store.save_companies(row, companies)
    store.set_stage(row, "companies_found")
    meta["companies_found_at"] = meta.get("companies_found_at") or "ok"
    meta["icp_accounts_seeded"] = 0
    meta["company_search_round"] = search_round + 1
    # Nuevo lote de empresas → reiniciar cursor de enrich.
    meta.pop("enrich_progress", None)
    meta.pop("last_error", None)
    meta["quota_force_full"] = False
    store.save_meta(row, meta)
    db.commit()
    return len(companies)


def _seed_icp_account_leads(
    row,
    companies: list[CompanyCandidateRead],
    campaign: Campaign,
    db: Session | None = None,
) -> int:
    """Cuentas ICP desde Web Search — MVP sin Phantom."""
    fit_threshold = int(getattr(row, "fit_threshold", None) or 70)

    accounts: list[LeadCandidateRead] = []
    seen: set[str] = set()
    for c in companies:
        if c.result_kind != "company":
            continue
        key = (c.canonical_key or c.external_id or c.name or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        score = max(c.confidence or 0, c.icp_relevance_score or 0)
        from app.services.lead_sourcing.prospeo_contact_validation import is_directory_host
        from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

        domain = (c.company_domain or "").strip().lower() or None
        if c.website_url:
            web_dom = _website_domain(c.website_url)
            if web_dom and not is_directory_host(web_dom):
                domain = web_dom
            elif web_dom and is_directory_host(web_dom):
                domain = None
        if domain and is_directory_host(domain):
            domain = None
        if db is not None:
            from app.services.crm import exclusions as crm_exclusions

            if crm_exclusions.is_crm_excluded(
                db,
                campaign.company_id,
                company_name=c.name,
                company_website=c.website_url,
                company_domain=domain,
            ):
                continue
        accounts.append(
            LeadCandidateRead(
                external_id=f"icp-account-{c.external_id or key[:32]}",
                provider="web_search",
                name=(c.name or "").strip()[:255] or "Contacto",
                company_name=(c.name or "").strip()[:255] or "",
                company_website=(c.website_url or "").strip() or None,
                company_domain=domain,
                linked_company_key=key,
                industry=c.industry,
                country=c.country,
                compatibility_score=score,
                fit_tier="good" if score >= fit_threshold else "low_fit",
                score_breakdown=f"Cuenta ICP · Web Search (relevancia {score})",
                enrichment_source="web_search",
                enrichment_confidence=score,
                visible_in_panel=True,
            )
        )
    if not accounts:
        return 0
    store.save_people(row, accounts)
    store.set_stage(row, "leads_detected")
    return len(accounts)


def _run_enrich(db: Session, row, meta: dict, campaign: Campaign) -> int:
    from app.services.lead_sourcing.lead_profile import build_profiles
    from app.services.lead_sourcing.mvp_enrichment import run_mvp_company_enrichment
    from app.services.lead_sourcing.nexus_outreach_mvp import generate_for_eligible_profiles
    from app.services.lead_sourcing.prospeo_api_health import cleanup_stale_prospeo_meta
    from app.services.lead_sourcing.timeouts_config import PROSPEO_ENRICH_BATCH_SIZE

    meta, _ = cleanup_stale_prospeo_meta(meta)
    companies = store.load_companies(row)
    people = store.load_people(row)
    threshold = row.fit_threshold
    progress = meta.get("enrich_progress") if isinstance(meta.get("enrich_progress"), dict) else {}
    company_offset = int(progress.get("processed") or progress.get("cursor") or 0)

    def _log(msg: str) -> None:
        append_log(meta, step="enrich", stage="enriching_contacts", event="info", message=msg[:500])

    def _merge_debug_list(key: str, new_rows: list | None) -> None:
        if not isinstance(new_rows, list) or not new_rows:
            return
        prev = meta.get(key) if isinstance(meta.get(key), list) else []
        meta[key] = (prev + new_rows)[-80:]

    def _checkpoint(
        co: list,
        pe: list,
        batch_stats: dict,
    ) -> None:
        store.save_companies(row, co)
        store.save_people(row, pe)
        ep = batch_stats.get("enrich_progress")
        if isinstance(ep, dict):
            meta["enrich_progress"] = ep
        _merge_debug_list("prospeo_search_debug", batch_stats.get("prospeo_search_debug"))
        _merge_debug_list("prospeo_contact_debug", batch_stats.get("contact_validation_debug"))
        _merge_debug_list("prospeo_contact_debug", batch_stats.get("contact_validation_debug"))
        _merge_debug_list("prospeo_search_debug", batch_stats.get("prospeo_search_debug"))
        ph = batch_stats.get("prospeo_health")
        if isinstance(ph, dict):
            from app.services.lead_sourcing.prospeo_api_health import sanitize_prospeo_health_dict

            meta["prospeo_health"] = sanitize_prospeo_health_dict(ph)
        store.save_meta(row, meta)
        db.commit()

    cached_ph = meta.get("prospeo_health") if isinstance(meta.get("prospeo_health"), dict) else None
    from app.services.lead_sourcing.prospeo_api_health import prospeo_rate_limit_cooldown_active

    # Si hay rate limit activo, reusar health aunque el cursor vuelva a 0 (evita martillar).
    use_cached_health = bool(cached_ph) and (
        company_offset > 0 or prospeo_rate_limit_cooldown_active(cached_ph)
    )
    companies, people, enrich_stats = run_mvp_company_enrichment(
        companies=companies,
        people=people,
        campaign=campaign,
        fit_threshold=threshold,
        log_fn=_log,
        company_offset=company_offset,
        max_companies_per_run=PROSPEO_ENRICH_BATCH_SIZE,
        on_checkpoint=_checkpoint,
        cached_prospeo_health=cached_ph if use_cached_health else None,
        db=db,
    )
    store.save_companies(row, companies)
    people = _filter_crm_excluded_people(db, campaign.company_id, people)
    store.save_people(row, people)
    meta["mvp_enrich_stats"] = enrich_stats
    ep = enrich_stats.get("enrich_progress")
    if isinstance(ep, dict):
        meta["enrich_progress"] = ep
    _merge_debug_list("prospeo_contact_debug", enrich_stats.get("contact_validation_debug"))
    _merge_debug_list("prospeo_search_debug", enrich_stats.get("prospeo_search_debug"))
    ph = enrich_stats.get("prospeo_health")
    if isinstance(ph, dict):
        from app.services.lead_sourcing.prospeo_api_health import sanitize_prospeo_health_dict

        meta["prospeo_health"] = sanitize_prospeo_health_dict(ph)
    dr = enrich_stats.get("domain_resolution")
    if isinstance(dr, dict) and isinstance(dr.get("resolution_debug"), list):
        meta["domain_resolution_debug"] = dr["resolution_debug"][:80]

    from app.services.lead_sourcing.mvp_enrichment import build_company_contact_rows, compute_mvp_metrics

    from app.services.lead_sourcing.corporate_domain_resolver import companies_ready_for_prospeo

    profiles = build_profiles(
        people,
        companies,
        meta.get("lead_profiles_cache") or {},
        fit_threshold=threshold,
        icp_target_phrase=meta.get("icp_target_phrase") or meta.get("search_query"),
        campaign_target_industry=campaign.target_industry,
        campaign_target_role=campaign.target_role,
        campaign_target_country=campaign.target_country,
        campaign_target_company_size=campaign.target_company_size,
    )
    has_more = bool(
        isinstance(meta.get("enrich_progress"), dict) and meta["enrich_progress"].get("has_more")
    )
    if has_more:
        _log(
            f"Lote Prospeo guardado ({meta['enrich_progress'].get('processed', 0)}/"
            f"{meta['enrich_progress'].get('total', 0)}). Usá «Enriquecer siguientes»."
        )
    elif companies_ready_for_prospeo(companies, fit_threshold=threshold):
        profiles = generate_for_eligible_profiles(db, campaign, profiles)
    else:
        _log(
            "Nexus Outreach omitido: ninguna empresa con dominio corporativo real. "
            "Revisá la tabla de dominios y volvé a enriquecer."
        )
    meta["mvp_contact_metrics"] = compute_mvp_metrics(companies, people, fit_threshold=threshold).model_dump()
    meta["company_contact_rows"] = [
        r.model_dump() for r in build_company_contact_rows(companies, people, fit_threshold=threshold)
    ]
    cache: dict[str, dict] = {}
    for pr in profiles:
        cache[pr.external_id] = {
            "outreach": pr.outreach.model_dump() if pr.outreach else None,
            "ai_sdr": pr.ai_sdr.model_dump() if pr.ai_sdr else None,
        }
    meta["lead_profiles_cache"] = cache
    meta["lead_profiles_count"] = len(profiles)
    meta["outreach_generated_count"] = sum(1 for p in profiles if p.outreach)

    store.set_stage(row, "ready_to_import")
    meta["prospeo_enriched_count"] = int(enrich_stats.get("people_enriched") or 0) + int(
        enrich_stats.get("people_discovered") or 0
    )
    meta.pop("last_error", None)
    store.save_meta(row, meta)
    db.commit()
    return int(meta["prospeo_enriched_count"])


def _run_b2c_people(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
    *,
    people_limit: int = 40,
) -> int:
    """Person-first: busca contactos B2C sin pasar por empresas."""
    from app.services.campaign_sequence_channels import campaign_requires_whatsapp
    from app.services.lead_sourcing.b2c_person_search import search_b2c_people
    from app.services.prospect_ingestion import company_contact_exclusion_sets

    try:
        excl_emails, excl_linkedin, excl_phones = company_contact_exclusion_sets(
            db, int(campaign.company_id)
        )
    except Exception:  # noqa: BLE001
        excl_emails, excl_linkedin, excl_phones = set(), set(), set()

    try:
        from app.services.nexus_contact_cache import (
            merge_exclusion_sets,
            tenant_delivered_exclusion_sets,
        )

        excl_emails, excl_linkedin, excl_phones = merge_exclusion_sets(
            (excl_emails, excl_linkedin, excl_phones),
            tenant_delivered_exclusion_sets(db, int(campaign.company_id)),
        )
    except Exception:  # noqa: BLE001
        pass

    require_mobile = campaign_requires_whatsapp(campaign)
    # Móvil se revela al activar (lazy); acá solo presupuesto de email.
    max_enrich = max(4, min(12, (max(1, min(people_limit, 80)) + 1) // 2))
    if require_mobile:
        max_enrich = min(max(people_limit + 4, 8), 16)

    limit = max(1, min(people_limit, 80))
    cached: list = []
    cache_diag: dict = {}
    try:
        from app.services.nexus_contact_cache import find_cached_leads_for_campaign

        cached, cache_diag = find_cached_leads_for_campaign(
            db,
            campaign,
            limit=limit,
            exclude_emails=excl_emails,
            exclude_linkedin=excl_linkedin,
            exclude_phones=excl_phones,
        )
    except Exception:  # noqa: BLE001
        cached, cache_diag = [], {"error": "lookup_failed"}

    remain = max(0, limit - len(cached))
    people: list = list(cached)
    diag: dict = {"nexus_cache": cache_diag}
    if remain > 0:
        prospeo_people, prospeo_diag = search_b2c_people(
            campaign,
            limit=remain,
            require_mobile=require_mobile,
            max_enrich=max_enrich,
            exclude_emails=excl_emails,
            exclude_linkedin=excl_linkedin,
            exclude_phones=excl_phones,
        )
        diag.update(prospeo_diag if isinstance(prospeo_diag, dict) else {})
        # Evitar duplicar email/LI ya traídos del cache.
        seen_em = {(p.email or "").strip().lower() for p in people if p.email}
        seen_li = {
            (p.linkedin_url or "").strip().lower() for p in people if p.linkedin_url
        }
        for p in prospeo_people:
            em = (p.email or "").strip().lower()
            li = (p.linkedin_url or "").strip().lower()
            if (em and em in seen_em) or (li and li in seen_li):
                continue
            people.append(p)
            if em:
                seen_em.add(em)
            if li:
                seen_li.add(li)
            if len(people) >= limit:
                break
    # Ya vienen con score B2C; no re-scorar con lógica B2B (industria/empresa).
    fit = get_min_lead_display_score()
    for p in people:
        score = int(p.compatibility_score or 0)
        p.fit_tier = "good" if score >= fit else "low_fit"
        p.visible_in_panel = score >= max(20, fit - 15)

    store.save_companies(row, [])
    store.save_people(row, people)
    store.set_stage(row, "ready_to_import" if people else "leads_detected")
    meta["mvp_mode"] = "b2c"
    meta["b2c_search_diag"] = diag
    meta["people_found_at"] = "ok"
    meta.pop("enrich_progress", None)

    if people:
        meta.pop("last_error", None)
        append_log(
            meta,
            step="people_direct",
            stage="ready_to_import",
            event="info",
            message=(
                f"B2C: {len(people)} personas "
                f"(cache {cache_diag.get('kept', 0)}, "
                f"filtros {diag.get('filters_tried', 0)}, hits {diag.get('raw_hits', 0)})."
            )[:500],
        )
    else:
        err_msgs = [
            str(e.get("msg") or e.get("code") or "")
            for e in (diag.get("errors") or [])
            if isinstance(e, dict)
        ]
        hint = err_msgs[0] if err_msgs else (
            "B2C: no se encontraron personas. Ampliá región, intereses o perfil "
            "(ej. Argentina + fitness coach)."
        )
        meta["last_error"] = hint[:500]
        append_log(
            meta,
            step="people_direct",
            stage="searching_people",
            event="info",
            message=hint[:500],
        )
    store.save_meta(row, meta)
    db.commit()
    return len(people)


def _run_role_first_people(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
    *,
    people_limit: int = 40,
) -> int:
    """B2B sin industria: personas por rol ICP + región (Prospeo person-first)."""
    from app.services.lead_sourcing.role_person_search import search_role_first_people
    from app.services.prospect_ingestion import company_contact_exclusion_sets

    # Contactos ya en la empresa (otras campañas / otros vendedores): no volver a traerlos.
    try:
        excl_emails, excl_linkedin, excl_phones = company_contact_exclusion_sets(
            db, int(campaign.company_id)
        )
    except Exception:  # noqa: BLE001
        excl_emails, excl_linkedin, excl_phones = set(), set(), set()

    try:
        from app.services.nexus_contact_cache import (
            merge_exclusion_sets,
            tenant_delivered_exclusion_sets,
        )

        excl_emails, excl_linkedin, excl_phones = merge_exclusion_sets(
            (excl_emails, excl_linkedin, excl_phones),
            tenant_delivered_exclusion_sets(db, int(campaign.company_id)),
        )
    except Exception:  # noqa: BLE001
        pass

    # Over-fetch search barato; enrich-person solo email (móvil WA = lazy al activar).
    limit = max(1, min(people_limit, 80))
    from app.services.campaign_sequence_channels import campaign_requires_whatsapp

    require_mobile = campaign_requires_whatsapp(campaign)
    if require_mobile:
        max_enrich = min(limit + 4, 16)
    else:
        max_enrich = max(4, min(12, (limit + 1) // 2))

    cached: list = []
    cache_diag: dict = {}
    try:
        from app.services.nexus_contact_cache import find_cached_leads_for_campaign

        cached, cache_diag = find_cached_leads_for_campaign(
            db,
            campaign,
            limit=limit,
            exclude_emails=excl_emails,
            exclude_linkedin=excl_linkedin,
            exclude_phones=excl_phones,
        )
    except Exception:  # noqa: BLE001
        cached, cache_diag = [], {"error": "lookup_failed"}

    remain = max(0, limit - len(cached))
    people: list = list(cached)
    diag: dict = {"nexus_cache": cache_diag}
    if remain > 0:
        prospeo_people, prospeo_diag = search_role_first_people(
            campaign,
            limit=remain,
            max_enrich=max_enrich,
            exclude_emails=excl_emails,
            exclude_linkedin=excl_linkedin,
            exclude_phones=excl_phones,
            require_mobile=require_mobile,
        )
        diag.update(prospeo_diag if isinstance(prospeo_diag, dict) else {})
        seen_em = {(p.email or "").strip().lower() for p in people if p.email}
        seen_li = {
            (p.linkedin_url or "").strip().lower() for p in people if p.linkedin_url
        }
        for p in prospeo_people:
            em = (p.email or "").strip().lower()
            li = (p.linkedin_url or "").strip().lower()
            if (em and em in seen_em) or (li and li in seen_li):
                continue
            people.append(p)
            if em:
                seen_em.add(em)
            if li:
                seen_li.add(li)
            if len(people) >= limit:
                break
    fit = get_min_lead_display_score()
    for p in people:
        score = int(p.compatibility_score or 0)
        p.fit_tier = "good" if score >= fit else "low_fit"
        p.visible_in_panel = score >= max(20, fit - 15)

    # Merge con gente ya en pipeline (no pisar importables previos del lote).
    existing = store.load_people(row) or []
    by_id = {p.external_id: p for p in existing if (p.external_id or "").strip()}
    for p in people:
        by_id[p.external_id] = p
    merged = list(by_id.values())

    store.save_people(row, merged)
    store.set_stage(row, "ready_to_import" if merged else "leads_detected")
    meta["mvp_mode"] = "role_first"
    meta["role_first_search_diag"] = diag
    meta["people_found_at"] = "ok"
    meta.pop("enrich_progress", None)

    if people:
        meta.pop("last_error", None)
        append_log(
            meta,
            step="people_direct",
            stage="ready_to_import",
            event="info",
            message=(
                f"Rol-first: {len(people)} contactos "
                f"(cache {cache_diag.get('kept', 0)}, "
                f"search {diag.get('raw_hits', 0)}, enrich {diag.get('enriched', 0)})."
            )[:500],
        )
    else:
        hint = (
            "Rol-first: sin contactos importables tras filtros de rol/contacto. "
            "Revisá rol ICP o región (LATAM - Brasil = sin Brasil; LATAM + Brasil incluye Brasil)."
        )
        if isinstance(diag, dict):
            hint = (
                f"Rol-first: 0 importables (search {diag.get('raw_hits', 0)}, "
                f"enrich {diag.get('enriched', 0)}, rechazados rol {diag.get('role_rejected', 0)}). "
                "Revisá rol/región."
            )[:500]
        meta["last_error"] = hint[:500]
        append_log(
            meta,
            step="people_direct",
            stage="searching_people",
            event="info",
            message=hint[:500],
        )
    store.save_meta(row, meta)
    db.commit()
    return len(people)


def _execute_substep(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
    substep: str,
    *,
    company_limit: int,
    people_limit: int,
) -> int:
    from app.services.campaign_market import campaign_is_b2c
    from app.services.lead_sourcing.sourcing_route import campaign_uses_role_first_sourcing

    if substep == "people_direct" or (
        campaign_is_b2c(campaign) and substep in ("companies", "enrich")
    ):
        _commit_running(db, row, meta, step="people_direct", stage="searching_people")
        if campaign_is_b2c(campaign):
            return run_with_timeout(
                lambda: _run_b2c_people(db, row, meta, campaign, people_limit=people_limit),
                stage_timeout_sec("enrich"),
                "B2C personas",
            )
        if campaign_uses_role_first_sourcing(campaign) or substep == "people_direct":
            return run_with_timeout(
                lambda: _run_role_first_people(
                    db, row, meta, campaign, people_limit=people_limit
                ),
                stage_timeout_sec("enrich"),
                "Prospeo rol-first",
            )
        raise ValueError("people_direct solo aplica a B2C o B2B sin industria (rol-first).")
    if substep == "companies":
        _commit_running(db, row, meta, step="companies", stage="searching_companies")
        return run_with_timeout(
            lambda: _run_companies(db, row, meta, campaign, company_limit=company_limit),
            stage_timeout_sec("companies"),
            "Web Search",
        )
    if substep in ("extract_companies", "prepare_phantom", "people"):
        raise ValueError(
            f"El paso «{substep}» ya no está disponible. Usá companies, enrich o full (Web Search + Prospeo)."
        )
    if substep == "enrich":
        _commit_running(db, row, meta, step="enrich", stage="enriching_contacts")
        return run_with_timeout(
            lambda: _run_enrich(db, row, meta, campaign),
            stage_timeout_sec("enrich"),
            "Prospeo",
        )
    if substep == "score":
        people = store.load_people(row)
        fit = get_min_lead_display_score()
        store.save_people(row, [_score_lead(p, campaign, fit_threshold=fit) for p in people])
        if row.stage in ("idle", "companies_found"):
            store.set_stage(row, "leads_detected")
        store.save_meta(row, meta)
        db.commit()
        return len(people)
    raise ValueError(f"Paso desconocido: {substep}")


def run_step(
    db: Session,
    campaign: Campaign,
    step: str,
    *,
    company_limit: int = 15,
    people_limit: int = 40,
) -> PipelineRunRead:
    from app.services.campaign_market import campaign_is_b2c
    from app.services.lead_sourcing.sourcing_route import campaign_uses_role_first_sourcing

    row = store.get_or_create(db, campaign.id)
    meta = store.load_meta(row)
    meta = recover_stale_run(db, row, meta)

    substeps: list[str]
    if campaign_is_b2c(campaign):
        # B2C: un solo paso person-first (no Web Search de empresas)
        if step in ("full", "companies", "enrich", "people_direct"):
            substeps = ["people_direct"]
        else:
            substeps = [step]
    elif campaign_uses_role_first_sourcing(campaign):
        # B2B sin industria + con rol → Prospeo por cargo (no Brave empresas genéricas)
        if step in ("full", "companies", "enrich", "people_direct"):
            substeps = ["people_direct"]
        else:
            substeps = [step]
    elif step == "full":
        substeps = mvp_substeps_full()
    else:
        substeps = [step]

    try:
        last_count = 0
        for sub in substeps:
            last_count = _execute_substep(
                db,
                row,
                meta,
                campaign,
                sub,
                company_limit=company_limit,
                people_limit=people_limit,
            )
            mark_finished(
                meta,
                step=sub,
                stage=row.stage,
                message=f"Paso «{sub}» completado",
                result_count=last_count,
            )
            store.save_meta(row, meta)
            db.commit()

        pipe = read_pipeline(db, campaign)
        return PipelineRunRead(
            ok=True,
            step=step,
            pipeline=pipe,
            message=f"Paso «{step}» completado.",
        )

    except (ProviderNotConfiguredError, ProviderAPIError, PipelineTimeoutError, ValueError) as e:
        _logger.warning("[pipeline] step=%s failed: %s", step, e)
        msg = str(e)
        mark_error(
            meta,
            step=step,
            stage=row.stage,
            message=msg,
            event="timeout" if isinstance(e, PipelineTimeoutError) else "error",
        )
        meta["last_error"] = msg
        store.set_stage(row, "error")
        store.save_meta(row, meta)
        db.commit()
        pipe = read_pipeline(db, campaign)
        return PipelineRunRead(ok=False, step=step, pipeline=pipe, message=msg)
    except Exception as e:
        _logger.exception("[pipeline] step=%s unexpected error", step)
        msg = f"Error inesperado en «{step}»: {e}"
        mark_error(meta, step=step, stage=row.stage, message=msg)
        meta["last_error"] = msg
        store.set_stage(row, "error")
        store.save_meta(row, meta)
        db.commit()
        pipe = read_pipeline(db, campaign)
        return PipelineRunRead(ok=False, step=step, pipeline=pipe, message=msg)
