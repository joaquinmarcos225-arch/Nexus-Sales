"""CRUD de reuniones comerciales (simulado; listo para Google Calendar)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_campaign, get_company, get_prospect
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.meeting import Meeting
from app.models.prospect import Prospect
from app.schemas.meeting import MeetingAcceptSuggestionRead, MeetingCreate, MeetingRead, MeetingUpdate
from app.services.meeting_booking import book_prospect_meeting

router = APIRouter(tags=["meetings"])


def _serialize(m: Meeting) -> MeetingRead:
    return MeetingRead.model_validate(m)


@router.get("/companies/{company_id}/meetings", response_model=list[MeetingRead])
def list_company_meetings(
    company_id: int,
    db: Session = Depends(get_db),
    _company: Company = Depends(get_company),
) -> list[MeetingRead]:
    rows = db.scalars(
        select(Meeting)
        .where(Meeting.company_id == company_id)
        .order_by(Meeting.scheduled_for.desc(), Meeting.id.desc())
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/campaigns/{campaign_id}/meetings", response_model=list[MeetingRead])
def list_campaign_meetings(
    campaign_id: int,
    db: Session = Depends(get_db),
    campaign: Campaign = Depends(get_campaign),
) -> list[MeetingRead]:
    rows = db.scalars(
        select(Meeting)
        .where(Meeting.campaign_id == campaign_id)
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
    )
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
    """Crea reunión al aceptar sugerencia (interés alto + mensaje orientado a agenda)."""
    campaign = db.get(Campaign, prospect.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    if not prospect.meeting_suggestion_pending and (prospect.interest_level or "").lower() != "high":
        raise HTTPException(
            status_code=400,
            detail="La IA no dejó una sugerencia de reunión pendente y el interés no es alto.",
        )

    result = book_prospect_meeting(
        db,
        campaign=campaign,
        prospect=prospect,
        title=f"Reunión · {prospect.name}",
        description="Registrada al confirmar intención de reunión desde la conversación.",
        create_google_event=True,
        testing=False,
    )
    row = db.get(Meeting, result["meeting_id"])
    if row is None:
        raise HTTPException(status_code=500, detail="No se pudo crear la reunión")
    prospect.meeting_nudge_sent_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    if result.get("calendar_created"):
        detail = "Reunión agendada en Google Calendar."
    elif result.get("calendar_error"):
        detail = f"Reunión registrada (pendiente Calendar): {result['calendar_error']}"
    else:
        detail = "Reunión registrada. Conectá Google Calendar para confirmar el evento."
    return MeetingAcceptSuggestionRead(meeting=_serialize(row), detail=detail)
