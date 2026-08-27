from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.schemas.datetime_utc import as_utc_datetime_optional


class CommercialSnapshot(BaseModel):
    meetings_pending: int
    meetings_confirmed: int
    meetings_completed: int
    meetings_canceled: int
    meetings_no_show: int
    meetings_total: int
    meeting_completion_rate: float
    pipeline_by_stage: dict[str, int]
    pipeline_open_count: int
    top_campaigns_by_meetings: list[dict[str, int | str | float]]


class IntelligenceSnapshot(BaseModel):
    hot_prospects: int
    pending_scheduled_followups: int
    pending_tasks_total: int
    ia_meeting_nudges: int
    objections_top: list[dict[str, int | str]]
    interest_by_campaign: list[dict[str, int | str | float]]
    industry_response_rates: list[dict[str, int | str | float]]
    suggested_meeting_momentum: int


class AnalyticsTotals(BaseModel):
    campaigns_active: int
    campaigns_paused: int
    campaigns_other: int

    prospects_imported: int
    prospects_active: int
    prospects_contacted: int
    prospects_responded: int
    prospects_interested: int
    meetings_booked: int

    messages_sent: int
    response_rate: float = Field(description="0..1", ge=0, le=1)
    interest_rate: float = Field(description="0..1", ge=0, le=1)

    last_activity_at: datetime | None

    @field_serializer("last_activity_at")
    def _serialize_last_activity(self, value: datetime | None) -> datetime | None:
        return as_utc_datetime_optional(value)


class CampaignAnalyticsRow(BaseModel):
    campaign_id: int
    name: str
    status: str
    seller_id: int | None = None
    seller_name: str

    prospects_active: int
    prospects_contacted: int
    prospects_responded: int
    prospects_interested: int
    prospects_not_interested: int = 0
    prospects_replied: int = 0
    meetings: int
    meetings_scheduled: int = Field(default=0, description="Filas en módulo Meeting")
    messages_sent: int
    last_activity_at: datetime | None

    @field_serializer("last_activity_at")
    def _serialize_last_activity(self, value: datetime | None) -> datetime | None:
        return as_utc_datetime_optional(value)


class SellerAnalyticsRow(BaseModel):
    user_id: int
    name: str
    email: str

    prospects_in_campaigns: int
    prospects_active: int
    messages_sent: int
    responses: int
    interested: int
    meetings: int
    pending_tasks: int

    active_campaigns: int = 0
    response_rate: float = Field(default=0.0, ge=0, le=1)
    interest_rate: float = Field(default=0.0, ge=0, le=1)
    last_activity_at: datetime | None = None

    @field_serializer("last_activity_at")
    def _serialize_last_activity(self, value: datetime | None) -> datetime | None:
        return as_utc_datetime_optional(value)


class WeeklyMeetingsPoint(BaseModel):
    week_label: str
    count: int


class ResponsesByCampaignPoint(BaseModel):
    campaign_id: int
    campaign_name: str
    responses: int


class CompanyAnalyticsRead(BaseModel):
    totals: AnalyticsTotals
    prospect_status_breakdown: dict[str, int]
    intelligence: IntelligenceSnapshot
    commercial: CommercialSnapshot
    campaigns: list[CampaignAnalyticsRow]
    sellers: list[SellerAnalyticsRow]
    weekly_meetings: list[WeeklyMeetingsPoint]
    responses_by_campaign: list[ResponsesByCampaignPoint]


class RecommendedActionItem(BaseModel):
    """Tarea pendiente con contexto legible (sin depender de IDs en UI)."""

    id: int
    task_kind: str
    title: str
    due_at: datetime
    campaign_id: int
    prospect_id: int | None = None
    campaign_name: str = "—"
    prospect_name: str = ""
    prospect_company: str = ""
    action_label: str = ""
    headline: str = ""
    reason: str = ""
    suggested_action: str = ""
    priority_score: int = 0


class AnalyticsDashboardRead(CompanyAnalyticsRead):
    """
    Misma forma que el dashboard actual (`CompanyAnalyticsRead`) más métricas planas
    y arrays alias para clientes que llaman `GET /analytics`.
    """

    total_campaigns: int = Field(description="Campañas de la empresa")
    active_campaigns: int = Field(description="Campañas running o ready")
    total_products: int
    total_prospects: int = Field(description="Total prospectos (suma por status)")
    contacted_prospects: int
    replied_prospects: int
    interested_prospects: int
    booked_meetings: int = Field(description="Prospectos en estado meeting_booked")
    pending_followups: int = Field(description="Tareas scheduled_followup pendientes")
    hot_prospects: int
    meetings_pending: int
    meetings_completed: int

    campaigns_summary: list[CampaignAnalyticsRow]
    team_summary: list[SellerAnalyticsRow]
    funnel: dict[str, int] = Field(description="Mismo que prospect_status_breakdown")
    recommended_actions: list[RecommendedActionItem]

    # Extensiones dashboard (outreach / respuestas / series)
    outreach_messages_by_channel: list[dict[str, Any]] = Field(default_factory=list)
    prospects_no_reply: int = 0
    followups_sent_total: int = 0
    responses_positive: int = 0
    responses_negative: int = 0
    responses_neutral: int = 0
    responses_wants_meeting: int = 0
    objection_counts: dict[str, int] = Field(default_factory=dict)
    responses_campaign_detail: list[dict[str, Any]] = Field(default_factory=list)
    weekly_inbound_responses: list[WeeklyMeetingsPoint] = Field(default_factory=list)
    scatter_response_vs_messages: list[dict[str, Any]] = Field(default_factory=list)
    interest_histogram: list[dict[str, Any]] = Field(default_factory=list)

    avg_reply_hours: float | None = Field(
        default=None,
        description="Promedio horas entre último outbound e inbound (empresa), si hay datos.",
    )

