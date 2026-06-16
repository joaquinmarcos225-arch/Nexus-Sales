from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.deps import get_company
from app.models.outreach_task import OutreachTask
from app.schemas.outreach_tasks import OutreachTaskRead
from app.services.recommended_actions import load_curated_tasks

router = APIRouter(prefix="/companies", tags=["outreach-tasks"])

_TASK_KIND_LABELS: dict[str, str] = {
    "scheduled_followup": "Seguimiento programado",
    "deferred_sequence_resume": "Re-contacto tras postergación",
    "review_inbound": "Revisar respuesta del prospecto",
    "awaiting_reply": "Esperar réplica del prospecto",
    "hot_lead": "Lead con alto interés — actuar",
}


def _task_to_read(
    t: OutreachTask,
    *,
    headline: str = "",
    reason: str = "",
    suggested_action: str = "",
    priority_score: int = 0,
) -> OutreachTaskRead:
    base = OutreachTaskRead.model_validate(t)
    camp = t.campaign
    pr = t.prospect
    return base.model_copy(
        update={
            "campaign_name": camp.name if camp else "—",
            "prospect_name": (pr.name if pr else "") or "",
            "prospect_company": (pr.company_name if pr else "") or "",
            "action_label": _TASK_KIND_LABELS.get(t.task_kind, "Tarea operativa"),
            "headline": headline or (t.title or ""),
            "reason": reason,
            "suggested_action": suggested_action,
            "priority_score": priority_score,
        }
    )


@router.get("/{company_id}/outreach-tasks", response_model=list[OutreachTaskRead])
def list_outreach_tasks(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    status: str | None = Query(
        default=None,
        description="Filtrar por estado: pending, done, cancelled",
    ),
    campaign_id: int | None = Query(
        default=None,
        description="Filtrar por campaña",
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=120,
        description="Máximo de tareas curadas (por defecto 3 en campaña, 5 en empresa)",
    ),
) -> list[OutreachTask]:
    if status in (None, "pending"):
        if limit is not None:
            eff_limit = limit
        else:
            eff_limit = 3 if campaign_id is not None else 5
        curated = load_curated_tasks(
            db,
            company_id=company_id,
            campaign_id=campaign_id,
            limit=eff_limit,
        )
        return [
            _task_to_read(
                t,
                headline=headline,
                reason=reason,
                suggested_action=suggested,
                priority_score=score,
            )
            for t, headline, reason, suggested, _lbl, score in curated
        ]

    q = (
        select(OutreachTask)
        .where(OutreachTask.company_id == company_id)
        .options(selectinload(OutreachTask.campaign), selectinload(OutreachTask.prospect))
    )
    if status:
        q = q.where(OutreachTask.status == status)
    if campaign_id is not None:
        q = q.where(OutreachTask.campaign_id == campaign_id)
    q = q.order_by(OutreachTask.due_at.asc(), OutreachTask.id.asc())
    rows = db.scalars(q).unique().all()
    return [_task_to_read(t) for t in rows]
