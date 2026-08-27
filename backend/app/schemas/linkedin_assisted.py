from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LinkedInAssistStatus = Literal[
    "none",
    "suggested",
    "prepared",
    "opened",
    "sent",
]

LinkedInAssistEventKind = Literal[
    "linkedin_suggested",
    "linkedin_prepared",
    "linkedin_opened",
    "linkedin_copy",
    "linkedin_sent",
    "linkedin_pending",
]


class LinkedInAssistedPrepareRead(BaseModel):
    message: str = Field(description="Borrador para que el SDR copie y envíe manualmente en LinkedIn.")
    linkedin_url: str | None = None
    assist_status: LinkedInAssistStatus = "suggested"
    session_id: str | None = None


class LinkedInAssistedAssistRead(BaseModel):
    message: str
    linkedin_url: str | None = None
    clipboard_ready: bool = True
    detail: str = ""
    assist_status: LinkedInAssistStatus = "opened"
    session_id: str = Field(description="ID de sesión para futura extensión Chrome / browser assistant.")


class LinkedInAssistedMarkSentRead(BaseModel):
    ok: bool = True
    detail: str = ""
    assist_status: LinkedInAssistStatus = "sent"
    session_id: str | None = None


class LinkedInAssistedRegenerateRead(BaseModel):
    ok: bool = True
    message: str = ""
    assist_status: LinkedInAssistStatus = "suggested"
    openai_used: bool = False
    detail: str = ""


class LinkedInAssistedAbandonRead(BaseModel):
    ok: bool = True
    detail: str = ""
    assist_status: LinkedInAssistStatus = "suggested"


class LinkedInInboundRegisterBody(BaseModel):
    message: str = Field(..., min_length=2, max_length=8000)
    linkedin_message_id: str | None = Field(
        default=None,
        max_length=128,
        description="ID único del mensaje en LinkedIn (extensión) para deduplicar.",
    )


class LinkedInInboundRegisterRead(BaseModel):
    ok: bool = True
    inserted: bool = False
    duplicate: bool = False
    sequence_paused: bool = False
    reply_draft_ready: bool = False
    reply_draft: str | None = None
    reply_available_at: datetime | None = None
    detail: str = ""


class LinkedInResolveProspectRead(BaseModel):
    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    linkedin_url: str
    campaign_id: int | None = None


LinkedInTaskAction = Literal["connect", "verify_connect", "waiting_accept", "message", "reply"]

LinkedInConnectionStatus = Literal[
    "none",
    "checking",
    "check_queued",
    "check_failed",
    "invite_pending",
    "invite_sent",
    "connected",
    "declined",
    "expired",
    "not_connected",
]


class LinkedInPendingConnectCheckRead(BaseModel):
    """Prospecto esperando conexión: Nexus/extensión debe verificar 1º grado sola."""

    prospect_id: int
    campaign_id: int | None = None
    prospect_name: str
    linkedin_url: str
    connection_status: LinkedInConnectionStatus = "invite_sent"


class LinkedInPendingConnectChecksRead(BaseModel):
    items: list[LinkedInPendingConnectCheckRead] = Field(default_factory=list)
    total: int = 0


class LinkedInAssistTaskRead(BaseModel):
    """Tarea operativa para la cola SDR en Notificaciones."""

    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    linkedin_url: str
    linkedin_profile_urn: str | None = None
    message: str
    assist_status: LinkedInAssistStatus
    session_id: str | None = None
    priority: Literal["alta", "media", "baja"] = "media"
    sequence_group: str | None = None
    opened_at: datetime | None = None
    suggested_at: datetime | None = None
    is_reply: bool = False
    action: LinkedInTaskAction = "message"
    connection_status: LinkedInConnectionStatus = "none"


class LinkedInAssistDayBucket(BaseModel):
    day_offset: int = 0
    label: str = "Hoy"
    actionable: bool = True
    invites_limit: int = 0
    invites_scheduled: int = 0
    dms_limit: int = 0
    dms_scheduled: int = 0
    tasks: list[LinkedInAssistTaskRead] = Field(default_factory=list)


class LinkedInAssistQueueRead(BaseModel):
    campaign_id: int
    tasks: list[LinkedInAssistTaskRead] = Field(default_factory=list)
    total_pending: int = 0
    # Cupos diarios por SDR (anti-bloqueo). El excedente se programa en días siguientes.
    invites_remaining: int = 0
    invites_limit: int = 0
    dms_remaining: int = 0
    dms_limit: int = 0
    hidden_by_cap: int = 0
    days: list[LinkedInAssistDayBucket] = Field(default_factory=list)
    # Prospectos en checking: la extensión verifica sola (no aparecen en tasks).
    pending_verify: int = 0


class LinkedInConnectSentRead(BaseModel):
    ok: bool = True
    detail: str = ""
    connection_status: LinkedInConnectionStatus = "invite_sent"


class LinkedInConnectionStatusBody(BaseModel):
    status: LinkedInConnectionStatus = Field(
        description=(
            "Estado detectado por la extensión: connected | not_connected | "
            "invite_sent | declined | checking."
        )
    )


class LinkedInConnectionStatusRead(BaseModel):
    ok: bool = True
    detail: str = ""
    connection_status: LinkedInConnectionStatus = "connected"
    message_ready: bool = False
    message: str | None = None


class LinkedInProfileUrnBody(BaseModel):
    """URN fsd_profile aprendido del botón Mensaje / URL de compose."""

    urn: str | None = Field(default=None, max_length=128)
    compose_url: str | None = Field(default=None, max_length=2048)


class LinkedInProfileUrnRead(BaseModel):
    ok: bool = True
    prospect_id: int
    linkedin_profile_urn: str
    compose_url: str
    detail: str = ""


class LinkedInAssistedSummaryRead(BaseModel):
    ready_for_linkedin: int = Field(description="Prospectos con URL de LinkedIn y estado apto")
    prospects_with_draft: int
    replies_pending_style: int = Field(
        default=0,
        description="Prospectos con conversación activa (aprox.: replied/interested con inbound reciente).",
    )
    marked_sent_today: int
    pending_queue: int = 0
    recommended_daily_connections: int = 20
    recommended_daily_dms: int = 50
    risk_level: str = Field(description="bajo | medio | alto")
