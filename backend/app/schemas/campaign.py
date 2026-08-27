from datetime import datetime

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    INBOUND_REPLY_DELAY_CHOICES,
    AutopilotStatus,
    CampaignStatus,
    InboundReplyMode,
    MarketScope,
    OutreachEmailMode,
    OutreachMode,
)
from app.schemas.campaign_channels import normalize_allowed_channels
from app.services.campaign_market import normalize_outreach_mode


class ProspectEstimateRequest(BaseModel):
    prospect_count: int = Field(ge=1, le=200_000)


class CampaignIcpAnalysisRead(BaseModel):
    """Resultado de 'Analizar ICP con IA' (persistido en la campaña)."""

    icp_quality: str = ""
    icp_scope: str = ""
    recommendations: str = ""
    suggested_channels: list[str] = Field(default_factory=list)
    message_style: str = ""
    low_response_risk: str = ""
    suggested_initial_prospect_count: int = 0
    notes: str = ""


class ProspectEstimateResponse(BaseModel):
    prospect_count: int
    estimated_meetings_min: int
    estimated_meetings_max: int
    estimated_cost_min: int
    estimated_cost_max: int
    estimated_avg_cost_per_meeting: float


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    seller_id: int = Field(ge=1)
    product_id: int = Field(ge=1)

    # Si el producto es "both", el cliente elige b2b|b2c. Si no, el backend lo fuerza.
    outreach_mode: OutreachMode | None = None

    target_company_size: str | None = None
    target_industry: str | None = None
    target_country: str | None = None
    target_language: str | None = None
    target_role: str | None = None
    target_area: str | None = None
    target_interests: str | None = None

    prospect_count: int = Field(ge=1, le=200_000)

    calendar_link: str = Field(default="", max_length=2048)
    timezone: str = Field(min_length=1, max_length=128)
    available_hours: str = Field(min_length=1)
    tone: str = Field(min_length=1, max_length=255)

    allowed_channels: list[str] = Field(default_factory=lambda: ["linkedin", "email"])

    status: CampaignStatus = Field(default=CampaignStatus.draft)

    sender_name: str | None = Field(default=None, max_length=255)
    sender_email: str | None = Field(default=None, max_length=255)
    ai_context: str | None = Field(default=None, max_length=50_000)
    followup_delay_days: int | None = Field(default=None, ge=1, le=365)
    max_auto_followups: int | None = Field(default=None, ge=1, le=10)
    post_sequence_followup_enabled: bool = Field(default=True)

    outreach_email_mode: OutreachEmailMode = Field(default=OutreachEmailMode.auto_send)
    automation_paused: bool = Field(default=False)
    inbound_reply_mode: InboundReplyMode = Field(default=InboundReplyMode.auto_send)
    inbound_reply_delay_minutes: int = Field(default=2)

    sequence_plan: dict | None = Field(default=None)

    @field_validator("outreach_mode", mode="before")
    @classmethod
    def _normalize_outreach_create(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, OutreachMode):
            return v
        return OutreachMode(normalize_outreach_mode(str(v)))

    @field_validator("sequence_plan")
    @classmethod
    def _validate_sequence_plan(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        from app.core.sequence_templates import validate_plan

        try:
            return validate_plan(v)
        except ValueError as e:
            raise ValueError(str(e)) from e

    @field_validator("allowed_channels")
    @classmethod
    def _validate_channels(cls, v: list[str]) -> list[str]:
        try:
            return normalize_allowed_channels(v)
        except ValueError as e:
            raise ValueError(str(e)) from e

    @field_validator("inbound_reply_delay_minutes")
    @classmethod
    def _validate_inbound_delay(cls, v: int) -> int:
        if v not in INBOUND_REPLY_DELAY_CHOICES:
            raise ValueError("inbound_reply_delay_minutes debe ser 1, 2 o 5")
        return v


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    seller_id: int | None = Field(default=None, ge=1)
    product_id: int | None = Field(default=None, ge=1)

    outreach_mode: OutreachMode | None = None

    target_company_size: str | None = None
    target_industry: str | None = None
    target_country: str | None = None
    target_language: str | None = None
    target_role: str | None = None
    target_area: str | None = None
    target_interests: str | None = None

    prospect_count: int | None = Field(default=None, ge=1, le=200_000)

    calendar_link: str | None = Field(default=None, max_length=2048)
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    available_hours: str | None = Field(default=None, min_length=1)
    tone: str | None = Field(default=None, min_length=1, max_length=255)

    allowed_channels: list[str] | None = None

    status: CampaignStatus | None = None

    autopilot_status: AutopilotStatus | None = None

    sender_name: str | None = Field(default=None, max_length=255)
    sender_email: str | None = Field(default=None, max_length=255)
    ai_context: str | None = Field(default=None, max_length=50_000)
    followup_delay_days: int | None = Field(default=None, ge=1, le=365)
    max_auto_followups: int | None = Field(default=None, ge=1, le=10)
    post_sequence_followup_enabled: bool = Field(default=True)

    outreach_email_mode: OutreachEmailMode | None = None
    automation_paused: bool | None = None
    inbound_reply_mode: InboundReplyMode | None = None
    inbound_reply_delay_minutes: int | None = None

    sequence_plan: dict | None = None

    @field_validator("outreach_mode", mode="before")
    @classmethod
    def _normalize_outreach_update(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, OutreachMode):
            return v
        return OutreachMode(normalize_outreach_mode(str(v)))

    @field_validator("sequence_plan")
    @classmethod
    def _validate_sequence_plan_optional(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        from app.core.sequence_templates import validate_plan

        try:
            return validate_plan(v)
        except ValueError as e:
            raise ValueError(str(e)) from e

    @field_validator("allowed_channels")
    @classmethod
    def _validate_channels_optional(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        try:
            return normalize_allowed_channels(v)
        except ValueError as e:
            raise ValueError(str(e)) from e

    @field_validator("inbound_reply_delay_minutes")
    @classmethod
    def _validate_inbound_delay_optional(cls, v: int | None) -> int | None:
        if v is None:
            return None
        if v not in INBOUND_REPLY_DELAY_CHOICES:
            raise ValueError("inbound_reply_delay_minutes debe ser 1, 2 o 5")
        return v

class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    seller_id: int
    product_id: int

    product_name: str
    seller_name: str

    name: str
    status: CampaignStatus
    autopilot_status: AutopilotStatus = Field(default=AutopilotStatus.off)
    autopilot_last_cycle_at: datetime | None = None
    autopilot_last_cycle_summary: dict | None = None

    outreach_mode: OutreachMode = Field(default=OutreachMode.b2b)
    product_market_scope: MarketScope | None = None

    target_company_size: str | None
    target_industry: str | None
    target_country: str | None
    target_language: str | None
    target_role: str | None
    target_area: str | None = None
    target_interests: str | None = None

    prospect_count: int

    calendar_link: str
    timezone: str
    available_hours: str
    tone: str
    allowed_channels: list[str]

    estimated_meetings_min: int
    estimated_meetings_max: int
    estimated_cost_min: int
    estimated_cost_max: int
    estimated_avg_cost_per_meeting: float

    created_at: datetime
    updated_at: datetime | None = None

    sender_name: str | None = None
    sender_email: str | None = None
    ai_context: str | None = None
    followup_delay_days: int | None = None
    max_auto_followups: int | None = None
    post_sequence_followup_enabled: bool = True

    outreach_email_mode: OutreachEmailMode = Field(default=OutreachEmailMode.auto_send)
    automation_paused: bool = False
    inbound_reply_mode: InboundReplyMode = Field(default=InboundReplyMode.auto_send)
    inbound_reply_delay_minutes: int = Field(default=2)

    icp_ai_last_analysis: dict | None = None
    outreach_activity_log: list[dict[str, Any]] | None = None
    sequence_plan: dict | None = None
