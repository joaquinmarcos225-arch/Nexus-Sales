"""Web Push: VAPID + suscripciones."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.services import push_notify

router = APIRouter(prefix="/notifications", tags=["notifications"])


class PushSubscribeRequest(BaseModel):
    app: str = Field(pattern="^(sales|support)$")
    endpoint: str = Field(min_length=8, max_length=1024)
    keys: dict[str, str]


class PushUnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=8, max_length=1024)


@router.get("/push/vapid-public")
def vapid_public(_user: User = Depends(get_current_user)) -> dict:
    key = push_notify.vapid_public_key()
    if not key:
        raise HTTPException(status_code=503, detail="Notificaciones push no configuradas")
    return {"public_key": key}


@router.post("/push/subscribe")
def push_subscribe(
    payload: PushSubscribeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    p256dh = str(payload.keys.get("p256dh") or "").strip()
    auth = str(payload.keys.get("auth") or "").strip()
    if not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Faltan keys p256dh/auth")
    try:
        push_notify.upsert_subscription(
            db,
            user=user,
            app=payload.app,
            endpoint=payload.endpoint,
            p256dh=p256dh,
            auth=auth,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
def push_unsubscribe(
    payload: PushUnsubscribeRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    ok = push_notify.delete_subscription(db, endpoint=payload.endpoint)
    db.commit()
    return {"ok": ok}
