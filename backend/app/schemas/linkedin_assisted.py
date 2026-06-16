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


class LinkedInAssistedAbandonRead(BaseModel):
    ok: bool = True
    detail: str = ""
    assist_status: LinkedInAssistStatus = "suggested"


class LinkedInAssistTaskRead(BaseModel):
    """Tarea operativa para la cola SDR en Notificaciones."""

    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    linkedin_url: str
    message: str
    assist_status: LinkedInAssistStatus
    session_id: str | None = None
    priority: Literal["alta", "media", "baja"] = "media"
    sequence_group: str | None = None
    opened_at: datetime | None = None
    suggested_at: datetime | None = None


class LinkedInAssistQueueRead(BaseModel):
    campaign_id: int
    tasks: list[LinkedInAssistTaskRead] = Field(default_factory=list)
    total_pending: int = 0


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
