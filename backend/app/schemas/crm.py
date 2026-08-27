from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CrmIntegrationVerifyRead(BaseModel):
    configured: bool = False
    enabled: bool = False
    oauth_configured: bool = False
    company_connected: bool = False
    api_reachable: bool = False
    portal_name: str | None = None
    portal_id: str | None = None
    org_name: str | None = None
    instance_url: str | None = None
    http_status: int | None = None
    verification_summary: str | None = None
    api_error: str | None = None


class CrmSyncFailureRead(BaseModel):
    event_id: int
    prospect_id: int
    prospect_name: str
    event_key: str
    hubspot_synced: bool = False
    salesforce_synced: bool = False
    hubspot_error: str | None = None
    salesforce_error: str | None = None
    last_attempt_at: datetime | None = None


class CrmSyncStatusRead(BaseModel):
    hubspot_active: bool = False
    salesforce_active: bool = False
    pending_count: int = 0
    failed_recent: list[CrmSyncFailureRead] = []


class CrmSyncRetryRead(BaseModel):
    retried: int = 0
    resolved: int = 0


class CrmExclusionSyncProviderRead(BaseModel):
    provider: str
    ok: bool = False
    inserted: int = 0
    updated: int = 0
    total: int = 0
    scanned: int = 0
    error: str | None = None


class CrmExclusionStatusRead(BaseModel):
    total: int = 0
    by_provider: dict[str, int] = {}
    by_type: dict[str, int] = {}
    hubspot_active: bool = False
    salesforce_active: bool = False


class CrmExclusionSyncRead(BaseModel):
    status: CrmExclusionStatusRead
    results: list[CrmExclusionSyncProviderRead] = []


class CrmExclusionManualImportRead(BaseModel):
    status: CrmExclusionStatusRead
    result: CrmExclusionSyncProviderRead


class CrmExclusionManualClearRead(BaseModel):
    status: CrmExclusionStatusRead
    deleted: int = 0
