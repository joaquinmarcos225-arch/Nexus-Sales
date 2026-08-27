"""API Nexus Support."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.models.support_ticket import SupportThread
from app.models.user import User
from app.services.support import (
    add_message,
    get_or_create_user_thread,
    get_thread_for_ops,
    is_nexus_support_ops,
    list_ops_threads,
    serialize_thread,
    set_thread_status,
)

router = APIRouter(tags=["support"])


class SupportMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class SupportThreadStatusPatch(BaseModel):
    status: str = Field(pattern="^(open|resolved)$")


class ProviderBalancePatch(BaseModel):
    balance_usd: float = Field(ge=0)
    notes: str | None = Field(default=None, max_length=512)


def _require_ops(user: User) -> None:
    if not is_nexus_support_ops(user):
        raise HTTPException(status_code=403, detail="Solo el equipo Nexus Support puede ver esta bandeja.")


def _load_thread(db: Session, thread_id: int) -> SupportThread:
    row = db.scalars(
        select(SupportThread)
        .options(selectinload(SupportThread.company), selectinload(SupportThread.messages))
        .where(SupportThread.id == thread_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    return row


@router.get("/support/me")
def support_me(user: User = Depends(get_current_user)) -> dict:
    return {"is_support_ops": is_nexus_support_ops(user)}


@router.get("/support/thread")
def get_my_support_thread(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    thread = get_or_create_user_thread(db, company_id=int(user.company_id), user=user)
    db.commit()
    return serialize_thread(_load_thread(db, int(thread.id)))


@router.post("/support/messages")
def post_my_support_message(
    payload: SupportMessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    thread = get_or_create_user_thread(db, company_id=int(user.company_id), user=user)
    if int(thread.opened_by_user_id) != int(user.id):
        raise HTTPException(status_code=403, detail="Hilo de soporte inválido")
    try:
        add_message(db, thread=thread, author=user, role="user", body=payload.text)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    db.commit()
    return serialize_thread(_load_thread(db, int(thread.id)))


@router.get("/support/ops/threads")
def ops_list_threads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_ops(user)
    threads = list_ops_threads(db)
    return {
        "items": [serialize_thread(t, include_messages=False) for t in threads],
        "total": len(threads),
    }


@router.get("/support/ops/observability")
def ops_observability(
    refresh_prospeo: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Salud global, colas, límites y costos estimados; exclusivo de Nexus Support."""
    _require_ops(user)
    from app.services.support_observability import build_support_observability

    return build_support_observability(db, refresh_prospeo=refresh_prospeo)


@router.get("/support/ops/capacity")
def ops_capacity(
    refresh: bool = False,
    proposed_grant: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Calculadora de capacidad: saldos proveedor → secuencias netas disponibles."""
    _require_ops(user)
    from app.services.capacity_calculator import build_capacity_report

    report = build_capacity_report(db, refresh=refresh, proposed_grant=proposed_grant)
    db.commit()
    return report


@router.patch("/support/ops/capacity/balances/{provider}")
def ops_patch_provider_balance(
    provider: str,
    payload: ProviderBalancePatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_ops(user)
    from app.services.capacity_calculator import build_capacity_report, patch_provider_balance_manual

    try:
        patch_provider_balance_manual(
            db,
            provider=provider,
            balance_usd=payload.balance_usd,
            notes=payload.notes,
            updated_by_user_id=int(user.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return build_capacity_report(db, refresh=False)


@router.get("/support/ops/threads/{thread_id}")
def ops_get_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_ops(user)
    thread = get_thread_for_ops(db, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    return serialize_thread(thread)


@router.patch("/support/ops/threads/{thread_id}/status")
def ops_set_thread_status(
    thread_id: int,
    payload: SupportThreadStatusPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_ops(user)
    thread = get_thread_for_ops(db, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    try:
        set_thread_status(db, thread=thread, status=payload.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    db.commit()
    return serialize_thread(get_thread_for_ops(db, thread_id))


@router.post("/support/ops/threads/{thread_id}/messages")
def ops_reply_thread(
    thread_id: int,
    payload: SupportMessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _require_ops(user)
    thread = get_thread_for_ops(db, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Hilo no encontrado")
    add_message(db, thread=thread, author=user, role="support", body=payload.text)
    db.commit()
    thread = get_thread_for_ops(db, thread_id)
    return serialize_thread(thread)
