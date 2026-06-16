from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.mvp_outreach import OutreachValidationReportRead
from app.schemas.outreach import OutreachMessageRead


class SequenceTouchPreview(BaseModel):
    day: int
    channel: str
    objective: str
    body_preview: str


class OutreachReadinessCheckRead(BaseModel):
    key: str
    label: str
    ok: bool
    optional: bool = False
    detail: str | None = None


class OutreachChannelDetailRead(BaseModel):
    key: str
    label: str
    ok: bool
    detail: str | None = None


class OutreachReadinessRead(BaseModel):
    is_ready: bool
    channel_count: int = 0
    channels_required: int = 2
    channels_total: int = 3
    channels_summary: str | None = None
    channels_detail: list[OutreachChannelDetailRead] = Field(default_factory=list)
    prep_action: str | None = None
    missing_summary: str | None = None
    checklist: list[OutreachReadinessCheckRead] = Field(default_factory=list)


class OutreachCampaignOptionRead(BaseModel):
    id: int
    name: str
    product_name: str | None = None


class SequenceDebugRead(BaseModel):
    prospect_id: int
    ownership_status: str
    sequence_status: str
    sequence_started_at: datetime | None = None
    has_draft_raw: bool = False
    has_usable_draft: bool = False
    draft_is_corrupt: bool = False
    has_draft: bool = False
    draft_touch_count: int = 0
    touch_log_entries: int = 0
    has_touches: bool = False
    has_timeline: bool = False
    playbook_name: str | None = None
    sequence_id: int | None = None


class SequenceTestingConfigRead(BaseModel):
    real_mode: bool = False
    outreach_simulation_disabled: bool = False
    sequence_testing_enabled: bool = False
    env_nexus_real_mode: str = ""
    env_nexus_disable_outreach_simulation: str = ""
    env_nexus_enable_sequence_testing: str = ""
    enable_sequence_testing_hint: str = ""
    enable_all_simulation_hint: str = ""


class ProspectOutreachContextRead(BaseModel):
    prospect_id: int
    campaign_id: int | None = None
    company_id: int
    prospect_name: str
    prospect_company: str
    prospect_email: str | None
    prospect_linkedin: str | None
    prospect_phone: str | None
    prospect_whatsapp: str | None = None
    prospect_company_website: str | None = None
    ownership_status: str
    owner_user_id: int | None
    campaign_name: str | None = None
    product_name: str | None
    product_description: str | None
    playbook_name: str
    available_channels: list[str]
    campaign_channels: list[str]
    seller_name: str | None
    readiness: OutreachReadinessRead
    campaign_options: list[OutreachCampaignOptionRead] = Field(default_factory=list)
    testing: SequenceTestingConfigRead = Field(default_factory=SequenceTestingConfigRead)
    sequence_debug: SequenceDebugRead | None = None
    can_generate_sequence: bool = False
    can_view_sequence: bool = False
    can_start_sequence: bool = False
    generate_sequence_block_reason: str | None = None
    start_sequence_block_reason: str | None = None


class SequencePreviewRead(BaseModel):
    prospect_id: int
    playbook_name: str
    touches: list[SequenceTouchPreview]


class StartSequenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    prospect_id: int
    ownership_status: str
    sequence_started_at: datetime | None
    next_touch_at: datetime | None
    next_touch_label: str | None
    playbook_name: str | None
    message: str = Field(default="Secuencia iniciada")


class ProspectEnrichRead(BaseModel):
    prospect_id: int
    message: str
    enriched: bool = False
    readiness: OutreachReadinessRead


SequenceStepStatus = Literal["sent", "current", "pending", "failed", "skipped", "respondido"]

TouchStatus = Literal["pendiente", "generado", "enviado", "respondido", "fallido", "omitido"]


class OpenAILastErrorRead(BaseModel):
    model: str | None = None
    error_type: str | None = None
    error: str | None = None
    attempts: int | None = None
    timestamp: str | None = None
    retryable: bool = False


class SequenceStepRead(BaseModel):
    day: int
    channel: str
    objective: str | None = None
    touch_status: TouchStatus = "pendiente"
    status: SequenceStepStatus
    status_label: str
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    subject: str | None = None
    body: str | None = None
    message_body: str | None = None
    message_preview: str | None = None
    message_id: int | None = None
    error_message: str | None = None
    validation_rejection: OutreachValidationReportRead | None = None
    openai_last_error: OpenAILastErrorRead | None = None
    generation_context: dict[str, Any] | None = None
    fallback_test: bool = False
    can_execute: bool = False
    can_skip: bool = False


class SequenceTrackingRead(BaseModel):
    prospect_id: int
    prospect_name: str
    prospect_company: str
    playbook_name: str
    ownership_status: str
    sequence_started_at: datetime | None = None
    sequence_paused: bool = False
    sequence_state: str | None = None
    prospect_status: str | None = None
    current_day: int | None = None
    current_day_label: str | None = None
    next_touch_at: datetime | None = None
    next_touch_label: str | None = None
    last_response_class: str | None = None
    last_response_class_label: str | None = None
    last_reply_objective: str | None = None
    last_reply_objective_label: str | None = None
    last_response_is_testing: bool = False
    suggested_reply: str | None = None
    conversation_state: str | None = None
    conversation_state_label: str | None = None
    last_auto_sent: bool = False
    last_classification_confidence: float | None = None
    last_escalation_reason: str | None = None
    last_delivery_mode: str | None = None
    steps: list[SequenceStepRead] = Field(default_factory=list)
    history: list[SequenceStepRead] = Field(default_factory=list)
    conversation: list[OutreachMessageRead] = Field(default_factory=list)
    testing: SequenceTestingConfigRead = Field(default_factory=SequenceTestingConfigRead)
    sequence_debug: SequenceDebugRead | None = None


class ActiveSequenceSummaryRead(BaseModel):
    prospect_id: int
    prospect_name: str
    company_name: str
    ownership_status: str
    current_day: int | None = None
    current_day_label: str | None = None
    next_touch_label: str | None = None
    next_touch_at: datetime | None = None


class ActiveSequencesWorkspaceRead(BaseModel):
    sequences: list[ActiveSequenceSummaryRead] = Field(default_factory=list)


class ExecuteTouchRead(BaseModel):
    prospect_id: int
    day: int
    touch_status: TouchStatus
    status_label: str
    message: str
    fallback_test: bool = False
    tracking: SequenceTrackingRead


ResponseClass = Literal[
    "interesado",
    "no_interesado",
    "pedir_mas_info",
    "derivar_a_otra_persona",
    "contactar_mas_adelante",
    "respuesta_automatica",
    "desconocido",
]


class SimulateSequenceResponseBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    channel: Literal["email", "linkedin", "whatsapp"] | None = None


class CommercialStateDebugRead(BaseModel):
    inbound_text: str
    response_class: str
    response_class_label: str
    reply_objective: str
    reply_objective_label: str
    previous_commercial_state: str
    previous_commercial_state_label: str
    new_commercial_state: str
    new_commercial_state_label: str
    saved_to_db: bool = True
    is_testing: bool = True


class AgentTurnDebugRead(BaseModel):
    inbound_text: str
    response_class: str
    response_class_label: str
    reply_objective: str
    reply_objective_label: str
    classification_confidence: float
    delivery_mode: Literal["auto_sent", "escalated"] = "escalated"
    auto_sent: bool = False
    channel: str
    escalation_reason: str | None = None
    conversation_state: str = "sin_conversacion"
    conversation_state_label: str = "Sin conversación"
    saved_to_db: bool = True
    is_testing: bool = True


class SimulateSequenceResponseRead(BaseModel):
    prospect_id: int
    affected_day: int | None = None
    response_class: ResponseClass
    response_class_label: str
    reply_objective: str = "seguimiento"
    reply_objective_label: str = "Mantener conversación"
    commercial_state: str = "prospeccion"
    commercial_state_label: str = "Prospección"
    commercial_state_is_testing: bool = True
    commercial_state_debug: CommercialStateDebugRead | None = None
    agent_turn: AgentTurnDebugRead | None = None
    auto_sent: bool = False
    delivery_mode: Literal["auto_sent", "escalated"] = "escalated"
    classification_confidence: float = 0.0
    escalation_reason: str | None = None
    conversation_state: str = "sin_conversacion"
    conversation_state_label: str = "Sin conversación"
    outbound_message: OutreachMessageRead | None = None
    testing: bool = True
    classification_summary: str
    sequence_paused: bool
    sequence_state: str | None = None
    prospect_status: str
    suggested_reply: str | None = None
    suggested_channel: str
    inbound_message: OutreachMessageRead
    conversation: list[OutreachMessageRead] = Field(default_factory=list)
    tracking: SequenceTrackingRead
