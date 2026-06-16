"""Lead Sourcing Engine — pipeline Web Search → Company Extraction → PhantomBuster → Prospeo."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_campaign
from app.models.campaign import Campaign
from app.routes.prospects import _persist_new_prospect
from app.schemas.lead_sourcing import (
    LeadSourcingImportRead,
    LeadSourcingImportRequest,
    LeadSourcingPipelineRead,
    LeadSourcingStatusRead,
    PipelineRunRead,
    PipelineRunRequest,
)
from app.schemas.mvp_outreach import (
    OutreachEditRequest,
    OutreachGenerateRequest,
    OutreachGenerateResultRead,
    OutreachTestingGenerateRequest,
    PlaybookFullPreviewRead,
)
from app.services.lead_sourcing import service
from app.services.lead_sourcing.diag_trace import DiagTrace
from app.services.lead_sourcing.providers.base import ProviderAPIError, ProviderNotConfiguredError

router = APIRouter(tags=["lead-sourcing"])
_logger = logging.getLogger(__name__)


@router.get("/lead-sourcing/ping")
def lead_sourcing_ping() -> dict[str, bool]:
    """Diagnóstico: responde al instante si el backend recibe HTTP."""
    trace = DiagTrace("GET /lead-sourcing/ping")
    trace.done(ok=True)
    return {"ok": True}


@router.get("/lead-sourcing/status", response_model=LeadSourcingStatusRead)
def lead_sourcing_status() -> LeadSourcingStatusRead:
    trace = DiagTrace("GET /lead-sourcing/status")
    try:
        trace.step("handler_start")
        out = service.get_status(trace=trace)
        trace.done(configured=out.configured, providers=len(out.providers))
        return out
    except Exception as e:
        trace.fail(e)
        raise


@router.get(
    "/campaigns/{campaign_id}/lead-sourcing/pipeline",
    response_model=LeadSourcingPipelineRead,
)
def get_pipeline(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> LeadSourcingPipelineRead:
    trace = DiagTrace("GET /campaigns/{id}/lead-sourcing/pipeline", campaign_id=campaign_id)
    try:
        trace.step("deps_resolved", campaign_db_id=campaign.id)
        out = service.get_pipeline(db, campaign, trace=trace)
        trace.done(stage=out.stage, companies=out.companies_count, people=out.people_count)
        return out
    except Exception as e:
        trace.fail(e)
        raise


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/run",
    response_model=PipelineRunRead,
)
def run_pipeline(
    campaign_id: int,
    body: PipelineRunRequest,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> PipelineRunRead:
    try:
        return service.run_pipeline_step(
            db,
            campaign,
            body.step,
            company_limit=body.company_limit,
            people_limit=body.people_limit,
            fit_threshold=body.fit_threshold,
        )
    except ProviderNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ProviderAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/import",
    response_model=LeadSourcingImportRead,
)
def import_leads(
    campaign_id: int,
    body: LeadSourcingImportRequest,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> LeadSourcingImportRead:
    def _persist(db_sess: Session, camp: Campaign, payload):
        from fastapi import HTTPException as HE

        try:
            return _persist_new_prospect(db_sess, camp, payload)
        except HE:
            raise

    try:
        result = service.import_people(db, campaign, body.external_ids, persist_fn=_persist)
        db.commit()
        return result
    except ProviderNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/outreach/generate-ready",
    response_model=LeadSourcingPipelineRead,
)
def generate_ready_outreach_drafts_route(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> LeadSourcingPipelineRead:
    del campaign_id
    try:
        return service.generate_ready_outreach_drafts(db, campaign)
    except ProviderAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/profiles/{external_id}/outreach/generate",
    response_model=OutreachGenerateResultRead,
)
def generate_profile_outreach(
    campaign_id: int,
    external_id: str,
    body: OutreachGenerateRequest,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> OutreachGenerateResultRead:
    del campaign_id
    try:
        if body.regenerate:
            return service.regenerate_outreach(db, campaign, external_id)
        return service.generate_next_playbook_touch(db, campaign, external_id, regenerate=False)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ProviderAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/profiles/{external_id}/outreach/reset",
    response_model=LeadSourcingPipelineRead,
)
def reset_profile_outreach_sequence(
    campaign_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> LeadSourcingPipelineRead:
    del campaign_id
    try:
        return service.reset_playbook_sequence(db, campaign, external_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/profiles/{external_id}/outreach/test-generate",
    response_model=OutreachGenerateResultRead,
)
def generate_profile_outreach_test(
    campaign_id: int,
    external_id: str,
    body: OutreachTestingGenerateRequest,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> OutreachGenerateResultRead:
    del campaign_id
    try:
        return service.generate_testing_outreach_draft(
            db, campaign, external_id, channel=body.channel
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ProviderAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post(
    "/campaigns/{campaign_id}/lead-sourcing/profiles/{external_id}/outreach/test-playbook-preview",
    response_model=PlaybookFullPreviewRead,
)
def generate_profile_playbook_preview(
    campaign_id: int,
    external_id: str,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> PlaybookFullPreviewRead:
    del campaign_id
    try:
        return service.generate_full_playbook_preview(db, campaign, external_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ProviderAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.patch(
    "/campaigns/{campaign_id}/lead-sourcing/profiles/{external_id}/outreach",
    response_model=LeadSourcingPipelineRead,
)
def edit_profile_outreach(
    campaign_id: int,
    external_id: str,
    body: OutreachEditRequest,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> LeadSourcingPipelineRead:
    try:
        return service.patch_outreach_message(
            db,
            campaign,
            external_id,
            channel=body.channel,
            slot=body.slot,
            subject=body.subject,
            body=body.body,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
