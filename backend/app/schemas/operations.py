from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AutomationMode = Literal["manual", "semi_auto", "full_auto"]


class AiDecisionEventRead(BaseModel):
    id: int
    at: datetime
    event_type: str
    decision: str
    summary: str
    campaign_id: int | None = None
    prospect_id: int | None = None
    confidence: float | None = None
    payload: dict[str, Any] | None = None


class OperationsOverviewRead(BaseModel):
    generated_at: str
    global_automation_stop: bool
    real_mode: bool
    scheduler: dict[str, Any]
    integrations: dict[str, Any]
    jobs: list[dict[str, Any]]
    recent_errors: list[dict[str, Any]]
    inbound_auto_reply_tasks: dict[str, int]
    task_queue: dict[str, Any]
    metrics_24h: dict[str, Any]
    metrics_7d: dict[str, Any]
    campaigns: list[dict[str, Any]]
    campaigns_running: int
    campaigns_paused: int


class AutomationModeUpdate(BaseModel):
    mode: AutomationMode


class EmergencyStopUpdate(BaseModel):
    stop: bool = True


class ProspectAiPauseUpdate(BaseModel):
    paused: bool = True
