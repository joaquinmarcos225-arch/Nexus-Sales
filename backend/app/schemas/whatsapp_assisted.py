from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

WhatsAppAssistStatus = Literal[
    "none",
    "suggested",
    "prepared",
    "opened",
    "sent",
]


class WhatsAppAssistedAssistRead(BaseModel):
    message: str
    phone_digits: str
    send_url: str
    app_send_url: str | None = None
    desktop_protocol_url: str | None = None
    clipboard_ready: bool = True
    detail: str = ""
    assist_status: WhatsAppAssistStatus = "opened"
    session_id: str = Field(description="ID de sesión para extensión Chrome / WhatsApp Web.")


class WhatsAppAssistedMarkSentRead(BaseModel):
    ok: bool = True
    detail: str = ""
    assist_status: WhatsAppAssistStatus = "sent"
    session_id: str | None = None


class WhatsAppAssistedAbandonRead(BaseModel):
    ok: bool = True
    detail: str = ""
    assist_status: WhatsAppAssistStatus = "suggested"


class WhatsAppInboundRegisterBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    whatsapp_message_id: str | None = Field(
        default=None,
        max_length=128,
        description="ID único del mensaje (Meta wamid o fingerprint extensión) para deduplicar.",
    )
    prepare_reply_draft: bool = Field(
        default=True,
        description="Si False, solo persiste el inbound (sin generar borrador de réplica).",
    )


class WhatsAppInboundRegisterRead(BaseModel):
    ok: bool = True
    inserted: bool = False
    duplicate: bool = False
    sequence_paused: bool = False
    reply_draft_ready: bool = False
    reply_draft: str | None = None
    calendar_reconnect_required: bool = False
    operator_message: str | None = None
    detail: str = ""


class WhatsAppResolveProspectRead(BaseModel):
    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    phone_digits: str
    campaign_id: int | None = None


class WhatsAppAssistTaskRead(BaseModel):
    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    phone_digits: str
    phone_display: str = ""
    message: str
    assist_status: WhatsAppAssistStatus
    session_id: str | None = None
    priority: Literal["alta", "media", "baja"] = "media"
    sequence_group: str | None = None
    opened_at: datetime | None = None
    send_url: str | None = None
    app_send_url: str | None = None
    desktop_protocol_url: str | None = None


class WhatsAppAssistDayBucket(BaseModel):
    day_offset: int = 0
    label: str = "Hoy"
    actionable: bool = True
    limit: int = 0
    scheduled: int = 0
    tasks: list[WhatsAppAssistTaskRead] = Field(default_factory=list)


class WhatsAppAssistQueueRead(BaseModel):
    campaign_id: int
    tasks: list[WhatsAppAssistTaskRead] = Field(default_factory=list)
    total_pending: int = 0
    limit: int = 0
    effective_limit_today: int = 0
    bonus_from_replies: int = 0
    remaining_today: int = 0
    hidden_by_cap: int = 0
    days: list[WhatsAppAssistDayBucket] = Field(default_factory=list)
