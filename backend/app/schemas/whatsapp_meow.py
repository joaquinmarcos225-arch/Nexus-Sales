"""Schemas API WhatsApp meow (sesiones por usuario)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class WhatsappMeowSessionRead(BaseModel):
    session_uuid: str
    label: str | None = None
    status: str
    phone_e164: str | None = None
    display_name: str | None = None
    qr_code: str | None = None
    qr_png_base64: str | None = None
    qr_expires_at: datetime | None = None
    last_error: str | None = None
    connected_at: datetime | None = None
    updated_at: datetime | None = None


class WhatsappMeowEngineStatusRead(BaseModel):
    engine: str
    go_configured: bool = False
    go_reachable: bool = False
    go_simulate: bool = False
    can_pair_real_qr: bool = False
    dev_simulate_tools: bool = False
    user_message: str = ""


class WhatsappMeowSessionCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class WhatsappMeowSendBody(BaseModel):
    to: str = Field(..., min_length=8, max_length=64)
    body: str = Field(..., min_length=1, max_length=4096)
    idempotency_key: str | None = Field(default=None, max_length=64)


class WhatsappMeowMessageRead(BaseModel):
    id: int
    direction: str
    peer: str
    body: str
    status: str
    whatsapp_msg_id: str | None = None
    error: str | None = None
    created_at: datetime


class WhatsappMeowInboundEvent(BaseModel):
    session_id: str
    company_id: int
    user_id: int
    from_: str = Field(alias="from")
    body: str = ""
    quoted_body: str | None = None
    event: str | None = None
    presence_state: str | None = None
    whatsapp_msg_id: str
    timestamp: str | None = None
    # Nota de voz bajada por el servicio Go (Meow); Nexus transcribe acá.
    audio_base64: str | None = Field(default=None, max_length=12_000_000)
    audio_mime: str | None = Field(default=None, max_length=80)

    model_config = {"populate_by_name": True, "extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _normalize_go_payload(cls, data: Any) -> Any:
        # Go a veces manda camelCase o timestamp numérico.
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if not out.get("whatsapp_msg_id"):
            for key in ("WhatsAppMsgID", "whatsappMsgId", "id", "message_id"):
                if out.get(key):
                    out["whatsapp_msg_id"] = str(out[key])
                    break
        ts = out.get("timestamp")
        if ts is not None and not isinstance(ts, str):
            out["timestamp"] = str(ts)
        return out
