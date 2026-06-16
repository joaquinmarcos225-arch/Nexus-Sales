from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OutreachTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    campaign_id: int
    prospect_id: int | None
    task_kind: str
    title: str
    notes: str | None
    due_at: datetime
    status: str
    created_at: datetime
    updated_at: datetime

    campaign_name: str = "—"
    prospect_name: str = ""
    prospect_company: str = ""
    action_label: str = ""
    headline: str = ""
    reason: str = ""
    suggested_action: str = ""
    priority_score: int = 0


class ScheduledFollowupRunResponse(BaseModel):
    processed: int
    skipped: int
    errors: int
    deferred_resumed: int = 0


class FollowupReprogramRequest(BaseModel):
    days: int = Field(default=3, ge=0, le=30)
