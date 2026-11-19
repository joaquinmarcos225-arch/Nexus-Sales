"""Webhook interno: eventos entrantes desde el servicio Go."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.schemas.whatsapp_meow import WhatsappMeowInboundEvent, WhatsappMeowMessageRead
from app.services import whatsapp_meow_service as svc

router = APIRouter(tags=["whatsapp-meow-internal"])
logger = logging.getLogger(__name__)


def _hitlog(line: str) -> None:
    """Append-only local diagnostic (Meow→API). No secrets."""
    try:
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "logs" / "meow_inbound_hits.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip() + "\n")
    except Exception:
        pass


def _verify_internal_token(x_nexus_internal_token: str | None = Header(default=None)) -> None:
    expected = (os.getenv("NEXUS_WHATSAPP_INTERNAL_TOKEN") or "").strip()
    if not expected:
        _hitlog("auth 503 token_not_configured")
        raise HTTPException(status_code=503, detail="Internal token not configured")
    if (x_nexus_internal_token or "").strip() != expected:
        _hitlog("auth 401 unauthorized")
        raise HTTPException(status_code=401, detail="Unauthorized")


def _coerce_inbound_dict(raw: Any) -> dict[str, Any]:
    """Normaliza lo que manda Go (camelCase, tipos sueltos) a lo que espera el schema."""
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="Expected JSON object")
    data = dict(raw)
    if not data.get("whatsapp_msg_id"):
        for key in ("WhatsAppMsgID", "whatsappMsgId", "MessageID", "message_id", "id"):
            if data.get(key) not in (None, ""):
                data["whatsapp_msg_id"] = str(data[key])
                break
    if not data.get("session_id"):
        for key in ("SessionID", "sessionId", "session_uuid"):
            if data.get(key) not in (None, ""):
                data["session_id"] = str(data[key])
                break
    if "from" not in data or data.get("from") in (None, ""):
        for key in ("From", "from_jid", "sender", "peer"):
            if data.get(key) not in (None, ""):
                data["from"] = str(data[key])
                break
    for int_key in ("company_id", "user_id"):
        if int_key in data and not isinstance(data[int_key], int):
            try:
                data[int_key] = int(data[int_key])
            except (TypeError, ValueError):
                pass
    if "company_id" not in data:
        for key in ("CompanyID", "companyId"):
            if data.get(key) is not None:
                try:
                    data["company_id"] = int(data[key])
                except (TypeError, ValueError):
                    pass
                break
    if "user_id" not in data:
        for key in ("UserID", "userId"):
            if data.get(key) is not None:
                try:
                    data["user_id"] = int(data[key])
                except (TypeError, ValueError):
                    pass
                break
    if data.get("body") is None:
        data["body"] = ""
    else:
        data["body"] = str(data.get("body") or "")
    ts = data.get("timestamp")
    if ts is not None and not isinstance(ts, str):
        data["timestamp"] = str(ts)
    if not data.get("whatsapp_msg_id"):
        data["whatsapp_msg_id"] = f"meow-missing-id-{uuid.uuid4().hex[:16]}"
    return data


def process_meow_inbound_event(
    payload: WhatsappMeowInboundEvent,
    db: Session,
) -> WhatsappMeowMessageRead | None:
    """Core inbound (tests + HTTP)."""
    logger.info(
        "meow inbound POST session=%s company=%s user=%s from=%s msg_id=%s ts=%s body_len=%s event=%s",
        payload.session_id,
        payload.company_id,
        payload.user_id,
        payload.from_,
        payload.whatsapp_msg_id,
        payload.timestamp,
        len(payload.body or ""),
        payload.event,
    )
    _hitlog(
        f"post session={payload.session_id} company={payload.company_id} user={payload.user_id} "
        f"from={payload.from_} msg_id={payload.whatsapp_msg_id} ts={payload.timestamp!r} "
        f"body_len={len(payload.body or '')} event={payload.event!r}"
    )
    row = svc.get_session_for_tenant(
        db,
        company_id=payload.company_id,
        user_id=payload.user_id,
        session_uuid=payload.session_id,
    )
    if row is None:
        _hitlog(
            f"404 session={payload.session_id} company={payload.company_id} user={payload.user_id}"
        )
        logger.warning(
            "meow inbound 404 session=%s company=%s user=%s",
            payload.session_id,
            payload.company_id,
            payload.user_id,
        )
        raise HTTPException(status_code=404, detail="Session not found for tenant")
    if (payload.event or "").strip().lower() == "presence":
        try:
            from app.services.whatsapp_humanize import note_prospect_composing

            state = (payload.presence_state or "").strip().lower()
            note_prospect_composing(
                payload.from_,
                composing=state in ("composing", "recording"),
            )
        except ImportError:
            pass
        return None
    from app.services.whatsapp_meow_commercial_isolation import (
        gate_meow_inbound_commercial,
        log_meow_inbound_excluded,
    )

    try:
        from app.services.audio_transcription import resolve_inbound_body_with_audio

        inbound_body = resolve_inbound_body_with_audio(
            body=payload.body,
            audio_base64=payload.audio_base64,
            audio_mime=payload.audio_mime,
        )
    except ImportError:
        inbound_body = payload.body or ""
    payload.body = inbound_body

    gate = gate_meow_inbound_commercial(
        db,
        session_row=row,
        from_peer=payload.from_,
        body=inbound_body,
        inbound_timestamp=payload.timestamp,
    )
    if not gate.accept:
        _hitlog(f"excluded reason={gate.reason} msg_id={payload.whatsapp_msg_id}")
        log_meow_inbound_excluded(
            reason=gate.reason,
            session_id=payload.session_id,
            from_peer=payload.from_,
            whatsapp_msg_id=payload.whatsapp_msg_id,
        )
        if gate.reason in {
            "unknown_contact",
            "ambiguous_contact",
            "invalid_sender",
            "seller_session_mismatch",
            "no_active_sequence",
            "no_campaign",
            "historical_sync",
            "pre_outbound_history",
            "meeting_booked",
            "meeting_done",
            "ai_paused",
            "opt_out",
        }:
            svc.record_inbound_message(
                db,
                session_uuid=payload.session_id,
                company_id=payload.company_id,
                user_id=payload.user_id,
                peer=payload.from_,
                body=inbound_body or f"[ignored:{gate.reason}]",
                whatsapp_msg_id=payload.whatsapp_msg_id,
                status="ignored",
            )
        db.commit()
        return None

    msg = svc.record_inbound_message(
        db,
        session_uuid=payload.session_id,
        company_id=payload.company_id,
        user_id=payload.user_id,
        peer=payload.from_,
        body=inbound_body,
        whatsapp_msg_id=payload.whatsapp_msg_id,
    )
    if msg is not None:
        try:
            from app.services.whatsapp_meow_inbound_bridge import (
                process_meow_inbound_after_store,
            )

            process_meow_inbound_after_store(
                db,
                payload=payload,
                session_row=row,
                meow_row=msg,
            )
        except ImportError:
            logger.info(
                "meow inbound stored without bridge session=%s msg_id=%s",
                payload.session_id,
                payload.whatsapp_msg_id,
            )
        _hitlog(f"stored id={msg.id} msg_id={payload.whatsapp_msg_id}")
    else:
        _hitlog(f"dup_or_none msg_id={payload.whatsapp_msg_id}")
    db.commit()
    if msg is None:
        return None
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


def _process_meow_inbound_in_thread(
    payload: WhatsappMeowInboundEvent,
) -> WhatsappMeowMessageRead | None:
    """Session propia: SQLAlchemy no es thread-safe con la session del request async."""
    db = SessionLocal()
    try:
        return process_meow_inbound_event(payload, db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post(
    "/internal/whatsapp/meow/events",
    response_model=WhatsappMeowMessageRead | None,
)
async def post_meow_inbound_event(
    request: Request,
    _: None = Depends(_verify_internal_token),
) -> WhatsappMeowMessageRead | None:
    # Parseo manual: un 422 de Pydantic antes del handler dejaba el inbound en silencio.
    try:
        raw = await request.json()
    except Exception as exc:  # noqa: BLE001
        _hitlog(f"raw_json_error err={exc!s}")
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc}") from exc

    keys = sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
    _hitlog(f"raw_keys={keys}")

    try:
        data = _coerce_inbound_dict(raw)
        payload = WhatsappMeowInboundEvent.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        _hitlog(f"parse_fail err={exc!s} keys={keys}")
        logger.exception("meow inbound parse failed")
        raise HTTPException(status_code=422, detail=f"Invalid payload: {exc}") from exc

    # Sync work fuera del event loop (health/campañas no se cuelgan con inbound WA).
    return await asyncio.to_thread(_process_meow_inbound_in_thread, payload)
