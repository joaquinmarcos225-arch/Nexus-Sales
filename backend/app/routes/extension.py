"""Config OTA + telemetría para la extensión Nexus (JSON only — sin JS remoto)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.models.user import User
from app.services.chrome_extension_pack import build_chrome_extension_zip, extension_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extension", tags=["extension"])

# Versión del schema de config. Subir cuando cambie la forma del JSON.
WA_CONFIG_VERSION = 1


def default_wa_config() -> dict[str, Any]:
    """Selectores/módulos tuneables sin republish de la extensión."""
    return {
        "version": WA_CONFIG_VERSION,
        "storeEnabled": True,
        "domFallbackEnabled": True,
        "quietOpenEnabled": True,
        "pollIntervalMs": 3000,
        "storeWatchBatchSize": 16,
        "storeWatchMax": 40,
        "domScrollSearchEnabled": True,
        "domOpenBatchSize": 8,
        "domReportBatchSize": 16,
        "storeModuleCandidates": [
            "WAWebCollections",
            "WAWebChatCollection",
            "WAWebContactCollection",
            "WAWebChatLoadMessages",
            "WAWebConversationMsgs",
        ],
        "domUnreadTestIds": [
            "icon-unread-count",
            "icon-unread",
            "unread-count",
        ],
        "domChatListTestIds": [
            "chat-list",
            "cell-frame-container",
            "cell-frame-title",
            "cell-frame-secondary",
        ],
    }


class WaTelemetryIn(BaseModel):
    extension_version: str | None = Field(default=None, max_length=32)
    store_ok: bool | None = None
    store_source: str | None = Field(default=None, max_length=64)
    store_error: str | None = Field(default=None, max_length=120)
    chats: int | None = None
    matched: int | None = None
    inbound: int | None = None
    candidates: int | None = None
    reported: int | None = None
    reason: str | None = Field(default=None, max_length=64)


@router.get("/chrome-zip")
def download_chrome_extension_zip(user: User = Depends(get_current_user)) -> Response:
    """ZIP listo para chrome://extensions → Cargar descomprimida."""
    _ = user
    if not extension_available():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El paquete de la extensión no está disponible en este servidor.",
        )
    try:
        payload = build_chrome_extension_zip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("chrome extension zip failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo generar el ZIP de la extensión.",
        ) from exc
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="nexus-linkedin-assist.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/wa-config")
def get_wa_extension_config(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """JSON OTA: la extensión lo cachea. Sin código ejecutable."""
    _ = user
    return default_wa_config()


@router.post("/wa-telemetry")
def post_wa_extension_telemetry(
    body: WaTelemetryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Telemetría anónima de salud Store/inbound (sin cuerpo de mensajes)."""
    _ = db
    logger.info(
        "wa_ext_telemetry user_id=%s company_id=%s ver=%s store_ok=%s source=%s err=%s "
        "chats=%s matched=%s inbound=%s candidates=%s reported=%s reason=%s",
        getattr(user, "id", None),
        getattr(user, "company_id", None),
        body.extension_version,
        body.store_ok,
        body.store_source,
        body.store_error,
        body.chats,
        body.matched,
        body.inbound,
        body.candidates,
        body.reported,
        body.reason,
    )
    return {"ok": True}
