"""Orquestación Lead Sourcing — pipeline desacoplado de proveedores."""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.schemas.lead_sourcing import (
    LeadCandidateRead,
    LeadSourcingImportRead,
    LeadSourcingPipelineRead,
    LeadSourcingStatusRead,
    PipelineRunRead,
    ProspeoHealthRead,
    ProviderStatusRead,
)
from app.schemas.prospect import ProspectCreate
from app.services.lead_sourcing import pipeline as pipe
from app.services.lead_sourcing import pipeline_store as store
from app.services.lead_sourcing.env_config import refresh_lead_sourcing_env
from app.services.lead_sourcing.mapper import to_prospect_create
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError
from app.services.lead_sourcing.diag_trace import DiagTrace
from app.services.lead_sourcing.mvp_pipeline import MVP_PIPELINE_STEPS
from app.services.lead_sourcing.providers.registry import (
    get_providers_status,
    mvp_pipeline_ready,
    pipeline_ready_for_campaign,
    pipeline_ready,
    prospeo_ready,
)

_logger = logging.getLogger(__name__)


def get_status(*, trace: DiagTrace | None = None) -> LeadSourcingStatusRead:
    t0 = time.perf_counter()
    if trace:
        trace.step("service.get_status_start")
    if trace:
        trace.step("get_providers_status_before")
    raw_providers = get_providers_status()
    if trace:
        trace.step("get_providers_status_after", count=len(raw_providers))
    providers = [ProviderStatusRead(**s.__dict__) for s in raw_providers]
    if trace:
        trace.step("providers_mapped")
    mvp_ready = mvp_pipeline_ready()
    if mvp_ready:
        msg = (
            "Pipeline MVP: ICP → Web Search → empresas candidatas → Prospeo (selectivo) → "
            "Nexus Outreach."
        )
    else:
        missing = [
            p.name
            for p in providers
            if p.name in ("web_search", "prospeo") and not p.configured
        ]
        msg = f"Configurá: {', '.join(missing) or 'Web Search y Prospeo'}"
    prospeo_health = None
    if any(p.name == "prospeo" and p.configured for p in providers):
        from app.services.lead_sourcing.prospeo_api_health import (
            fetch_prospeo_account_health,
            sanitize_prospeo_health_dict,
        )

        prospeo_health = ProspeoHealthRead.model_validate(
            sanitize_prospeo_health_dict(fetch_prospeo_account_health().to_dict())
        )
    result = LeadSourcingStatusRead(
        configured=mvp_ready,
        message=msg,
        providers=providers,
        pipeline=list(MVP_PIPELINE_STEPS),
        mvp_ready=mvp_ready,
        prospeo_health=prospeo_health,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _logger.info("[lead-sourcing] GET status mvp_ready=%s elapsed_ms=%s", mvp_ready, elapsed_ms)
    if trace:
        trace.step("service.get_status_done", elapsed_ms=elapsed_ms)
    return result


def get_pipeline(
    db: Session,
    campaign: Campaign,
    *,
    trace: DiagTrace | None = None,
) -> LeadSourcingPipelineRead:
    if trace:
        trace.step("service.get_pipeline_start", campaign_id=campaign.id)
    if trace:
        trace.step("read_pipeline_before")
    out = pipe.read_pipeline(db, campaign, trace=trace)
    if trace:
        trace.step("read_pipeline_after")
    return out


def run_pipeline_step(
    db: Session,
    campaign: Campaign,
    step: str,
    *,
    company_limit: int = 15,
    people_limit: int = 40,
    fit_threshold: int | None = None,
) -> PipelineRunRead:
    refresh_lead_sourcing_env()
    if not pipeline_ready_for_campaign(campaign):
        from app.services.campaign_market import campaign_is_b2c

        if campaign_is_b2c(campaign):
            raise ProviderNotConfiguredError(
                "B2C requiere Prospeo (PROSPEO_API_KEY). Web Search no es necesario."
            )
        raise ProviderNotConfiguredError(get_status().message)
    if fit_threshold is not None:
        row = store.get_or_create(db, campaign.id)
        row.fit_threshold = fit_threshold
        db.flush()
    return pipe.run_step(
        db,
        campaign,
        step,
        company_limit=company_limit,
        people_limit=people_limit,
    )


def _existing_external_ids(db: Session, campaign_id: int) -> set[str]:
    rows = db.scalars(
        select(Prospect.source_external_id).where(
            Prospect.campaign_id == campaign_id,
            Prospect.source_external_id.isnot(None),
        )
    ).all()
    return {str(r).strip() for r in rows if r}


def import_people(
    db: Session,
    campaign: Campaign,
    external_ids: list[str],
    *,
    persist_fn,
) -> LeadSourcingImportRead:
    row = store.get_or_create(db, campaign.id)
    people = store.load_people(row)
    by_id = {p.external_id: p for p in people}
    existing = _existing_external_ids(db, campaign.id)

    result = LeadSourcingImportRead()
    for eid in external_ids:
        eid = eid.strip()
        if not eid:
            continue
        c = by_id.get(eid)
        if c is None:
            result.skipped_missing += 1
            result.errors.append(f"{eid}: no está en el pipeline actual.")
            continue
        if eid in existing:
            result.skipped_duplicates += 1
            continue
        from app.services.lead_sourcing.contact_identity import (
            is_pipeline_contact,
            is_pipeline_generic_contact,
        )
        from app.services.lead_sourcing.prospecting_lead import (
            is_prospecting_importable_for_campaign,
        )

        if not is_pipeline_contact(c) and not is_pipeline_generic_contact(c):
            result.skipped_missing += 1
            result.errors.append(f"{eid}: no es un contacto persona real.")
            continue
        fit = int(getattr(row, "fit_threshold", None) or 70)
        if not is_prospecting_importable_for_campaign(c, campaign, fit_threshold=fit):
            result.skipped_missing += 1
            result.errors.append(
                f"{eid}: no cumple calidad de importación (rol/email o LinkedIn/score)."
            )
            continue
        from app.services.lead_sourcing.icp_import_gate import (
            icp_import_gate_reason,
        )

        gate_reason = icp_import_gate_reason(
            campaign_role=campaign.target_role,
            campaign_industry=campaign.target_industry,
            campaign_country=campaign.target_country,
            campaign_company_size=campaign.target_company_size,
            prospect_role=c.role,
            prospect_industry=c.industry,
            prospect_country=c.country,
            company_name=c.company_name,
            company_domain=c.company_domain,
            linkedin_url=c.linkedin_url,
            email=c.email,
            company_size=getattr(c, "company_size", None),
            employee_count=getattr(c, "employee_count", None),
            compatibility_score=c.compatibility_score,
            fit_threshold=fit,
        )
        if gate_reason:
            result.skipped_missing += 1
            result.errors.append(f"{eid}: ICP estricto — {gate_reason}")
            continue
        if not (c.linkedin_url or c.email):
            result.skipped_missing += 1
            result.errors.append(f"{eid}: sin email ni LinkedIn.")
            continue
        from app.services.crm import exclusions as crm_exclusions

        blocked = crm_exclusions.is_crm_excluded(
            db,
            campaign.company_id,
            email=c.email,
            company_name=c.company_name,
            company_website=c.company_website,
            company_domain=c.company_domain,
        )
        if blocked is not None:
            result.skipped_duplicates += 1
            continue
        try:
            from app.services.nexus_contact_cache import contact_delivered_to_tenant

            if contact_delivered_to_tenant(
                db,
                int(campaign.company_id),
                email=c.email,
                linkedin_url=c.linkedin_url,
                phone=getattr(c, "phone", None),
                whatsapp=getattr(c, "whatsapp", None) or getattr(c, "whatsapp_number", None),
            ):
                result.skipped_duplicates += 1
                continue
        except Exception:  # noqa: BLE001
            pass
        payload = to_prospect_create(c)
        try:
            p_row = persist_fn(db, campaign, payload)
            result.imported += 1
            result.prospect_ids.append(p_row.id)
            try:
                from app.services.lead_sourcing.cogs_runtime_metrics import record_import

                record_import(1)
            except Exception:  # noqa: BLE001
                pass
            try:
                from app.services.nexus_contact_cache import safe_upsert_from_prospect

                safe_upsert_from_prospect(
                    db,
                    p_row,
                    tenant_company_id=int(campaign.company_id),
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException) and exc.status_code == 409:
                result.skipped_duplicates += 1
                continue
            detail = getattr(exc, "detail", None) or str(exc)
            result.errors.append(f"{eid}: {detail}")

    return result


def _pipeline_people_for_outreach(row, companies: list, meta: dict, campaign: Campaign) -> list:
    from app.services.lead_sourcing.mvp_enrichment import (
        refresh_prospeo_people_scores,
        refresh_prospecting_contact_fields,
    )

    people = store.load_people(row)
    people = refresh_prospeo_people_scores(
        people,
        companies,
        fit_threshold=row.fit_threshold,
        icp_target_role=campaign.target_role,
        icp_target_industry=campaign.target_industry,
        icp_target_country=campaign.target_country,
        icp_target_company_size=campaign.target_company_size,
    )
    return refresh_prospecting_contact_fields(people)


def _load_outreach_profile(db: Session, campaign: Campaign, external_id: str):
    from app.services.lead_sourcing.lead_profile import build_lead_profile
    from app.schemas.mvp_outreach import AISDRInsightRead

    row = store.get_or_create(db, campaign.id)
    meta = store.load_meta(row)
    companies = store.load_companies(row)
    people = _pipeline_people_for_outreach(row, companies, meta, campaign)
    lead = next((p for p in people if p.external_id == external_id), None)
    if lead is None:
        raise ValueError(f"Lead {external_id} no encontrado en el pipeline.")

    cache = meta.get("lead_profiles_cache") if isinstance(meta.get("lead_profiles_cache"), dict) else {}
    entry = cache.get(external_id) if isinstance(cache.get(external_id), dict) else {}
    cached_ai = entry.get("ai_sdr")
    ai_sdr = None
    if isinstance(cached_ai, dict):
        try:
            ai_sdr = AISDRInsightRead.model_validate(cached_ai)
        except Exception:
            ai_sdr = None

    profile = build_lead_profile(
        lead,
        companies,
        ai_sdr=ai_sdr,
        fit_threshold=row.fit_threshold,
        icp_target_phrase=meta.get("icp_target_phrase") or meta.get("search_query"),
        campaign_target_industry=campaign.target_industry,
        campaign_target_role=campaign.target_role,
        campaign_target_country=campaign.target_country,
        campaign_target_company_size=campaign.target_company_size,
    )
    if not profile.ready_for_outreach:
        raise ValueError(
            profile.no_contact_message
            or "Lead sin canales mínimos para outreach (email corp + LinkedIn personal)."
        )
    playbook_raw = entry.get("playbook_state") if isinstance(entry.get("playbook_state"), dict) else None
    return row, meta, cache, entry, profile, playbook_raw


def _outreach_validation_failure(
    exc: Exception,
    *,
    pipeline: "LeadSourcingPipelineRead",
    testing: bool = False,
) -> "OutreachGenerateResultRead":
    from app.schemas.mvp_outreach import (
        OpenAIGenerationDebugRead,
        OutreachGenerateResultRead,
        OutreachValidationReportRead,
    )
    from app.services.lead_sourcing.sdr_playbook_outreach import SdrDraftValidationError, SdrResponseParseError

    if isinstance(exc, SdrDraftValidationError):
        report = OutreachValidationReportRead.model_validate(exc.report)
        return OutreachGenerateResultRead(
            ok=False,
            message="Borrador rechazado por validación",
            detail=report.summary,
            validation=report,
            testing=testing,
            openai_configured=True,
            pipeline=pipeline,
        )
    if isinstance(exc, SdrResponseParseError):
        salvage = (exc.salvage_body or "").strip()
        report = OutreachValidationReportRead(
            valid=False,
            summary=exc.message,
            issues=[exc.debug.get("parse_error") or exc.message],
            rejected_body=salvage,
            channel=exc.debug.get("channel"),
            step_day=exc.debug.get("step_day"),
            generation_debug=OpenAIGenerationDebugRead.model_validate(exc.debug),
        )
        return OutreachGenerateResultRead(
            ok=False,
            message="Error al parsear respuesta OpenAI" if not salvage else "Respuesta OpenAI sin JSON válido (texto recuperado)",
            detail=report.summary,
            validation=report,
            testing=testing,
            openai_configured=True,
            pipeline=pipeline,
        )
    raise exc


def generate_next_playbook_touch(
    db: Session,
    campaign: Campaign,
    external_id: str,
    *,
    regenerate: bool = False,
) -> "OutreachGenerateResultRead":
    from fastapi import HTTPException

    from app.schemas.mvp_outreach import OutreachGenerateResultRead
    from app.services.lead_sourcing.mvp_outreach_playbook import openai_configured
    from app.services.lead_sourcing.nexus_outreach_mvp import (
        generate_ai_sdr_insight,
        generate_next_playbook_touch as gen_touch,
    )
    from app.services.lead_sourcing.sdr_playbook_outreach import SdrDraftValidationError, SdrResponseParseError

    if not openai_configured():
        return OutreachGenerateResultRead(
            ok=False,
            message="OpenAI no configurado",
            detail="OPENAI_API_KEY no configurada en el backend.",
            openai_configured=False,
            pipeline=get_pipeline(db, campaign),
        )

    row, meta, cache, entry, profile, playbook_raw = _load_outreach_profile(
        db, campaign, external_id
    )
    ai_sdr = profile.ai_sdr
    try:
        touch, new_state = gen_touch(
            db,
            campaign,
            profile,
            playbook_raw,
            regenerate_last=regenerate,
        )
    except (SdrDraftValidationError, SdrResponseParseError) as exc:
        return _outreach_validation_failure(
            exc, pipeline=get_pipeline(db, campaign), testing=False
        )
    except HTTPException as exc:
        pipeline = get_pipeline(db, campaign)
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("issues"):
            from app.schemas.mvp_outreach import OutreachValidationReportRead

            report = OutreachValidationReportRead.model_validate(detail)
            return OutreachGenerateResultRead(
                ok=False,
                message="Error al generar borrador",
                detail=report.summary,
                validation=report,
                openai_configured=True,
                pipeline=pipeline,
            )
        return OutreachGenerateResultRead(
            ok=False,
            message="Error al generar borrador",
            detail=str(detail) if detail else str(exc),
            openai_configured=True,
            pipeline=pipeline,
        )
    except ValueError as exc:
        return OutreachGenerateResultRead(
            ok=False,
            message=str(exc),
            detail=str(exc),
            openai_configured=True,
            pipeline=get_pipeline(db, campaign),
        )

    if not ai_sdr:
        ai_sdr = generate_ai_sdr_insight(db, campaign, profile)

    cache[external_id] = {
        **entry,
        "playbook_state": new_state,
        "ai_sdr": ai_sdr.model_dump(),
    }
    meta["lead_profiles_cache"] = cache
    store.save_meta(row, meta)
    db.commit()
    pipeline = get_pipeline(db, campaign)
    return OutreachGenerateResultRead(
        ok=True,
        message=f"Toque Día {touch.day} · {touch.channel} generado.",
        touch=touch,
        testing=False,
        openai_configured=True,
        pipeline=pipeline,
    )


def generate_testing_outreach_draft(
    db: Session,
    campaign: Campaign,
    external_id: str,
    *,
    channel: str,
) -> "OutreachGenerateResultRead":
    from fastapi import HTTPException

    from app.schemas.mvp_outreach import OutreachGenerateResultRead
    from app.services.lead_sourcing.sdr_playbook_outreach import SdrDraftValidationError, SdrResponseParseError
    from app.services.lead_sourcing.mvp_outreach_playbook import openai_configured
    from app.services.lead_sourcing.nexus_outreach_mvp import generate_testing_playbook_draft

    if not openai_configured():
        return OutreachGenerateResultRead(
            ok=False,
            message="OpenAI no configurado",
            detail="OPENAI_API_KEY no configurada en el backend.",
            openai_configured=False,
            pipeline=get_pipeline(db, campaign),
        )

    _row, _meta, _cache, _entry, profile, playbook_raw = _load_outreach_profile(
        db, campaign, external_id
    )
    del _row, _meta, _cache, _entry
    try:
        touch = generate_testing_playbook_draft(
            db,
            campaign,
            profile,
            playbook_raw,
            channel=channel,
        )
    except (SdrDraftValidationError, SdrResponseParseError) as exc:
        return _outreach_validation_failure(
            exc, pipeline=get_pipeline(db, campaign), testing=True
        )
    except HTTPException as exc:
        return OutreachGenerateResultRead(
            ok=False,
            message="Error al generar borrador de prueba",
            detail=str(exc.detail) if exc.detail else str(exc),
            openai_configured=True,
            testing=True,
            pipeline=get_pipeline(db, campaign),
        )
    except ValueError as exc:
        return OutreachGenerateResultRead(
            ok=False,
            message=str(exc),
            detail=str(exc),
            openai_configured=True,
            testing=True,
            pipeline=get_pipeline(db, campaign),
        )

    pipeline = get_pipeline(db, campaign)
    label = {"email": "Email", "linkedin": "LinkedIn", "whatsapp": "WhatsApp"}.get(
        channel, channel
    )
    return OutreachGenerateResultRead(
        ok=True,
        message=f"Borrador de prueba · {label} (Día {touch.day}) — no modifica el playbook.",
        touch=touch,
        testing=True,
        openai_configured=True,
        pipeline=pipeline,
    )


def generate_full_playbook_preview(
    db: Session,
    campaign: Campaign,
    external_id: str,
) -> "PlaybookFullPreviewRead":
    from app.schemas.mvp_outreach import PlaybookFullPreviewRead
    from app.services.lead_sourcing.nexus_outreach_mvp import generate_full_playbook_preview as _preview

    _row, _meta, _cache, _entry, profile, _playbook_raw = _load_outreach_profile(
        db, campaign, external_id
    )
    del _row, _meta, _cache, _entry, _playbook_raw
    return _preview(db, campaign, profile)


def reset_playbook_sequence(
    db: Session,
    campaign: Campaign,
    external_id: str,
) -> LeadSourcingPipelineRead:
    row, meta, cache, entry, _profile, _playbook_raw = _load_outreach_profile(
        db, campaign, external_id
    )
    entry["playbook_state"] = {
        "completed": [],
        "paused": False,
        "pause_reason": None,
    }
    cache[external_id] = entry
    meta["lead_profiles_cache"] = cache
    store.save_meta(row, meta)
    db.commit()
    return get_pipeline(db, campaign)


def regenerate_outreach(
    db: Session,
    campaign: Campaign,
    external_id: str,
) -> "OutreachGenerateResultRead":
    return generate_next_playbook_touch(db, campaign, external_id, regenerate=True)


def generate_ready_outreach_drafts(
    db: Session,
    campaign: Campaign,
) -> LeadSourcingPipelineRead:
    """Legacy: no auto-genera en batch — usar generate_next_playbook_touch por lead."""
    return get_pipeline(db, campaign)


def patch_outreach_message(
    db: Session,
    campaign: Campaign,
    external_id: str,
    *,
    channel: str,
    slot: str,
    subject: str | None,
    body: str,
) -> LeadSourcingPipelineRead:
    del slot
    row = store.get_or_create(db, campaign.id)
    meta = store.load_meta(row)
    cache = meta.get("lead_profiles_cache") if isinstance(meta.get("lead_profiles_cache"), dict) else {}
    entry = cache.get(external_id) if isinstance(cache.get(external_id), dict) else {}
    state = entry.get("playbook_state") if isinstance(entry.get("playbook_state"), dict) else {}
    completed = state.get("completed") if isinstance(state.get("completed"), list) else []
    if not completed:
        raise ValueError("No hay borrador playbook para editar. Generá un toque primero.")
    last = dict(completed[-1]) if isinstance(completed[-1], dict) else {}
    last["body"] = body
    last["edited"] = True
    if channel == "email":
        last["subject"] = subject
    completed[-1] = last
    state["completed"] = completed
    entry["playbook_state"] = state
    cache[external_id] = entry
    meta["lead_profiles_cache"] = cache
    store.save_meta(row, meta)
    db.commit()
    return get_pipeline(db, campaign)
