from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_campaign, get_company, get_prospect
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.schemas.operations import (
    AiDecisionEventRead,
    AutomationModeUpdate,
    EmergencyStopUpdate,
    OperationsOverviewRead,
    ProspectAiPauseUpdate,
)
from app.services import multichannel_sequence as mseq
from app.services.ai_decision_log import list_prospect_timeline, record_ai_decision
from app.services.operations_service import (
    apply_automation_mode,
    build_activity_feed,
    build_operations_overview,
)

router = APIRouter(prefix="/companies", tags=["operations"])


@router.get("/{company_id}/operations/overview", response_model=OperationsOverviewRead)
def get_operations_overview(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> OperationsOverviewRead:
    data = build_operations_overview(db, company_id)
    return OperationsOverviewRead.model_validate(data)


@router.get("/{company_id}/operations/activity-feed", response_model=list[AiDecisionEventRead])
def get_operations_activity_feed(
    company_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> list[AiDecisionEventRead]:
    lim = max(1, min(limit, 150))
    raw = build_activity_feed(db, company_id, limit=lim)
    return [
        AiDecisionEventRead(
            id=r["id"],
            at=datetime.fromisoformat(r["at"].replace("Z", "+00:00")),
            event_type=r["event_type"],
            decision=r["decision"],
            summary=r["summary"],
            campaign_id=r.get("campaign_id"),
            prospect_id=r.get("prospect_id"),
            confidence=r.get("confidence"),
            payload=r.get("payload"),
        )
        for r in raw
    ]


@router.get("/{company_id}/prospects/{prospect_id}/ai-timeline", response_model=list[AiDecisionEventRead])
def get_prospect_ai_timeline(
    company_id: int,
    prospect_id: int,
    limit: int = 80,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
    _company=Depends(get_company),
) -> list[AiDecisionEventRead]:
    if prospect.company_id != company_id:
        raise HTTPException(status_code=404, detail="Prospecto no encontrado")
    rows = list_prospect_timeline(db, company_id=company_id, prospect_id=prospect_id, limit=limit)
    return [
        AiDecisionEventRead(
            id=r.id,
            at=r.created_at,
            event_type=r.event_type,
            decision=r.decision,
            summary=r.summary,
            campaign_id=r.campaign_id,
            prospect_id=r.prospect_id,
            confidence=r.confidence,
            payload=r.payload,
        )
        for r in rows
    ]


@router.post("/{company_id}/operations/emergency-stop")
def post_emergency_stop(
    company_id: int,
    payload: EmergencyStopUpdate,
    db: Session = Depends(get_db),
    company: Company = Depends(get_company),
) -> dict:
    company.global_automation_stop = bool(payload.stop)
    camps = db.scalars(select(Campaign).where(Campaign.company_id == company_id)).all()
    for c in camps:
        c.automation_paused = bool(payload.stop)
        if payload.stop:
            mseq._append_log(
                c,
                "Parada de emergencia: automatización pausada desde Centro de Operaciones.",
                kind="info",
            )
    record_ai_decision(
        db,
        company_id=company_id,
        event_type="ops_control",
        decision="emergency_stop" if payload.stop else "emergency_resume",
        summary="Parada global de automatización activada"
        if payload.stop
        else "Automatización global reanudada",
        payload={"campaigns_affected": len(camps)},
        commit=False,
    )
    db.commit()
    return {
        "global_automation_stop": company.global_automation_stop,
        "campaigns_updated": len(camps),
    }


@router.patch("/{company_id}/campaigns/{campaign_id}/automation-mode")
def patch_campaign_automation_mode(
    company_id: int,
    campaign_id: int,
    payload: AutomationModeUpdate,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
    _company=Depends(get_company),
) -> dict:
    if campaign.company_id != company_id:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")
    apply_automation_mode(campaign, payload.mode)
    campaign.updated_at = datetime.now(UTC)
    mseq._append_log(
        campaign,
        f"Modo de automatización → {payload.mode} (Centro de Operaciones).",
        kind="info",
    )
    record_ai_decision(
        db,
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        event_type="ops_control",
        decision=f"mode_{payload.mode}",
        summary=f"Campaña en modo {payload.mode}",
        payload={
            "inbound_reply_mode": campaign.inbound_reply_mode,
            "outreach_email_mode": campaign.outreach_email_mode,
            "automation_paused": campaign.automation_paused,
        },
        commit=False,
    )
    db.commit()
    db.refresh(campaign)
    return {
        "campaign_id": campaign.id,
        "automation_mode": campaign.automation_mode,
        "inbound_reply_mode": campaign.inbound_reply_mode,
        "outreach_email_mode": campaign.outreach_email_mode,
        "automation_paused": campaign.automation_paused,
    }


@router.patch("/{company_id}/prospects/{prospect_id}/ai-pause")
def patch_prospect_ai_pause(
    company_id: int,
    prospect_id: int,
    payload: ProspectAiPauseUpdate,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
    _company=Depends(get_company),
) -> dict:
    if prospect.company_id != company_id:
        raise HTTPException(status_code=404, detail="Prospecto no encontrado")
    prospect.ai_paused = bool(payload.paused)
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign:
        mseq._append_log(
            campaign,
            f"{'Pausa' if payload.paused else 'Reanuda'} IA · {prospect.name or prospect.email}",
            kind="info",
        )
        record_ai_decision(
            db,
            company_id=prospect.company_id,
            campaign_id=campaign.id,
            prospect_id=prospect.id,
            event_type="ops_control",
            decision="ai_paused" if payload.paused else "ai_resumed",
            summary=f"Agente IA {'pausado' if payload.paused else 'activo'} para este prospecto",
            commit=False,
        )
    db.commit()
    return {"prospect_id": prospect.id, "ai_paused": prospect.ai_paused}


@router.post("/{company_id}/outreach-tasks/{task_id}/retry")
def retry_outreach_task(
    company_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> dict:
    task = db.get(OutreachTask, task_id)
    if task is None or task.company_id != company_id:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    task.status = "pending"
    task.due_at = datetime.now(UTC)
    db.commit()
    record_ai_decision(
        db,
        company_id=task.company_id,
        campaign_id=task.campaign_id,
        prospect_id=task.prospect_id,
        event_type="ops_control",
        decision="task_retry",
        summary=f"Reintento manual de tarea #{task.id} ({task.task_kind})",
        payload={"task_kind": task.task_kind},
        commit=True,
    )
    return {"task_id": task.id, "status": task.status, "due_at": task.due_at.isoformat()}
