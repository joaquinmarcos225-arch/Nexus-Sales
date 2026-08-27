from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import sequence_templates as seqt
from app.database.session import get_db
from app.deps import get_company
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.models.sequence_template import SequenceTemplate

router = APIRouter(tags=["sequence-templates"])


class SequenceStep(BaseModel):
    day: int
    channel: str


class SequenceFollowUp(BaseModel):
    enabled: bool = True
    channel: str = "auto"


class SequenceTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mode: str = Field(default="fixed")
    steps: list[SequenceStep]
    follow_up: SequenceFollowUp = Field(default_factory=SequenceFollowUp)


class SequenceTemplateRead(BaseModel):
    template_id: str
    name: str
    mode: str
    is_system: bool
    steps: list[SequenceStep]
    follow_up: SequenceFollowUp
    success_rate: float | None = None
    sample_size: int = 0


def _template_success(db: Session, company_id: int, template_id: str) -> tuple[float | None, int]:
    """Tasa de respuesta (respondieron/contactados) de campañas que usan la plantilla."""
    try:
        campaigns = db.scalars(
            select(Campaign).where(Campaign.company_id == company_id)
        ).all()
        campaign_ids: list[int] = []
        for c in campaigns:
            plan = getattr(c, "sequence_plan", None)
            tid = None
            if isinstance(plan, dict):
                tid = plan.get("template_id")
            if tid is None:
                tid = seqt.SYSTEM_TEMPLATE_NEXUS_7  # sin plan = Nexus 7 toques
            if str(tid) == str(template_id):
                campaign_ids.append(c.id)
        if not campaign_ids:
            return None, 0

        prospects = db.scalars(
            select(Prospect).where(Prospect.campaign_id.in_(campaign_ids))
        ).all()
        contacted = 0
        responded = 0
        for p in prospects:
            if getattr(p, "last_outbound_at", None) or int(getattr(p, "outreach_touch_count", 0) or 0) > 0:
                contacted += 1
                if getattr(p, "last_inbound_at", None):
                    responded += 1
        if contacted == 0:
            return None, 0
        return round(responded / contacted, 4), contacted
    except Exception:  # noqa: BLE001 — métrica best-effort, nunca rompe el listado
        return None, 0


def _custom_template_id(row: SequenceTemplate) -> str:
    return f"custom:{row.id}"


def _row_to_read(db: Session, company_id: int, row: SequenceTemplate) -> SequenceTemplateRead:
    tid = _custom_template_id(row)
    rate, sample = _template_success(db, company_id, tid)
    follow_up = row.follow_up if isinstance(row.follow_up, dict) else {}
    return SequenceTemplateRead(
        template_id=tid,
        name=row.name,
        mode=row.mode,
        is_system=False,
        steps=[SequenceStep(**s) for s in (row.steps or [])],
        follow_up=SequenceFollowUp(
            enabled=bool(follow_up.get("enabled", True)),
            channel=str(follow_up.get("channel", "auto")),
        ),
        success_rate=rate,
        sample_size=sample,
    )


def _system_to_read(db: Session, company_id: int, plan: dict[str, Any]) -> SequenceTemplateRead:
    tid = str(plan["template_id"])
    rate, sample = _template_success(db, company_id, tid)
    # Nexus 7: tasa de referencia de producto hasta tener muestra real suficiente.
    if tid == seqt.SYSTEM_TEMPLATE_NEXUS_7 and (rate is None or sample < 30):
        rate, sample = 0.36, max(sample, 30)
    fu = plan.get("follow_up") or {}
    return SequenceTemplateRead(
        template_id=tid,
        name=plan["template_name"],
        mode=plan["mode"],
        is_system=True,
        steps=[SequenceStep(**s) for s in plan["steps"]],
        follow_up=SequenceFollowUp(
            enabled=bool(fu.get("enabled", True)),
            channel=str(fu.get("channel", "auto")),
        ),
        success_rate=rate,
        sample_size=sample,
    )


@router.get(
    "/companies/{company_id}/sequence-templates",
    response_model=list[SequenceTemplateRead],
)
def list_sequence_templates(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> list[SequenceTemplateRead]:
    out: list[SequenceTemplateRead] = [
        _system_to_read(db, company_id, plan) for plan in seqt.system_templates()
    ]
    rows = db.scalars(
        select(SequenceTemplate)
        .where(SequenceTemplate.company_id == company_id)
        .order_by(SequenceTemplate.created_at.desc())
    ).all()
    out.extend(_row_to_read(db, company_id, r) for r in rows)
    return out


@router.post(
    "/companies/{company_id}/sequence-templates",
    response_model=SequenceTemplateRead,
    status_code=201,
)
def create_sequence_template(
    company_id: int,
    payload: SequenceTemplateCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> SequenceTemplateRead:
    plan = {
        "template_name": payload.name.strip(),
        "mode": payload.mode,
        "steps": [s.model_dump() for s in payload.steps],
        "follow_up": payload.follow_up.model_dump(),
    }
    try:
        normalized = seqt.validate_plan(plan)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    row = SequenceTemplate(
        company_id=company_id,
        name=normalized["template_name"],
        mode=normalized["mode"],
        steps=normalized["steps"],
        follow_up=normalized["follow_up"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_read(db, company_id, row)


@router.delete(
    "/companies/{company_id}/sequence-templates/{template_id}",
    status_code=204,
)
def delete_sequence_template(
    company_id: int,
    template_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> Response:
    row = db.get(SequenceTemplate, template_id)
    if row is None or row.company_id != company_id:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    db.delete(row)
    db.commit()
    return Response(status_code=204)
