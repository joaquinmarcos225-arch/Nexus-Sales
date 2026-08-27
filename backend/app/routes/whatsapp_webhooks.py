"""Webhooks públicos de WhatsApp Cloud API (Meta) — detección automática de inbound."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.services.whatsapp_inbound_sync import ingest_meta_webhook_messages

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp-webhooks"])


def _verify_token() -> str:
    return (os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN") or "").strip()


def _app_secret() -> str:
    return (os.getenv("WHATSAPP_APP_SECRET") or "").strip()


def _webhooks_enabled() -> bool:
    v = (os.getenv("WHATSAPP_WEBHOOK_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _valid_signature(raw_body: bytes, header: str | None) -> bool:
    secret = _app_secret()
    if not secret:
        # Sin secret configurado: aceptar en dev (log warning).
        logger.warning("whatsapp webhook: WHATSAPP_APP_SECRET vacío — firma no verificada")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = header.split("=", 1)[1].strip()
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


@router.get("/webhooks/whatsapp")
def verify_whatsapp_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> Response:
    """Challenge de verificación Meta (suscripción del webhook)."""
    token = _verify_token()
    if hub_mode == "subscribe" and token and hub_verify_token == token and hub_challenge:
        return Response(content=str(hub_challenge), media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verificación WhatsApp fallida")


@router.post("/webhooks/whatsapp")
async def receive_whatsapp_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Recibe mensajes inbound de Meta Cloud API.
    Siempre responde 200 rápido para evitar reintentos agresivos.
    """
    if not _webhooks_enabled():
        return {"ok": True, "skipped": "disabled"}

    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _valid_signature(raw, sig):
        logger.warning("whatsapp webhook firma inválida")
        raise HTTPException(status_code=403, detail="Firma inválida")

    try:
        import json

        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return {"ok": True, "skipped": "invalid_json"}

    if not isinstance(payload, dict):
        return {"ok": True, "skipped": "not_object"}

    # Solo statuses (delivered/read) — ack sin trabajo.
    try:
        stats = ingest_meta_webhook_messages(db, payload=payload)
        db.commit()
    except Exception:
        logger.exception("whatsapp webhook ingest failed")
        db.rollback()
        return {"ok": True, "error": "ingest_failed"}

    logger.info(
        "whatsapp webhook processed=%s inserted=%s unmatched=%s duplicates=%s",
        stats.get("processed"),
        stats.get("inserted"),
        stats.get("unmatched"),
        stats.get("duplicates"),
    )
    return {"ok": True, **stats}
