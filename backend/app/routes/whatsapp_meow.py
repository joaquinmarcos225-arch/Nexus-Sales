"""Rutas usuario: sesiones WhatsApp Web (whatsmeow)."""

from __future__ import annotations

import base64
import io
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_company_for_user
from app.core.tenant_scope import assert_user_in_company
from app.database.session import get_db
from app.models.user import User
from app.models.whatsapp_meow import WhatsappMeowSession
from app.schemas.whatsapp_meow import (
    WhatsappMeowEngineStatusRead,
    WhatsappMeowMessageRead,
    WhatsappMeowSendBody,
    WhatsappMeowSessionCreate,
    WhatsappMeowSessionRead,
)
from app.services.whatsapp_meow_engine_probe import probe_meow_engine_status
from app.models.company import Company
from app.services import whatsapp_meow_service as svc
from app.services.whatsapp_commercial import snapshot as whatsapp_commercial_snapshot

router = APIRouter(tags=["whatsapp-meow"])


def _ensure_own_user(current_user: User, user_id: int) -> None:
    if int(current_user.id) != int(user_id):
        raise HTTPException(status_code=403, detail="Solo podés gestionar tus propios números")


def _tenant_whatsapp_actor(
    db: Session, *, company_id: int, user_id: int, current_user: User
) -> None:
    assert_user_in_company(db, company_id=company_id, user_id=user_id)
    _ensure_own_user(current_user, user_id)


def _qr_png_b64(code: str | None) -> str | None:
    if not code:
        return None
    try:
        import qrcode

        img = qrcode.make(code)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        logging.getLogger(__name__).exception("whatsapp qr png encode failed")
        return None


def _session_read(row: WhatsappMeowSession, *, qr_code: str | None = None) -> WhatsappMeowSessionRead:
    return WhatsappMeowSessionRead(
        session_uuid=row.session_uuid,
        label=row.label,
        status=row.status,
        phone_e164=row.phone_e164,
        display_name=row.display_name,
        qr_code=qr_code,
        qr_png_base64=_qr_png_b64(qr_code),
        qr_expires_at=row.qr_expires_at,
        last_error=row.last_error,
        connected_at=row.connected_at,
        updated_at=row.updated_at,
    )


def _session_read_with_live_qr(db: Session, row: WhatsappMeowSession) -> WhatsappMeowSessionRead:
    """Incluye QR del motor mientras el pairing está activo (polling UI)."""
    qr_code: str | None = None
    if row.status in ("pending_qr", "qr_ready", "connecting"):
        try:
            view = svc.refresh_session_from_engine(db, row)
            if view.status in ("pending_qr", "qr_ready", "connecting"):
                qr_code = view.qr_code
        except Exception:
            try:
                view = svc.fetch_qr(db, row)
                qr_code = view.qr_code
            except Exception:
                pass
    elif row.status == "connected":
        qr_code = None
    return _session_read(row, qr_code=qr_code)


@router.get(
    "/companies/{company_id}/users/{user_id}/whatsapp/engine-status",
    response_model=WhatsappMeowEngineStatusRead,
)
def get_whatsapp_engine_status(
    company_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowEngineStatusRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    return WhatsappMeowEngineStatusRead(**probe_meow_engine_status())


@router.get(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions",
    response_model=list[WhatsappMeowSessionRead],
)
def list_whatsapp_sessions(
    company_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> list[WhatsappMeowSessionRead]:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    try:
        rows = svc.list_sessions(db, company_id=company_id, user_id=user_id)
        visible = [r for r in rows if str(r.status or "") != "cancelled"]
        out = [_session_read_with_live_qr(db, r) for r in visible]
        db.commit()
    except Exception as exc:
        db.rollback()
        logging.getLogger(__name__).warning(
            "whatsapp list_sessions recover failed company=%s user=%s: %s",
            company_id,
            user_id,
            type(exc).__name__,
        )
        # Fallback: devolver filas sin sync/recover (no bloquear Conexiones).
        from sqlalchemy import select

        rows = list(
            db.scalars(
                select(WhatsappMeowSession)
                .where(
                    WhatsappMeowSession.company_id == company_id,
                    WhatsappMeowSession.user_id == user_id,
                )
                .order_by(WhatsappMeowSession.id.desc())
            ).all()
        )
        visible = [r for r in rows if str(r.status or "") != "cancelled"]
        out = [_session_read(r) for r in visible]
        db.commit()
    return out


@router.get(
    "/companies/{company_id}/users/{user_id}/whatsapp/usage",
)
def get_whatsapp_usage(
    company_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    company: Company = Depends(get_company_for_user),
) -> dict:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    if int(company.id) != int(company_id):
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return whatsapp_commercial_snapshot(db, company)


@router.post(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions",
    response_model=WhatsappMeowSessionRead,
)
def create_whatsapp_session(
    company_id: int,
    user_id: int,
    payload: WhatsappMeowSessionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowSessionRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    try:
        row = svc.create_pairing_session(
            db, company_id=company_id, user_id=user_id, label=payload.label
        )
        view = svc.fetch_qr(db, row)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _session_read(row, qr_code=view.qr_code)


@router.get(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions/{session_uuid}",
    response_model=WhatsappMeowSessionRead,
)
def get_whatsapp_session(
    company_id: int,
    user_id: int,
    session_uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowSessionRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    row = svc.get_session_for_tenant(
        db, company_id=company_id, user_id=user_id, session_uuid=session_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    view = svc.refresh_session_from_engine(db, row)
    db.commit()
    return _session_read(row, qr_code=view.qr_code if view.status != "connected" else None)


@router.get(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions/{session_uuid}/qr",
    response_model=WhatsappMeowSessionRead,
)
def get_whatsapp_qr(
    company_id: int,
    user_id: int,
    session_uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowSessionRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    row = svc.get_session_for_tenant(
        db, company_id=company_id, user_id=user_id, session_uuid=session_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    view = svc.fetch_qr(db, row)
    db.commit()
    return _session_read(row, qr_code=view.qr_code)


@router.post(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions/{session_uuid}/cancel",
    response_model=WhatsappMeowSessionRead,
)
def cancel_whatsapp_pairing(
    company_id: int,
    user_id: int,
    session_uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowSessionRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    row = svc.get_session_for_tenant(
        db, company_id=company_id, user_id=user_id, session_uuid=session_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    svc.cancel_pairing(db, row)
    db.commit()
    return _session_read(row)


@router.post(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions/{session_uuid}/disconnect",
    response_model=WhatsappMeowSessionRead,
)
def disconnect_whatsapp_session(
    company_id: int,
    user_id: int,
    session_uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowSessionRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    row = svc.get_session_for_tenant(
        db, company_id=company_id, user_id=user_id, session_uuid=session_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    svc.disconnect_session(db, row)
    db.commit()
    return _session_read(row)


@router.post(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions/{session_uuid}/messages",
    response_model=WhatsappMeowMessageRead,
)
def send_whatsapp_message(
    company_id: int,
    user_id: int,
    session_uuid: str,
    payload: WhatsappMeowSendBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowMessageRead:
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    row = svc.get_session_for_tenant(
        db, company_id=company_id, user_id=user_id, session_uuid=session_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    msg = svc.send_text_message(
        db,
        row=row,
        to=payload.to,
        body=payload.body,
        idempotency_key=payload.idempotency_key,
    )
    db.commit()
    if msg.status == "failed":
        raise HTTPException(status_code=502, detail=msg.error or "Envío fallido")
    return WhatsappMeowMessageRead(
        id=msg.id,
        direction=msg.direction,
        peer=msg.peer,
        body=msg.body,
        status=msg.status,
        whatsapp_msg_id=msg.whatsapp_msg_id,
        error=msg.error,
        created_at=msg.created_at,
    )


@router.post(
    "/companies/{company_id}/users/{user_id}/whatsapp/sessions/{session_uuid}/simulate-pair",
    response_model=WhatsappMeowSessionRead,
    include_in_schema=False,
)
def simulate_pair_dev_only(
    company_id: int,
    user_id: int,
    session_uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> WhatsappMeowSessionRead:
    if (os.getenv("NEXUS_WHATSAPP_SIMULATE") or "").strip() not in ("1", "true", "yes"):
        raise HTTPException(status_code=404, detail="Not available")
    _tenant_whatsapp_actor(db, company_id=company_id, user_id=user_id, current_user=current_user)
    row = svc.get_session_for_tenant(
        db, company_id=company_id, user_id=user_id, session_uuid=session_uuid
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Sesión no encontrada")
    svc.simulate_pair_for_tests(db, row)
    db.commit()
    return _session_read(row)
