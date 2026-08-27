from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import IntegrationProvider, IntegrationStatus


class ConnectionCardRead(BaseModel):
    """Estado de una integración para la UI (siempre una fila por proveedor)."""

    model_config = ConfigDict(from_attributes=True)

    provider: IntegrationProvider
    status: IntegrationStatus
    external_email: str | None = None
    connected_at: datetime | None = None
    updated_at: datetime | None = None


class IntegrationProviderVerifyRead(BaseModel):
    connected: bool = False
    status: str = "not_connected"
    effective_status: str = "not_connected"
    requires_reconnect: bool = False
    has_refresh_token: bool = False
    external_email: str | None = None
    connected_at: datetime | None = None
    updated_at: datetime | None = None
    api_reachable: bool = False
    api_error: str | None = None
    http_status: int | None = None
    scopes_granted: list[str] = []
    verification_summary: str | None = None


class GoogleCalendarVerifyRead(IntegrationProviderVerifyRead):
    can_read_availability: bool = False
    can_create_events: bool = False
    create_event_verified: bool = False


class GoogleIntegrationVerifyRead(BaseModel):
    oauth_configured: bool = True
    gmail: IntegrationProviderVerifyRead
    google_calendar: GoogleCalendarVerifyRead


class WhatsAppIntegrationVerifyRead(BaseModel):
    configured: bool = False
    api_reachable: bool = False
    mode: str | None = None
    dry_run: bool = False
    phone_number_id: str | None = None
    display_phone_number: str | None = None
    http_status: int | None = None
    verification_summary: str | None = None
    api_error: str | None = None
