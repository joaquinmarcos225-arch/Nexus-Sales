"""Rehidratación del motor simulate local desde filas DB (reinicio backend)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.services.whatsapp_meow_contract import MeowSessionView

if TYPE_CHECKING:
    from app.models.whatsapp_meow import WhatsappMeowSession
    from app.services.whatsapp_meow_simulate import LocalSimulateMeowEngine


def _is_local_simulate_mode() -> bool:
    from app.services.whatsapp_meow_client import get_meow_engine_client

    return get_meow_engine_client() is None


def view_from_db_row(row: WhatsappMeowSession) -> MeowSessionView:
    """Reconstruye la vista del motor a partir de la fila persistida (solo simulate)."""
    now = datetime.now(UTC)
    status = str(row.status or "pending_qr")
    qr_code = None
    qr_expires = row.qr_expires_at
    if status in {"pending_qr", "qr_ready", "connecting"}:
        if qr_expires and qr_expires.tzinfo is None:
            qr_expires = qr_expires.replace(tzinfo=UTC)
        if not qr_expires or now > qr_expires:
            qr_code = f"SIM-{row.session_uuid[:8]}-{uuid.uuid4().hex[:4]}"
            qr_expires = now + timedelta(minutes=2)
        else:
            qr_code = f"SIM-{row.session_uuid[:8]}"
    return MeowSessionView(
        session_id=row.session_uuid,
        company_id=int(row.company_id),
        user_id=int(row.user_id),
        status=status,
        phone=row.phone_e164,
        display_name=row.display_name,
        qr_code=qr_code,
        qr_expires_at=qr_expires if status in {"pending_qr", "qr_ready", "connecting"} else None,
        last_error=row.last_error,
    )


def ensure_simulate_engine_session(engine: LocalSimulateMeowEngine, row: WhatsappMeowSession) -> MeowSessionView:
    """Si el motor in-memory perdió la sesión, la restaura desde DB."""
    try:
        return engine.get_session(
            row.session_uuid, company_id=int(row.company_id), user_id=int(row.user_id)
        )
    except LookupError:
        view = view_from_db_row(row)
        engine.register_session(view)
        return view
