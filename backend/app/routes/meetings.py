"""CRUD de reuniones comerciales (Google Calendar)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.deps import get_campaign, get_company, get_prospect
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import MeetingStatus
from app.models.meeting import Meeting
from app.models.prospect import Prospect
from app.schemas.meeting import MeetingAcceptSuggestionRead, MeetingCreate, MeetingRead, MeetingUpdate
from app.services.meeting_booking import (
    CREATION_MANUAL,
    book_prospect_meeting,
    resolve_meeting_booking_from_prospect_thread,
)

router = APIRouter(tags=["meetings"])


def _serialize(m: Meeting, *, prospect: Prospect | None = None, campaign: Campaign | None = None) -> MeetingRead:
    data = MeetingRead.model_validate(m).model_dump()
    p = prospect
    c = campaign
    if p is None and m.prospect_id:
        p = getattr(m, "prospect", None)
    if c is None and m.campaign_id:
        c = getattr(m, "campaign", None)
    if p is not None:
        data["prospect_name"] = p.name
        data["prospect_company_name"] = p.company_name
        data["prospect_commercial_state"] = getattr(p, "commercial_state", None)
    if c is not None:
        data["campaign_name"] = c.name
    return MeetingRead.model_validate(data)


@router.get("/companies/{company_id}/meetings", response_model=list[MeetingRead])
def list_company_meetings(
    company_id: int,
    include_canceled: bool = Query(False, description="Incluir reuniones canceladas/rechazadas"),
    db: Session = Depends(get_db),
    _company: Company = Depends(get_company),
) -> list[MeetingRead]:
    stmt = select(Meeting).where(Meeting.company_id == company_id)
    if not include_canceled:
        stmt = stmt.where(Meeting.meeting_status != MeetingStatus.canceled.value)
    rows = db.scalars(
        stmt.options(selectinload(Meeting.prospect), selectinload(Meeting.campaign))
        .order_by(Meeting.scheduled_for.desc(), Meeting.id.desc())
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/campaigns/{campaign_id}/meetings", response_model=list[MeetingRead])
def list_campaign_meetings(
    campaign_id: int,
    include_canceled: bool = Query(False, description="Incluir reuniones canceladas/rechazadas"),
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> list[MeetingRead]:
    stmt = select(Meeting).where(Meeting.campaign_id == campaign_id)
    if not include_canceled:
        stmt = stmt.where(Meeting.meeting_status != MeetingStatus.canceled.value)
    rows = db.scalars(
        stmt.options(selectinload(Meeting.prospect), selectinload(Meeting.campaign))
        .order_by(Meeting.scheduled_for.asc(), Meeting.id.asc())
    ).all()
    return [_serialize(r) for r in rows]


@router.post("/campaigns/{campaign_id}/meetings", response_model=MeetingRead, status_code=201)
def create_meeting(
    campaign_id: int,
    body: MeetingCreate,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> MeetingRead:
    prospect = db.get(Prospect, body.prospect_id)
    if prospect is None or prospect.campaign_id != campaign_id:
        raise HTTPException(status_code=404, detail="Prospecto no encontrado en esta campaña")
    if prospect.company_id != campaign.company_id:
        raise HTTPException(status_code=409, detail="Prospecto no pertenece a la empresa de la campaña")
    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=body.scheduled_for,
        title=body.title.strip(),
        description=body.description.strip() if body.description else None,
        duration_minutes=body.duration_minutes,
        create_google_event=True,
        testing=False,
        creation_method=CREATION_MANUAL,
        require_scheduled_for=True,
    )
    if not result.get("meeting_id"):
        detail = result.get("calendar_error") or result.get("alternatives_reply") or "No se pudo agendar"
        raise HTTPException(status_code=400, detail=detail)
    row = db.get(Meeting, result["meeting_id"])
    if row is None:
        raise HTTPException(status_code=500, detail="No se pudo crear la reunión")
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.patch("/companies/{company_id}/meetings/{meeting_id}", response_model=MeetingRead)
def update_meeting(
    company_id: int,
    meeting_id: int,
    body: MeetingUpdate,
    db: Session = Depends(get_db),
    _company: Company = Depends(get_company),
) -> MeetingRead:
    m = db.get(Meeting, meeting_id)
    if m is None or m.company_id != company_id:
        raise HTTPException(status_code=404, detail="Reunión no encontrada")
    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        m.title = str(data["title"]).strip()
    if "description" in data:
        m.description = str(data["description"]).strip() if data["description"] else None
    if "scheduled_for" in data and data["scheduled_for"] is not None:
        m.scheduled_for = data["scheduled_for"]
    if "meeting_status" in data and data["meeting_status"] is not None:
        m.meeting_status = str(data["meeting_status"])
    if "timezone" in data and data["timezone"] is not None:
        m.timezone = str(data["timezone"])
    if "duration_minutes" in data and data["duration_minutes"] is not None:
        m.duration_minutes = int(data["duration_minutes"])
    db.commit()
    db.refresh(m)
    return _serialize(m)


@router.post(
    "/prospects/{prospect_id}/meetings/accept-suggestion",
    response_model=MeetingAcceptSuggestionRead,
    status_code=201,
)
def accept_ai_meeting_suggestion(
    prospect_id: int,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> MeetingAcceptSuggestionRead:
    """Agenda en Google Calendar usando el horario real del hilo (último inbound parseable)."""
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    if not prospect.meeting_suggestion_pending and (prospect.interest_level or "").lower() != "high":
        raise HTTPException(
            status_code=400,
            detail="La IA no dejó una sugerencia de reunión pendiente y el interés no es alto.",
        )

    slot, duration = resolve_meeting_booking_from_prospect_thread(
        db, prospect=prospect, campaign=campaign
    )
    if slot is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No hay un horario concreto en la conversación. "
                "Pedile al prospecto día y hora (ej. mañana a las 15) y volvé a intentar."
            ),
        )

    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        scheduled_for=slot,
        title=None,
        description="Agendada desde la conversación (horario propuesto por el prospecto).",
        duration_minutes=duration,
        create_google_event=True,
        testing=False,
        creation_method=CREATION_MANUAL,
        require_scheduled_for=True,
        check_availability=True,
    )
    if not result.get("meeting_id") or not result.get("calendar_created"):
        detail = (
            result.get("calendar_error")
            or result.get("alternatives_reply")
            or "No se pudo agendar en Google Calendar."
        )
        raise HTTPException(status_code=400, detail=detail)

    row = db.get(Meeting, result["meeting_id"])
    if row is None:
        raise HTTPException(status_code=500, detail="No se pudo crear la reunión")
    prospect.meeting_nudge_sent_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return MeetingAcceptSuggestionRead(
        meeting=_serialize(row),
        detail="Reunión agendada en Google Calendar.",
    )
