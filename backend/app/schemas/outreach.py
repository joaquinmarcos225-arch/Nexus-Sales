from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutreachMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    prospect_id: int
    campaign_id: int
    sender_type: Literal["ai", "prospect", "system", "user"]
    message: str
    channel: Literal["linkedin", "email", "whatsapp"]
    direction: Literal["outbound", "inbound"]
    testing: bool = Field(default=False, alias="is_testing")
    created_at: datetime


class OutreachSequenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    is_running: bool
    current_step: int
    created_at: datetime
    updated_at: datetime


class OutreachStats(BaseModel):
    """contacted/responded = prospectos distintos con mensajes outbound/inbound reales en la campaña."""

    contacted: int
    responded: int
    interested: int
    not_interested: int
    failed: int
    total_prospects: int = 0
    prospects_pending_contact: int = 0
    messages_outbound: int = 0
    messages_inbound: int = 0


class OutreachCampaignRead(BaseModel):
    sequence: OutreachSequenceRead
    stats: OutreachStats
    last_messages: list[OutreachMessageRead]
    pending_operational_tasks: int = Field(
        default=0,
        description="Tareas pendientes (seguimiento, postergación, revisión inbound, etc.) en la campaña",
    )
    real_mode: bool = Field(default=False, description="NEXUS_REAL_MODE: métricas y autopilot sin simulación.")
    simulation_disabled: bool = Field(
        default=False,
        description="Simulaciones de inbound / prospectos demo deshabilitadas por configuración.",
    )
    sequence_block: dict | None = Field(
        default=None,
        description=(
            "Si la secuencia espera reconectar una extensión/integración: "
            "channel, error exacto, code, action."
        ),
    )
    progress_note: str | None = Field(
        default=None,
        description="Último aviso operativo (búsqueda / secuencia / espera).",
    )


class ContinueWithoutChannelBody(BaseModel):
    channel: Literal["linkedin", "whatsapp", "email"]
    confirm: bool = Field(
        default=False,
        description="Debe ser true: confirma omitir el canal y seguir con el resto del plan.",
    )


class ContinueWithoutChannelResponse(BaseModel):
    ok: bool
    channel: str | None = None
    allowed_channels: list[str] = Field(default_factory=list)
    omitted_touches: int = 0
    advanced_prospects: int = 0
    message: str | None = None
    detail: str | None = None
    sequence_block: dict | None = None


class OutreachStartResponse(BaseModel):
    sequence: OutreachSequenceRead
    contacted_now: int
    drafts: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0
    error_messages: list[str] = Field(default_factory=list)
    campaign_status: str | None = None
    gmail_connected: bool = False
    used_gmail: bool = False
    sourcing_ran: bool = False
    sourcing_queued: bool = False
    sourcing_imported: int = 0
    sourcing_message: str | None = None
    sourcing_quota_met: bool = False
    sourcing_prospect_count_after: int = 0
    sourcing_prospect_count_target: int = 0
    channel_enrich_pending: int = 0


class ProspectResponseSimulationRead(BaseModel):
    prospect_id: int
    new_status: str
    messages: list[OutreachMessageRead]


class FollowupPreviewRead(BaseModel):
    prospect_id: int
    message: str


class ManualFollowupActionRead(BaseModel):
    ok: bool
    detail: str


class ProspectReanalysisRead(BaseModel):
    prospect_id: int
    status: str
    interest_probability: int
    objection_type: str | None
    next_best_action: str | None
    score_reason: str | None


class SimulateResponsesBatchRead(BaseModel):
    simulated: int
    skipped: int
    errors: list[str]
    detail: str = ""


class ConversationMessageRead(BaseModel):
    id: int
    prospect_id: int
    campaign_id: int
    sender_type: str
    message: str
    channel: str
    direction: str
    is_testing: bool = False
    is_auto_sent: bool = False
    created_at: datetime


class ConversationMeetingRead(BaseModel):
    id: int
    title: str
    scheduled_for: datetime
    meeting_status: str
    duration_minutes: int
    google_calendar_event_id: str | None = None
    google_calendar_html_link: str | None = None
    calendar_confirmed: bool = False
    creation_method: str = "manual"
    created_by_user_id: int | None = None


class ConversationTurnRead(BaseModel):
    day: int | None = None
    inbound_text: str | None = None
    response_class: str | None = None
    response_class_label: str | None = None
    reply_objective: str | None = None
    reply_objective_label: str | None = None
    classification_confidence: float | None = None
    auto_sent: bool = False
    delivery_mode: str | None = None
    escalation_reason: str | None = None
    inbound_at: str | None = None
    meeting_id: int | None = None
    google_calendar_html_link: str | None = None
    testing: bool = False


class ProspectConversationWorkspaceRead(BaseModel):
    prospect_id: int
    prospect_name: str
    prospect_company: str
    prospect_email: str | None = None
    conversation_state: str = "sin_conversacion"
    conversation_state_label: str = "Sin conversación"
    commercial_state: str = "prospeccion"
    commercial_state_label: str = "Prospección"
    commercial_state_is_testing: bool = False
    messages: list[ConversationMessageRead] = Field(default_factory=list)
    meetings: list[ConversationMeetingRead] = Field(default_factory=list)
    turns: list[ConversationTurnRead] = Field(default_factory=list)
    message_count: int = 0
    has_active_conversation: bool = False
