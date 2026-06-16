from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProspectEmailIndexEntry(BaseModel):
    prospect_id: int
    name: str
    email_raw: str
    email_normalized: str
    campaign_id: int


class AttendeeComparison(BaseModel):
    email_raw: str | None = None
    email_normalized: str
    is_seller: bool = False
    matched_prospect_id: int | None = None
    match: bool = False


class CalendarEventDebugTrace(BaseModel):
    event_id: str
    summary: str | None = None
    start_utc: str | None = None
    google_status: str | None = None
    list_attendees_count: int = 0
    get_http_status: int | None = None
    get_ok: bool = False
    organizer_raw: str | None = None
    organizer_normalized: str | None = None
    all_attendees_normalized: list[str] = Field(default_factory=list)
    guest_attendees_normalized: list[str] = Field(default_factory=list)
    emails_in_description: list[str] = Field(default_factory=list)
    attendee_comparisons: list[AttendeeComparison] = Field(default_factory=list)
    organizer_in_index: bool = False
    matched: bool = False
    match_via: str | None = None
    matched_prospect_id: int | None = None
    matched_email_normalized: str | None = None
    meeting_created: bool = False
    meeting_updated: bool = False
    pipeline_updated: bool = False
    pipeline_skip_reason: str | None = None
    skip_reason: str | None = None
    error: str | None = None
    source_calendar_id: str | None = None


class GoogleCalendarSyncCreate(BaseModel):
    user_id: int = Field(ge=1)
    company_id: int = Field(ge=1)
    campaign_id: int | None = None
    days_back: int = Field(default=90, ge=1, le=365)
    days_forward: int = Field(default=180, ge=1, le=730)
    include_debug: bool = Field(default=True)
    debug_max_events: int = Field(default=50, ge=1, le=200)
    client_now_utc: datetime | None = Field(
        default=None,
        description="Instante UTC del cliente (p. ej. new Date().toISOString()). Ancla timeMin/timeMax si el reloj del servidor está desfasado.",
    )


class GoogleCalendarSyncRead(BaseModel):
    events_seen: int
    events_enriched: int = 0
    prospects_with_email: int = 0
    matched: int
    created: int
    updated: int
    pipeline_updated: int = 0
    timelines_logged: int = 0
    calendar_account: str | None = None
    seller_emails: list[str] = Field(default_factory=list)
    time_window_start_utc: str | None = None
    time_window_end_utc: str | None = None
    errors: list[str] = Field(default_factory=list)
    prospect_email_index: list[ProspectEmailIndexEntry] = Field(default_factory=list)
    debug: list[CalendarEventDebugTrace] = Field(default_factory=list)
    events_list_debug: dict[str, Any] | None = None
    calendar_list_debug: dict[str, Any] | None = None
    time_window_anchor: str | None = Field(
        default=None,
        description="client = ventana anclada al reloj del navegador; server = datetime.now(UTC) del servidor",
    )
    server_now_utc_used: str | None = None
    client_now_utc_received: str | None = None
