"""Reconciliación de estado DB ↔ motor Go (errores temporales vs definitivos)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.models.whatsapp_meow import WhatsappMeowSession

_logger = logging.getLogger(__name__)


def classify_engine_error(exc: Exception | str) -> str:
    """Retorna ``definitive`` o ``transient``."""
    text = str(exc).lower()
    if "403" in text or "forbidden" in text or "tenant mismatch" in text:
        return "definitive"
    if "404" in text or "not found" in text or "session not found" in text:
        return "definitive"
    if "401" in text or "unauthorized" in text:
        return "definitive"
    if "405" in text or "410" in text:
        return "definitive"
    if "not connected" in text or "session not connected" in text:
        return "definitive"
    # Store whatsmeow vacío / sin device tras reinicio o logout en el teléfono.
    if (
        "device jid" in text
        or "store doesn't contain" in text
        or "store does not contain" in text
        or "no device" in text
        or "logged out" in text
    ):
        return "definitive"
    if "whatsapp meow 400" in text:
        return "definitive"
    if "connection refused" in text or "connecterror" in text:
        return "transient"
    if "timeout" in text or "timed out" in text:
        return "transient"
    if "whatsapp meow 5" in text or "whatsapp meow 502" in text:
        return "transient"
    if "whatsapp meow 503" in text:
        return "transient"
    return "transient"


def meow_needs_qr_reconnect(exc: Exception | str) -> bool:
    text = str(exc).lower()
    # Mensaje amigable ya persistido en last_error (post apply_sync_failure).
    if (
        "escaneá el qr" in text
        or "escanea el qr" in text
        or "whatsapp se desconect" in text
        or "whatsapp necesita reconex" in text
        or "integraciones → whatsapp" in text
        or "integraciones -> whatsapp" in text
    ):
        return True
    return classify_engine_error(exc) == "definitive"


def reconnect_detail_for_engine_error(exc: Exception | str) -> str:
    text = str(exc).lower()
    if "device jid" in text or "store doesn't contain" in text or "store does not contain" in text:
        return (
            "WhatsApp se desconectó (la sesión QR ya no está en el servidor). "
            "Andá a Integraciones → WhatsApp, escaneá el QR de nuevo y reanudá la secuencia."
        )
    return (
        "WhatsApp necesita reconexión. "
        "Andá a Integraciones → WhatsApp, conectá con QR y reanudá la secuencia."
    )


def apply_sync_failure(row: WhatsappMeowSession, exc: Exception) -> None:
    """Actualiza fila cuando no se pudo sincronizar con Go (sin borrar credenciales en Go)."""
    kind = classify_engine_error(exc)
    now = datetime.now(UTC)
    msg = str(exc).strip()[:500] or "Error de sincronización con motor WhatsApp"

    if kind == "definitive":
        if row.status in ("connected", "connecting", "qr_ready", "pending_qr", "error"):
            row.status = "disconnected"
            row.last_error = reconnect_detail_for_engine_error(exc)
            if row.disconnected_at is None:
                row.disconnected_at = now
        return

    # Transitorio: no afirmar conexión activa si no pudimos verificar.
    if row.status == "connected":
        row.status = "error"
        row.last_error = f"Sincronización temporal fallida: {msg}"
    elif row.status in ("pending_qr", "qr_ready", "connecting"):
        row.last_error = f"Motor WhatsApp no disponible: {msg}"


def persist_session_needs_qr_reconnect(session_id: int, exc: Exception | str) -> None:
    """Marca disconnected en una transacción aparte.

    ``execute_sequence_touch`` hace ``db.rollback()`` ante HTTPException; sin este
    commit el status ``connected`` fantasma vuelve y el día 1 reintenta el send.
    """
    from app.database.session import SessionLocal

    sid = int(session_id)
    err = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
    db = SessionLocal()
    try:
        row = db.get(WhatsappMeowSession, sid)
        if row is None:
            return
        apply_sync_failure(row, err)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        _logger.exception("persist_session_needs_qr_reconnect failed id=%s", sid)
    finally:
        db.close()


def mark_session_needs_qr_reconnect(
    row: WhatsappMeowSession,
    exc: Exception | str,
    *,
    disconnect_engine: bool = True,
) -> None:
    """Marca la sesión como reconectar QR (ORM + commit durable + disconnect Go)."""
    err = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
    apply_sync_failure(row, err)
    if disconnect_engine:
        try:
            from app.services.whatsapp_meow_service import _ensure_engine_session, resolve_meow_engine

            _ensure_engine_session(row)
            engine = resolve_meow_engine()
            engine.disconnect(
                row.session_uuid,
                company_id=int(row.company_id),
                user_id=int(row.user_id),
            )
        except Exception as disc_exc:  # noqa: BLE001
            _logger.info(
                "meow engine disconnect after device-jid failed session=%s: %s",
                row.session_uuid,
                disc_exc,
            )
    if getattr(row, "id", None) is not None:
        persist_session_needs_qr_reconnect(int(row.id), err)
