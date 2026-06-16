"""Sincronización real Google Calendar ↔ reuniones Nexus."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.user import User
from app.schemas.google_calendar import GoogleCalendarSyncCreate, GoogleCalendarSyncRead
from app.services.google_calendar_sync import sync_calendar_events_for_seller

router = APIRouter(tags=["google-calendar"])


@router.post("/google-calendar/sync", response_model=GoogleCalendarSyncRead)
def post_google_calendar_sync(
    payload: GoogleCalendarSyncCreate,
    db: Session = Depends(get_db),
) -> GoogleCalendarSyncRead:
    company = db.get(Company, payload.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    user = db.get(User, payload.user_id)
    if user is None or int(user.company_id) != int(payload.company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")

    if payload.campaign_id is not None:
        camp = db.scalars(
            select(Campaign).where(
                Campaign.id == payload.campaign_id,
                Campaign.company_id == payload.company_id,
            )
        ).first()
        if camp is None:
            raise HTTPException(status_code=404, detail="Campaña no encontrada en esta empresa")
        if int(camp.seller_id) != int(payload.user_id):
            raise HTTPException(
                status_code=403,
                detail="El usuario no es el vendedor asignado a esta campaña.",
            )

    try:
        stats = sync_calendar_events_for_seller(
            db,
            company_id=payload.company_id,
            user_id=payload.user_id,
            campaign_id=payload.campaign_id,
            days_back=payload.days_back,
            days_forward=payload.days_forward,
            include_debug=payload.include_debug,
            debug_max_events=payload.debug_max_events,
            client_now_utc=payload.client_now_utc,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:800] if e.response is not None else str(e)
        raise HTTPException(status_code=502, detail=f"Google Calendar API: {detail}") from e

    db.commit()
    return GoogleCalendarSyncRead.model_validate(stats)
