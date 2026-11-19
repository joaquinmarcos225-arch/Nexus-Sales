"""Orquestación DB + motor whatsmeow para sesiones multi-número."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.whatsapp_meow import WhatsappMeowMessage, WhatsappMeowSession
from app.services.whatsapp_meow_client import resolve_meow_engine
from app.services.whatsapp_meow_contract import MeowSessionView
from app.services.whatsapp_meow_reconcile import apply_sync_failure

logger = logging.getLogger(__name__)

_idempotency_guard = threading.Lock()
_idempotency_locks: dict[tuple[str, str], threading.Lock] = {}

SELF_CHAT_DETAIL = (
    "El WhatsApp del contacto es el mismo número conectado en Nexus. "
    "Poné el celular del prospecto (no el tuyo) e intentá de nuevo."
)


def set_chat_presence(*, row: WhatsappMeowSession, to: str, state: str) -> None:
    """Typing / paused. Si el motor viejo no tiene el endpoint, se ignora."""
    try:
        engine = resolve_meow_engine()
        fn = getattr(engine, "set_chat_presence", None)
        if fn is None:
            return
        fn(
            row.session_uuid,
            company_id=int(row.company_id),
            user_id=int(row.user_id),
            to=to,
            state=state,
        )
    except Exception as exc:
        logger.warning(
            "meow chat presence failed session=%s state=%s err=%s",
            row.session_uuid,
            state,
            exc,
        )


def _normalize_meow_peer(peer: str) -> str:
    raw = (peer or "").strip().lower()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[0]
    return "".join(ch for ch in raw if ch.isdigit())


def _digits_look_same_number(a: str, b: str) -> bool:
    """True si a y b son el mismo celular (variantes de prefijo país)."""
    da, db_ = _normalize_meow_peer(a), _normalize_meow_peer(b)
    if len(da) < 8 or len(db_) < 8:
        return False
    if da == db_:
        return True
    for x, y in ((da, db_), (db_, da)):
        if x.endswith(y) and len(y) >= 10:
            return True
    return False


def peer_is_session_self(session: WhatsappMeowSession, to: str) -> bool:
    """Destino = número conectado en la sesión Meow (mensaje a uno mismo)."""
    return _digits_look_same_number(session.phone_e164 or "", to)


def _outbound_idempotency_matches(
    existing: WhatsappMeowMessage, *, to: str, body: str
) -> bool:
    if (existing.status or "").strip().lower() != "sent":
        return False
    if _normalize_meow_peer(existing.peer) != _normalize_meow_peer(to):
        return False
    return (existing.body or "").strip() == (body or "").strip()


def _idempotency_lock(session_uuid: str, idempotency_key: str) -> threading.Lock:
    key = (session_uuid, idempotency_key)
    with _idempotency_guard:
        lock = _idempotency_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _idempotency_locks[key] = lock
        return lock


def _local_simulate_engine():
    from app.services.whatsapp_meow_client import get_meow_engine_client
    from app.services.whatsapp_meow_simulate import get_local_simulate_engine

    if get_meow_engine_client() is not None:
        return None
    return get_local_simulate_engine()


def _ensure_engine_session(row: WhatsappMeowSession) -> None:
    """Rehidrata sesión en motor local (simulate) o en Go (ensure)."""
    eng = _local_simulate_engine()
    if eng is not None:
        from app.services.whatsapp_meow_simulate_persist import ensure_simulate_engine_session

        ensure_simulate_engine_session(eng, row)
        return
    from app.services.whatsapp_meow_client import get_meow_engine_client

    client = get_meow_engine_client()
    if client is None:
        return
    try:
        client.get_session(row.session_uuid, company_id=row.company_id, user_id=row.user_id)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "404" in msg or "not found" in msg:
            try:
                client.ensure_session(
                    company_id=row.company_id,
                    user_id=row.user_id,
                    session_id=row.session_uuid,
                )
            except RuntimeError as ensure_exc:
                em = str(ensure_exc).lower()
                if any(
                    token in em
                    for token in (
                        "404",
                        "not found",
                        "405",
                        "store not found",
                        "session store not found",
                    )
                ):
                    raise RuntimeError(
                        "session not found in whatsapp engine (restart or expired)"
                    ) from ensure_exc
                raise
        else:
            raise


def _tenant(row: WhatsappMeowSession) -> dict[str, int]:
    return {"company_id": int(row.company_id), "user_id": int(row.user_id)}


def _sync_row(row: WhatsappMeowSession, view: MeowSessionView) -> None:
    from app.services.whatsapp_meow_reconcile import meow_needs_qr_reconnect

    engine_status = (view.status or "").strip().lower()
    db_status = (row.status or "").strip().lower()
    # Tras device-JID el motor a veces sigue reportando "connected" fantasma.
    # No re-promover hasta que el usuario arranque QR (fetch_qr limpia last_error).
    if (
        db_status == "disconnected"
        and engine_status == "connected"
        and meow_needs_qr_reconnect(row.last_error or "")
    ):
        logger.info(
            "meow skip ghost connected session=%s (DB disconnected, needs QR)",
            row.session_uuid,
        )
        return
    row.status = view.status
    row.phone_e164 = view.phone
    row.display_name = view.display_name
    row.last_error = view.last_error
    row.qr_expires_at = view.qr_expires_at
    if view.status == "connected" and row.connected_at is None:
        row.connected_at = datetime.now(UTC)
    if view.status in {"disconnected", "cancelled"} and row.disconnected_at is None:
        row.disconnected_at = datetime.now(UTC)


def _engine_get_session(row: WhatsappMeowSession) -> MeowSessionView:
    _ensure_engine_session(row)
    engine = resolve_meow_engine()
    return engine.get_session(row.session_uuid, **_tenant(row))


def max_sessions_per_user() -> int | None:
    """0 o vacío = sin límite fijo en código (límite comercial configurable)."""
    raw = (os.getenv("NEXUS_WHATSAPP_MAX_SESSIONS_PER_USER") or "0").strip()
    try:
        n = int(raw)
    except ValueError:
        return None
    return None if n <= 0 else n


def list_sessions(db: Session, *, company_id: int, user_id: int) -> list[WhatsappMeowSession]:
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
    for row in rows:
        if row.status in ("pending_qr", "qr_ready", "connecting", "connected", "error"):
            try:
                view = _engine_get_session(row)
                _sync_row(row, view)
            except Exception as exc:
                apply_sync_failure(row, exc)
                logger.info(
                    "whatsapp session reconcile session=%s kind=%s",
                    row.session_uuid,
                    type(exc).__name__,
                )
        if row.status == "connected":
            recover_pending_outbound_messages(db, row)
    return rows


_PAIRING_IN_PROGRESS = ("pending_qr", "qr_ready", "connecting")


def find_active_pairing_session(
    db: Session, *, company_id: int, user_id: int
) -> WhatsappMeowSession | None:
    """Evita duplicar sesiones si ya hay un pairing abierto."""
    return db.scalars(
        select(WhatsappMeowSession)
        .where(
            WhatsappMeowSession.company_id == company_id,
            WhatsappMeowSession.user_id == user_id,
            WhatsappMeowSession.status.in_(_PAIRING_IN_PROGRESS),
        )
        .order_by(WhatsappMeowSession.id.desc())
    ).first()


def create_pairing_session(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    label: str | None = None,
) -> WhatsappMeowSession:
    from app.services.whatsapp_meow_engine_probe import assert_pairing_engine_allowed

    assert_pairing_engine_allowed()

    existing = find_active_pairing_session(db, company_id=company_id, user_id=user_id)
    if existing is not None:
        if label and not (existing.label or "").strip():
            existing.label = (label or "").strip() or None
        return existing

    limit = max_sessions_per_user()
    if limit is not None:
        active = db.scalar(
            select(func.count())
            .select_from(WhatsappMeowSession)
            .where(
                WhatsappMeowSession.company_id == company_id,
                WhatsappMeowSession.user_id == user_id,
                WhatsappMeowSession.status.in_(("pending_qr", "qr_ready", "connecting", "connected")),
            )
        )
        if int(active or 0) >= limit:
            raise ValueError(f"Límite de números WhatsApp alcanzado ({limit})")

    session_uuid = str(uuid.uuid4())
    engine = resolve_meow_engine()
    view = engine.create_session(company_id=company_id, user_id=user_id, session_id=session_uuid)
    row = WhatsappMeowSession(
        session_uuid=session_uuid,
        company_id=company_id,
        user_id=user_id,
        label=(label or "").strip() or None,
        status=view.status,
        qr_expires_at=view.qr_expires_at,
    )
    _sync_row(row, view)
    db.add(row)
    db.flush()
    return row


def refresh_session_from_engine(db: Session, row: WhatsappMeowSession) -> MeowSessionView:
    try:
        view = _engine_get_session(row)
        _sync_row(row, view)
        db.flush()
        return view
    except Exception as exc:
        apply_sync_failure(row, exc)
        db.flush()
        raise


def fetch_qr(db: Session, row: WhatsappMeowSession) -> MeowSessionView:
    _ensure_engine_session(row)
    engine = resolve_meow_engine()
    try:
        view = engine.get_qr(row.session_uuid, **_tenant(row))
        if not (view.qr_code or "").strip() and view.status in ("pending_qr", "qr_ready"):
            for _ in range(8):
                time.sleep(0.4)
                view = engine.get_qr(row.session_uuid, **_tenant(row))
                if (view.qr_code or "").strip() or view.status not in ("pending_qr", "qr_ready"):
                    break
        # Usuario pidió QR → permitir _sync_row aunque last_error diga device-JID.
        row.last_error = None
        _sync_row(row, view)
        db.flush()
        return view
    except Exception as exc:
        apply_sync_failure(row, exc)
        db.flush()
        raise


def cancel_pairing(db: Session, row: WhatsappMeowSession) -> None:
    _ensure_engine_session(row)
    engine = resolve_meow_engine()
    engine.cancel_pairing(row.session_uuid, **_tenant(row))
    view = engine.get_session(row.session_uuid, **_tenant(row))
    _sync_row(row, view)
    db.flush()


def disconnect_session(db: Session, row: WhatsappMeowSession) -> None:
    _ensure_engine_session(row)
    engine = resolve_meow_engine()
    engine.disconnect(row.session_uuid, **_tenant(row))
    view = engine.get_session(row.session_uuid, **_tenant(row))
    _sync_row(row, view)
    db.flush()


def simulate_pair_for_tests(db: Session, row: WhatsappMeowSession) -> MeowSessionView:
    _ensure_engine_session(row)
    engine = resolve_meow_engine()
    view = engine.simulate_pair(row.session_uuid, **_tenant(row))
    _sync_row(row, view)
    db.flush()
    return view


def get_session_for_tenant(
    db: Session, *, company_id: int, user_id: int, session_uuid: str
) -> WhatsappMeowSession | None:
    return db.scalars(
        select(WhatsappMeowSession).where(
            WhatsappMeowSession.session_uuid == session_uuid,
            WhatsappMeowSession.company_id == company_id,
            WhatsappMeowSession.user_id == user_id,
        )
    ).first()


def _find_outbound_by_idempotency(
    db: Session, *, session_uuid: str, idempotency_key: str
) -> WhatsappMeowMessage | None:
    return db.scalars(
        select(WhatsappMeowMessage).where(
            WhatsappMeowMessage.session_uuid == session_uuid,
            WhatsappMeowMessage.idempotency_key == idempotency_key,
        )
    ).first()


def _deliver_outbound_row(
    db: Session,
    *,
    row: WhatsappMeowSession,
    msg_row: WhatsappMeowMessage,
) -> WhatsappMeowMessage:
    from app.services.whatsapp_meow_autosend_guard import meow_autosend_allowed_for_peer

    allowed, reason = meow_autosend_allowed_for_peer(msg_row.peer or "")
    if not allowed:
        msg_row.status = "failed"
        msg_row.error = reason
        db.flush()
        logger.info(
            "meow outbound blocked reason=%s session=%s idempotency=%s",
            reason,
            row.session_uuid,
            (msg_row.idempotency_key or "")[:32],
        )
        return msg_row
    if msg_row.status == "sent" and (msg_row.whatsapp_msg_id or "").strip():
        return msg_row
    _ensure_engine_session(row)
    engine = resolve_meow_engine()
    try:
        result = engine.send_text(
            row.session_uuid,
            **_tenant(row),
            to=msg_row.peer,
            body=msg_row.body or "",
            idempotency_key=msg_row.idempotency_key,
        )
    except Exception as exc:
        msg_row.status = "failed"
        msg_row.error = str(exc)
        from app.services.whatsapp_meow_reconcile import (
            mark_session_needs_qr_reconnect,
            meow_needs_qr_reconnect,
        )

        if meow_needs_qr_reconnect(exc):
            mark_session_needs_qr_reconnect(row, exc)
        else:
            from app.services.whatsapp_meow_reconcile import apply_sync_failure

            apply_sync_failure(row, exc)
        db.flush()
        logger.warning("whatsapp meow send failed session=%s: %s", row.session_uuid, exc)
        return msg_row

    if result.status != "sent":
        msg_row.status = "failed"
        msg_row.error = result.error or "send not confirmed"
        err = msg_row.error or ""
        from app.services.whatsapp_meow_reconcile import (
            mark_session_needs_qr_reconnect,
            meow_needs_qr_reconnect,
        )

        if meow_needs_qr_reconnect(err):
            mark_session_needs_qr_reconnect(row, RuntimeError(err))
    else:
        msg_row.status = "sent"
        msg_row.whatsapp_msg_id = result.whatsapp_msg_id or None
        msg_row.error = None
    db.flush()
    return msg_row


def recover_pending_outbound_messages(db: Session, row: WhatsappMeowSession) -> None:
    """Reintenta outbox Python (pending) tras reconexión del motor."""
    if row.status != "connected":
        return
    pending = db.scalars(
        select(WhatsappMeowMessage)
        .where(
            WhatsappMeowMessage.session_uuid == row.session_uuid,
            WhatsappMeowMessage.direction == "out",
            WhatsappMeowMessage.status == "pending",
        )
        .order_by(WhatsappMeowMessage.id.asc())
        .limit(20)
    ).all()
    for msg_row in pending:
        if not (msg_row.idempotency_key or "").strip():
            msg_row.status = "failed"
            msg_row.error = "missing idempotency_key"
            continue
        twin = db.scalars(
            select(WhatsappMeowMessage)
            .where(
                WhatsappMeowMessage.session_uuid == row.session_uuid,
                WhatsappMeowMessage.direction == "out",
                WhatsappMeowMessage.status == "sent",
                WhatsappMeowMessage.body == (msg_row.body or ""),
                WhatsappMeowMessage.id != msg_row.id,
            )
            .order_by(WhatsappMeowMessage.id.desc())
        ).first()
        if twin is not None and _normalize_meow_peer(twin.peer) == _normalize_meow_peer(
            msg_row.peer
        ):
            # No copiar twin.whatsapp_msg_id: uq_wa_meow_wa_msg_id es global y
            # rompía list/create de sesiones (IntegrityError → PendingRollbackError).
            msg_row.status = "sent"
            msg_row.whatsapp_msg_id = None
            msg_row.error = "reused_duplicate_body"
            continue
        _deliver_outbound_row(db, row=row, msg_row=msg_row)


def send_text_message(
    db: Session,
    *,
    row: WhatsappMeowSession,
    to: str,
    body: str,
    idempotency_key: str | None,
) -> WhatsappMeowMessage:
    if peer_is_session_self(row, to):
        raise HTTPException(status_code=400, detail=SELF_CHAT_DETAIL)
    effective_key = (idempotency_key or "").strip() or None
    if effective_key:
        existing = _find_outbound_by_idempotency(
            db, session_uuid=row.session_uuid, idempotency_key=effective_key
        )
        if existing and _outbound_idempotency_matches(existing, to=to, body=body):
            return existing
        if existing and (existing.status or "").strip().lower() == "sent":
            if _normalize_meow_peer(existing.peer) == _normalize_meow_peer(to):
                logger.info(
                    "meow idempotency reuse session=%s key=%s — already sent to same peer",
                    row.session_uuid,
                    idempotency_key,
                )
                return existing
            suffix = hashlib.sha256(f"{to}|{body}".encode("utf-8")).hexdigest()[:10]
            effective_key = f"{effective_key}-x{suffix}"
            logger.warning(
                "meow idempotency collision session=%s key=%s — new send",
                row.session_uuid,
                idempotency_key,
            )

    msg_row: WhatsappMeowMessage
    if effective_key:
        with _idempotency_lock(row.session_uuid, effective_key):
            existing = _find_outbound_by_idempotency(
                db, session_uuid=row.session_uuid, idempotency_key=effective_key
            )
            if existing and _outbound_idempotency_matches(existing, to=to, body=body):
                return existing
            if existing:
                msg_row = existing
                msg_row.peer = to
                msg_row.body = body
                if (msg_row.status or "").strip().lower() == "sent" and not _outbound_idempotency_matches(
                    msg_row, to=to, body=body
                ):
                    msg_row.status = "pending"
                    msg_row.error = None
                    msg_row.whatsapp_msg_id = None
            else:
                msg_row = WhatsappMeowMessage(
                    session_uuid=row.session_uuid,
                    company_id=row.company_id,
                    user_id=row.user_id,
                    direction="out",
                    peer=to,
                    body=body,
                    status="pending",
                    idempotency_key=effective_key,
                )
                try:
                    with db.begin_nested():
                        db.add(msg_row)
                        db.flush()
                except IntegrityError:
                    db.expire_all()
                    dup = _find_outbound_by_idempotency(
                        db, session_uuid=row.session_uuid, idempotency_key=effective_key
                    )
                    if dup:
                        if _outbound_idempotency_matches(dup, to=to, body=body):
                            return dup
                        return _deliver_outbound_row(db, row=row, msg_row=dup)
                    raise
    else:
        msg_row = WhatsappMeowMessage(
            session_uuid=row.session_uuid,
            company_id=row.company_id,
            user_id=row.user_id,
            direction="out",
            peer=to,
            body=body,
            status="pending",
            idempotency_key=effective_key,
        )
        db.add(msg_row)
        db.flush()

    return _deliver_outbound_row(db, row=row, msg_row=msg_row)


def record_inbound_message(
    db: Session,
    *,
    session_uuid: str,
    company_id: int,
    user_id: int,
    peer: str,
    body: str,
    whatsapp_msg_id: str | None,
    status: str = "received",
) -> WhatsappMeowMessage | None:
    if whatsapp_msg_id:
        dup = db.scalars(
            select(WhatsappMeowMessage).where(WhatsappMeowMessage.whatsapp_msg_id == whatsapp_msg_id)
        ).first()
        if dup:
            return None
    row = WhatsappMeowMessage(
        session_uuid=session_uuid,
        company_id=company_id,
        user_id=user_id,
        direction="in",
        peer=peer,
        body=body,
        status=(status or "received").strip() or "received",
        whatsapp_msg_id=whatsapp_msg_id or None,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        if whatsapp_msg_id:
            return None
        raise
    return row
