"""Nexus Support: un hilo por usuario de la empresa cliente → equipo CostGuard."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.company import Company
from app.models.support_ticket import SupportMessage, SupportThread
from app.models.user import User

_logger = logging.getLogger("nexus.support")


def is_nexus_support_ops(user: User) -> bool:
    """Equipo Nexus (no el cliente). Emails en NEXUS_SUPPORT_OPS_EMAILS, o owner/gerente de NEXUS_OPS_COMPANY_ID."""
    raw = (os.getenv("NEXUS_SUPPORT_OPS_EMAILS") or "").strip()
    emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if emails:
        return (user.email or "").strip().lower() in emails
    try:
        ops_cid = int((os.getenv("NEXUS_OPS_COMPANY_ID") or "1").strip() or "1")
    except ValueError:
        ops_cid = 1
    role = str(user.role or "").strip().lower()
    return int(user.company_id) == ops_cid and role in {
        "owner",
        "gerente",
        "admin",
        "director",
    }


def _now() -> datetime:
    return datetime.now(UTC)


def get_or_create_user_thread(db: Session, *, company_id: int, user: User) -> SupportThread:
    """Hilo del usuario autenticado con Support — no se comparte con el resto de la empresa."""
    thread = db.scalars(
        select(SupportThread).where(
            SupportThread.company_id == int(company_id),
            SupportThread.opened_by_user_id == int(user.id),
        )
    ).first()
    if thread is not None:
        return thread
    thread = SupportThread(
        company_id=int(company_id),
        opened_by_user_id=int(user.id),
        status="open",
        last_message_at=_now(),
    )
    db.add(thread)
    db.flush()
    return thread


def get_or_create_company_thread(db: Session, *, company_id: int, user: User) -> SupportThread:
    return get_or_create_user_thread(db, company_id=company_id, user=user)


_OPS_LOAD = (
    selectinload(SupportThread.company),
    selectinload(SupportThread.messages),
    selectinload(SupportThread.opened_by),
)


def list_ops_threads(db: Session) -> list[SupportThread]:
    return list(
        db.scalars(
            select(SupportThread)
            .options(*_OPS_LOAD)
            .order_by(SupportThread.last_message_at.desc())
        ).all()
    )


def get_thread_for_ops(db: Session, thread_id: int) -> SupportThread | None:
    return db.scalars(
        select(SupportThread)
        .options(*_OPS_LOAD)
        .where(SupportThread.id == int(thread_id))
    ).first()


def set_thread_status(db: Session, *, thread: SupportThread, status: str) -> SupportThread:
    key = (status or "").strip().lower()
    if key not in {"open", "resolved"}:
        raise ValueError("Estado inválido")
    thread.status = key
    db.flush()
    return thread


def add_message(
    db: Session,
    *,
    thread: SupportThread,
    author: User | None,
    role: str,
    body: str,
) -> SupportMessage:
    text = (body or "").strip()
    # Cliente: el mensaje solo puede ir al hilo propio (evita mezclar SDR en un hilo ajeno).
    if role == "user" and author is not None:
        opener_id = int(thread.opened_by_user_id or 0)
        if opener_id and int(author.id) != opener_id:
            raise ValueError(
                "No se puede escribir en el hilo de soporte de otro usuario"
            )
    msg = SupportMessage(
        thread_id=int(thread.id),
        author_user_id=int(author.id) if author is not None else None,
        role=role,
        body=text[:8000],
        created_at=_now(),
    )
    db.add(msg)
    thread.last_message_at = msg.created_at
    if role == "user":
        thread.status = "open"
    db.flush()
    try:
        from app.services.push_notify import notify_support_message

        notify_support_message(db, thread=thread, role=role, preview=text)
    except Exception:
        _logger.exception("[support] push notify falló thread=%s", thread.id)
    if role == "user":
        company = db.get(Company, thread.company_id)
        _logger.info(
            "[support] nuevo mensaje company_id=%s company=%s user=%s thread=%s",
            thread.company_id,
            getattr(company, "name", None),
            getattr(author, "email", None),
            thread.id,
        )
    return msg


def serialize_message(msg: SupportMessage) -> dict:
    return {
        "id": msg.id,
        "role": msg.role,
        "text": msg.body,
        "at": msg.created_at.isoformat() if msg.created_at else None,
    }


def serialize_thread(thread: SupportThread, *, include_messages: bool = True) -> dict:
    company = thread.company
    opener = thread.opened_by
    messages = list(thread.messages or [])
    last = messages[-1] if messages else None
    status = (thread.status or "open").strip().lower() or "open"
    last_role = last.role if last else None
    payload = {
        "id": thread.id,
        "company_id": thread.company_id,
        "company_name": getattr(company, "name", None),
        "status": status,
        "waiting": status == "open" and last_role == "user",
        "message_count": len(messages),
        "opened_by_name": (getattr(opener, "name", None) or getattr(opener, "email", None) or None),
        "opened_by_email": getattr(opener, "email", None),
        "user_id": int(thread.opened_by_user_id) if thread.opened_by_user_id else None,
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
        "preview": (last.body[:140] if last else "") or "",
        "last_role": last_role,
    }
    if include_messages:
        payload["messages"] = [serialize_message(m) for m in messages]
    return payload
