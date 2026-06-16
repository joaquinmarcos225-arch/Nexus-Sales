from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    scheduled_for: datetime
    meeting_status: str = Field(default="pending")
    timezone: str = Field(default="America/Argentina/Buenos_Aires", max_length=128)
    duration_minutes: int = Field(default=30, ge=15, le=240)
    prospect_id: int


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    scheduled_for: datetime | None = None
    meeting_status: str | None = None
    timezone: str | None = Field(default=None, max_length=128)
    duration_minutes: int | None = Field(default=None, ge=15, le=240)


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    campaign_id: int
    prospect_id: int
    title: str
    description: str | None
    scheduled_for: datetime
    meeting_status: str
    timezone: str
    suggested_slots: list | None
    duration_minutes: int
    google_calendar_event_id: str | None = None
    google_calendar_html_link: str | None = None
    creation_method: str = "manual"
    created_by_user_id: int | None = None
    created_at: datetime
    updated_at: datetime


class MeetingAcceptSuggestionRead(BaseModel):
    meeting: MeetingRead
    detail: str
