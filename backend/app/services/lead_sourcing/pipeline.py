"""Pipeline MVP: ICP → Web Search → Prospeo → Nexus Outreach (Phantom opcional)."""

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
    PhantomDebugRead,
    PhantomQueueRead,
    PipelineRunRead,
    PipelineRunStateRead,
    PipelineStageLogRead,
)
from app.services.lead_sourcing.env_config import getenv
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
from app.services.lead_sourcing.linkedin_phantom_query import get_min_lead_display_score
from app.services.lead_sourcing.phantombuster_queue import PhantomBusterQueueService
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
from app.services.lead_sourcing.mvp_pipeline import (
    MVP_PIPELINE_STEPS,
    PHANTOM_PIPELINE_STEPS,
    is_phantom_related_message,
    mvp_substeps_full,
    phantom_experimental_enabled,
    sanitize_panel_last_error,
)
from app.services.lead_sourcing.timeouts_config import PROSPEO_ENRICH_MAX_SEC
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
from app.services.lead_sourcing.providers.registry import (
    get_company_search_provider,
    get_contact_enrichment_provider,
    get_people_extraction_provider,
)

_logger = logging.getLogger(__name__)

STAGE_LABELS: dict[str, str] = {
    "idle": "En espera",
    "searching_companies": "Buscando empresas",
    "companies_found": "Empresas encontradas",
    "preparing_phantom": "Preparar extracción PhantomBuster",
    "phantom_ready": "Cola PhantomBuster lista",
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


def _build_discarded_leads(meta: dict, phantom_debug_raw: dict | None) -> list[DiscardedLeadRead]:
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

    if isinstance(phantom_debug_raw, dict):
        for item in phantom_debug_raw.get("discarded_rows_sample") or []:
            if not isinstance(item, dict):
                continue
            sample = item.get("sample") if isinstance(item.get("sample"), dict) else None
            name = None
            if isinstance(sample, dict):
                for k in ("fullName", "name", "firstName", "lastName"):
                    if sample.get(k):
                        name = str(sample.get(k))
                        break
            _add(
                name=name,
                company_name=None,
                reason=str(item.get("reason") or "parse_discard"),
                sample=sample,
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
    return companies + directories


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
        filtered_logs = _filter_stale_logs(
            raw_logs[-_READ_MAX_STAGE_LOGS:],
            agent_configured=bool(getenv("PHANTOMBUSTER_LINKEDIN_AGENT_ID")),
        )
        stage_logs = _sanitize_stage_logs(filtered_logs)
        if not phantom_experimental_enabled():
            stage_logs = [
                log
                for log in stage_logs
                if log.step not in PHANTOM_PIPELINE_STEPS
                and not is_phantom_related_message(f"{log.message} {log.step}")
            ]
        phantom_meta = meta.get("phantom_queue") if isinstance(meta.get("phantom_queue"), dict) else None
        phantom_queue = _phantom_queue_read(phantom_meta)
        if not extraction_stats and phantom_meta:
            extraction_stats = phantom_meta
        blocked_count = int((phantom_meta or {}).get("blocked_count") or 0)
        if trace:
            trace.step("read_pipeline.phantom_debug_before")
        phantom_debug = _phantom_debug_read_lite(
            meta.get("phantom_debug") if isinstance(meta.get("phantom_debug"), dict) else None
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
                targets_for_read = collect_target_companies(companies, phantom_meta)
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
        phantom_raw = meta.get("phantom_debug") if isinstance(meta.get("phantom_debug"), dict) else None
        discarded_leads = _build_discarded_leads(meta, phantom_raw)
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
            last_error=sanitize_panel_last_error(
                meta.get("last_error"),
                include_phantom=phantom_experimental_enabled(),
            ),
            pipeline_steps=list(MVP_PIPELINE_STEPS),
            extraction_stats=extraction_stats if isinstance(extraction_stats, dict) else None,
            extracted_companies_count=extracted_count,
            phantom_queue=phantom_queue,
            phantom_prepared=bool(phantom_meta and phantom_meta.get("prepared_at")),
            blocked_sources_count=blocked_count,
            phantom_debug=phantom_debug,
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


def _phantom_debug_read_lite(raw: dict | None) -> PhantomDebugRead | None:
    """Versión liviana para GET — omite previews/trazas enormes."""
    if not raw:
        return None
    lite = dict(raw)
    preview = lite.get("output_preview")
    if isinstance(preview, str) and len(preview) > 800:
        lite["output_preview"] = preview[:800] + "…"
    for heavy_list_key in ("poll_trace", "output_attempts", "discarded_rows_sample"):
        val = lite.get(heavy_list_key)
        if isinstance(val, list) and len(val) > 6:
            lite[heavy_list_key] = val[:6]
    for heavy_key in ("argument_sent", "input_summary", "launch_payload_sent", "auth_debug"):
        if heavy_key in lite:
            lite.pop(heavy_key, None)
    return _phantom_debug_read(lite)


def _phantom_debug_read(raw: dict | None) -> PhantomDebugRead | None:
    if not raw:
        return None
    keys = raw.get("output_keys")
    first_keys = raw.get("first_row_keys") if isinstance(raw.get("first_row_keys"), list) else []
    first_keys = [str(k) for k in first_keys if k is not None]
    output_keys = [str(k) for k in keys if k is not None] if isinstance(keys, list) else []
    try:
        return PhantomDebugRead(
            agent_id=raw.get("agent_id"),
            agent_name=raw.get("agent_name"),
            container_id=raw.get("container_id"),
            container_status=raw.get("container_status"),
            outcome=raw.get("outcome"),
            outcome_message=raw.get("outcome_message"),
            user_action=raw.get("user_action"),
            leads_count=int(raw.get("leads_count") or 0),
            rows_parsed=int(raw.get("rows_parsed") or 0),
            raw_rows_count=int(raw.get("raw_rows_count") or raw.get("rows_parsed") or 0),
            valid_rows_count=int(raw.get("valid_rows_count") or raw.get("leads_count") or 0),
            discarded_rows_count=int(raw.get("discarded_rows_count") or 0),
            discarded_rows_sample=raw.get("discarded_rows_sample")
            if isinstance(raw.get("discarded_rows_sample"), list)
            else [],
            first_row_keys=first_keys,
            first_row_sample=raw.get("first_row_sample") if isinstance(raw.get("first_row_sample"), dict) else None,
            parse_note=raw.get("parse_note"),
            session_cookie_in_agent=raw.get("session_cookie_in_agent"),
            argument_sent=raw.get("argument_sent") if isinstance(raw.get("argument_sent"), dict) else None,
            input_summary=raw.get("input_summary") if isinstance(raw.get("input_summary"), dict) else None,
            linkedin_query_exact=raw.get("linkedin_query_exact")
            if isinstance(raw.get("linkedin_query_exact"), str)
            else None,
            lead_score_audit=_audit_from_raw(raw.get("lead_score_audit")),
            parse_discards_count=int(raw.get("discarded_rows_count") or 0),
            company_searches=raw.get("company_searches")
            if isinstance(raw.get("company_searches"), list)
            else [],
            company_match_audit=raw.get("company_match_audit")
            if isinstance(raw.get("company_match_audit"), list)
            else [],
            company_search_runs=raw.get("company_search_runs")
            if isinstance(raw.get("company_search_runs"), list)
            else [],
            search_strategy=raw.get("search_strategy")
            if isinstance(raw.get("search_strategy"), str)
            else None,
            phantom_test_mode=bool(raw.get("phantom_test_mode"))
            if raw.get("phantom_test_mode") is not None
            else None,
            launch_response=raw.get("launch_response") if isinstance(raw.get("launch_response"), dict) else None,
            launch_payload_sent=raw.get("launch_payload_sent") if isinstance(raw.get("launch_payload_sent"), dict) else None,
            launch_uses_saved_agent_config=raw.get("launch_uses_saved_agent_config"),
            auth_debug=raw.get("auth_debug") if isinstance(raw.get("auth_debug"), dict) else None,
            output_source=raw.get("output_source"),
            output_endpoint=raw.get("output_endpoint"),
            launch_id=str(raw.get("launch_id")) if raw.get("launch_id") else None,
            leads_list_id=raw.get("leads_list_id"),
            manual_result_url=raw.get("manual_result_url"),
            has_result_object=raw.get("has_result_object"),
            s3_folders=raw.get("s3_folders") if isinstance(raw.get("s3_folders"), dict) else None,
            s3_urls_tried=raw.get("s3_urls_tried") if isinstance(raw.get("s3_urls_tried"), list) else [],
            result_urls_tried=raw.get("result_urls_tried")
            if isinstance(raw.get("result_urls_tried"), list)
            else [],
            output_attempts=raw.get("output_attempts")
            if isinstance(raw.get("output_attempts"), list)
            else [],
            output_keys=output_keys,
            output_preview=raw.get("output_preview"),
            container_exit_message=raw.get("container_exit_message"),
            container_poll_timeout=raw.get("container_poll_timeout"),
            poll_iterations=_safe_int(raw.get("poll_iterations")),
            poll_elapsed_sec=_safe_float(raw.get("poll_elapsed_sec")),
            poll_break=raw.get("poll_break"),
            poll_trace=raw.get("poll_trace") if isinstance(raw.get("poll_trace"), list) else [],
            step_completion=raw.get("step_completion"),
            agent_last_end_message=raw.get("agent_last_end_message"),
        )
    except Exception as e:
        return PhantomDebugRead(
            outcome="debug_parse_error",
            outcome_message=f"Debug PhantomBuster inválido, pipeline continúa: {e}",
            raw_rows_count=int(raw.get("raw_rows_count") or raw.get("rows_parsed") or 0),
            valid_rows_count=int(raw.get("valid_rows_count") or raw.get("leads_count") or 0),
            discarded_rows_count=int(raw.get("discarded_rows_count") or 0),
            first_row_keys=first_keys,
            output_keys=output_keys,
        )


def _filter_stale_logs(logs: list, *, agent_configured: bool) -> list:
    if not agent_configured or not logs:
        return logs
    stale_frag = "PHANTOMBUSTER_LINKEDIN_AGENT_ID"
    return [
        log
        for log in logs
        if not (
            isinstance(log, dict)
            and stale_frag in (log.get("message") or "")
            and log.get("event") == "error"
        )
    ]


def _phantom_queue_read(raw: dict | None) -> PhantomQueueRead | None:
    if not raw:
        return None
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    return PhantomQueueRead(
        prepared_at=raw.get("prepared_at"),
        icp_target_phrase=raw.get("icp_target_phrase"),
        role_hint=raw.get("role_hint"),
        location=raw.get("location"),
        icp_keywords=raw.get("icp_keywords") if isinstance(raw.get("icp_keywords"), list) else [],
        company_count=int(raw.get("company_count") or 0),
        directory_seed_count=int(raw.get("directory_seed_count") or 0),
        blocked_count=int(raw.get("blocked_count") or 0),
        total_items=int(raw.get("total_items") or len(items)),
        items=items,
    )


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
    queries = build_company_search_queries(campaign, profile=profile)
    query = profile.primary_target_phrase()
    meta["search_queries"] = queries
    meta["search_query"] = query
    meta["icp_target_phrase"] = query
    meta.pop("google_query", None)
    err = meta.get("last_error") or ""
    if "Google Search" in err or "Custom Search" in err:
        meta.pop("last_error", None)

    companies = _sanitize_companies(
        web.search_companies(campaign, limit=company_limit),
        campaign,
    )
    store.save_companies(row, companies)
    store.set_stage(row, "companies_found")
    meta["companies_found_at"] = meta.get("companies_found_at") or "ok"
    meta["icp_accounts_seeded"] = 0
    meta.pop("enrich_progress", None)
    meta.pop("last_error", None)
    store.save_meta(row, meta)
    db.commit()
    return len(companies)


def _seed_icp_account_leads(
    row,
    companies: list[CompanyCandidateRead],
    campaign: Campaign,
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
        domain = None
        if c.website_url:
            from app.services.lead_sourcing.providers.prospeo_enrichment import _website_domain

            domain = _website_domain(c.website_url)
        accounts.append(
            LeadCandidateRead(
                external_id=f"icp-account-{c.external_id or key[:32]}",
                provider="web_search",
                name=(c.name or "Empresa")[:255],
                company_name=(c.name or "Empresa")[:255],
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


def _run_prepare_phantom(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
) -> int:
    all_cos = store.load_companies(row)
    if not all_cos:
        raise ValueError("No hay empresas ni fuentes. Ejecutá «Buscar empresas» primero.")

    svc = PhantomBusterQueueService()
    queue_result = svc.prepare_queue(campaign, all_cos)
    queue_meta = queue_result.to_meta()
    meta["phantom_queue"] = queue_meta
    meta["extraction_stats"] = queue_meta
    store.set_stage(row, "phantom_ready")
    meta.pop("last_error", None)
    store.save_meta(row, meta)
    db.commit()
    return queue_meta.get("total_items") or 0


def _run_extract_companies(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
    *,
    company_limit: int,
    step: str,
) -> int:
    """Legacy alias — prepara cola PhantomBuster sin crawling directo."""
    return _run_prepare_phantom(db, row, meta, campaign)


def _run_people(
    db: Session,
    row,
    meta: dict,
    campaign: Campaign,
    *,
    people_limit: int,
) -> int:
    phantom = get_people_extraction_provider()
    companies = [c for c in store.load_companies(row) if c.result_kind == "company"]
    phantom_queue = meta.get("phantom_queue") if isinstance(meta.get("phantom_queue"), dict) else None
    if not companies and not phantom_queue:
        raise ValueError(
            "No hay empresas candidatas. Ejecutá buscar empresas y preparar PhantomBuster primero."
        )
    try:
        result = phantom.extract_people(
            campaign,
            companies,
            role_hint=campaign.target_role,
            limit=people_limit,
            phantom_queue=phantom_queue,
        )
    except ProviderAPIError as e:
        debug = getattr(e, "debug", None)
        if isinstance(debug, dict):
            meta["phantom_debug"] = debug
            store.save_meta(row, meta)
            db.commit()
        raise
    meta["phantom_debug"] = result.debug
    people = result.leads
    outcome = result.debug.get("outcome")
    if len(people) == 0:
        msg = result.debug.get("outcome_message") or "PhantomBuster devolvió 0 personas."
        append_log(
            meta,
            step="people",
            stage="leads_detected",
            event="skipped",
            message=msg,
            result_count=0,
        )
        meta["phantom_last_warning"] = msg
    else:
        meta.pop("phantom_last_warning", None)
    from app.services.lead_sourcing.phantom_runtime import is_phantom_test_mode

    targets = collect_target_companies(
        companies,
        phantom_queue,
        test_mode=is_phantom_test_mode(),
    )
    if isinstance(result.debug, dict) and result.debug.get("target_companies"):
        raw_tc = result.debug["target_companies"]
        if isinstance(raw_tc, list):
            targets = []
            for item in raw_tc:
                if isinstance(item, dict) and item.get("name"):
                    targets.append(
                        TargetCompany(
                            name=str(item["name"]),
                            url=item.get("url") if isinstance(item.get("url"), str) else None,
                            icp_relevance_score=int(item.get("icp_relevance_score") or 0),
                            canonical_key=str(item.get("canonical_key") or ""),
                        )
                    )
    meta["target_companies"] = [t.to_dict() for t in targets]
    if isinstance(result.debug, dict):
        if result.debug.get("company_searches"):
            meta["company_searches"] = result.debug["company_searches"]
        if result.debug.get("company_match_audit"):
            meta["company_match_audit"] = result.debug["company_match_audit"]
        if result.debug.get("input_summary"):
            summary = result.debug["input_summary"]
            if isinstance(summary, dict):
                if summary.get("phantom_companies_selected"):
                    meta["phantom_companies_selected"] = summary["phantom_companies_selected"]
                if summary.get("phantom_target_selection"):
                    meta["phantom_target_selection"] = summary["phantom_target_selection"]
    scored = [
        _score_lead(p, campaign, fit_threshold=row.fit_threshold, target_companies=targets)
        for p in people
    ]
    audit = _build_lead_score_audit(scored, fit_threshold=row.fit_threshold)
    meta["lead_score_audit"] = [a.model_dump(mode="json") for a in audit]
    meta["display_min_score"] = get_min_lead_display_score()
    parse_discards = result.debug.get("phantom_parse_discards")
    if isinstance(parse_discards, list):
        meta["phantom_parse_discards"] = parse_discards[:120]
    low_fit = sum(1 for p in scored if (p.fit_tier or "") == "low_fit")
    if isinstance(result.debug, dict):
        result.debug["lead_score_audit"] = meta["lead_score_audit"]
        result.debug["display_min_score"] = meta["display_min_score"]
        result.debug["low_fit_count"] = low_fit
    store.save_people(row, scored)
    store.set_stage(row, "leads_detected")
    append_log(
        meta,
        step="people",
        stage="leads_detected",
        event="completed",
        message=(
            f"{len(scored)} persona(s) visibles en panel "
            f"({low_fit} bajo fit, umbral import {row.fit_threshold}%, "
            f"etiqueta bajo fit desde {meta['display_min_score']}%)."
        ),
        result_count=len(scored),
    )
    meta.pop("last_error", None)
    meta.pop("phantom_discarded_low_icp", None)
    meta.pop("phantom_discarded_low_icp_count", None)
    store.save_meta(row, meta)
    db.commit()
    return len(scored)


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

    cached_ph = meta.get("prospeo_health") if company_offset > 0 else None
    companies, people, enrich_stats = run_mvp_company_enrichment(
        companies=companies,
        people=people,
        campaign=campaign,
        fit_threshold=threshold,
        log_fn=_log,
        company_offset=company_offset,
        max_companies_per_run=PROSPEO_ENRICH_BATCH_SIZE,
        on_checkpoint=_checkpoint,
        cached_prospeo_health=cached_ph if isinstance(cached_ph, dict) else None,
    )
    store.save_companies(row, companies)
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
    if substep == "companies":
        _commit_running(db, row, meta, step="companies", stage="searching_companies")
        return run_with_timeout(
            lambda: _run_companies(db, row, meta, campaign, company_limit=company_limit),
            stage_timeout_sec("companies"),
            "Web Search",
        )
    if substep in ("extract_companies", "prepare_phantom"):
        _commit_running(db, row, meta, step=substep, stage="preparing_phantom")
        return run_with_timeout(
            lambda: _run_prepare_phantom(db, row, meta, campaign),
            stage_timeout_sec("prepare_phantom"),
            "Preparar PhantomBuster",
        )
    if substep == "people":
        _commit_running(db, row, meta, step="people", stage="extracting_people")
        return run_with_timeout(
            lambda: _run_people(db, row, meta, campaign, people_limit=people_limit),
            stage_timeout_sec("people"),
            "PhantomBuster",
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
        store.save_people(row, [_score_lead(p, campaign) for p in people])
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
    row = store.get_or_create(db, campaign.id)
    meta = store.load_meta(row)
    meta = recover_stale_run(db, row, meta)

    substeps: list[str]
    if step == "full":
        substeps = mvp_substeps_full()
    else:
        substeps = [step]

    try:
        last_count = 0
        for sub in substeps:
            stage_before = row.stage
            try:
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
            except ValueError as e:
                if sub in ("extract_companies", "prepare_phantom") and step == "full" and "No hay empresas" in str(e):
                    append_log(
                        meta,
                        step=sub,
                        stage=stage_before,
                        event="skipped",
                        message=str(e),
                    )
                    store.save_meta(row, meta)
                    db.commit()
                    continue
                raise

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
