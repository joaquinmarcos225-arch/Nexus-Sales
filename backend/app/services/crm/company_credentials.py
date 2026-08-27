"""Credenciales CRM por empresa (OAuth en BD) con fallback opcional a env global."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_integration import CompanyIntegration
from app.models.enums import IntegrationStatus
from app.services.crm.config import hubspot_configured, hubspot_enabled, salesforce_enabled
from app.services.oauth_tokens import decrypt_secret, encrypt_secret

_logger = logging.getLogger("nexus.crm.credentials")

PROVIDER_HUBSPOT = "hubspot"
PROVIDER_SALESFORCE = "salesforce"


def get_integration(db: Session, company_id: int, provider: str) -> CompanyIntegration | None:
    return db.scalars(
        select(CompanyIntegration).where(
            CompanyIntegration.company_id == company_id,
            CompanyIntegration.provider == provider,
        )
    ).first()


def company_hubspot_connected(db: Session, company_id: int) -> bool:
    row = get_integration(db, company_id, PROVIDER_HUBSPOT)
    return bool(
        row
        and row.status == IntegrationStatus.connected.value
        and row.access_token_encrypted
    )


def company_salesforce_connected(db: Session, company_id: int) -> bool:
    row = get_integration(db, company_id, PROVIDER_SALESFORCE)
    return bool(
        row
        and row.status == IntegrationStatus.connected.value
        and (row.refresh_token_encrypted or row.access_token_encrypted)
    )


def hubspot_active(db: Session, company_id: int) -> bool:
    if company_hubspot_connected(db, company_id):
        return True
    return hubspot_enabled()


def salesforce_active(db: Session, company_id: int) -> bool:
    if company_salesforce_connected(db, company_id):
        return True
    return salesforce_enabled()


def _metadata(row: CompanyIntegration) -> dict[str, Any]:
    raw = (row.metadata_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _save_metadata(row: CompanyIntegration, data: dict[str, Any]) -> None:
    row.metadata_json = json.dumps(data, ensure_ascii=False)


def upsert_company_integration(
    db: Session,
    *,
    company_id: int,
    provider: str,
    connected_by_user_id: int,
    access_token: str,
    refresh_token: str | None,
    expires_in: int | None,
    external_label: str | None = None,
    external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CompanyIntegration:
    now = datetime.now(UTC)
    row = get_integration(db, company_id, provider)
    if row is None:
        row = CompanyIntegration(company_id=company_id, provider=provider)
        db.add(row)
    row.status = IntegrationStatus.connected.value
    row.connected_by_user_id = connected_by_user_id
    row.connected_at = now
    row.access_token_encrypted = encrypt_secret(access_token)
    if refresh_token:
        row.refresh_token_encrypted = encrypt_secret(refresh_token)
    if expires_in:
        row.token_expires_at = now + timedelta(seconds=max(60, int(expires_in) - 120))
    if external_label:
        row.external_label = external_label[:255]
    if external_id:
        row.external_id = external_id[:255]
    if metadata:
        _save_metadata(row, metadata)
    return row


def disconnect_company_integration(db: Session, company_id: int, provider: str) -> bool:
    row = get_integration(db, company_id, provider)
    if row is None:
        return False
    db.delete(row)
    return True


def _needs_hubspot_refresh(row: CompanyIntegration, now: datetime) -> bool:
    exp = row.token_expires_at
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return now >= exp


def get_hubspot_access_token(db: Session, company_id: int) -> str | None:
    import os

    row = get_integration(db, company_id, PROVIDER_HUBSPOT)
    if row and row.status == IntegrationStatus.connected.value and row.access_token_encrypted:
        now = datetime.now(UTC)
        access = decrypt_secret(row.access_token_encrypted)
        if _needs_hubspot_refresh(row, now) and row.refresh_token_encrypted:
            from app.services.crm import hubspot_oauth

            try:
                payload = hubspot_oauth.refresh_access_token(
                    decrypt_secret(row.refresh_token_encrypted)
                )
                access = str(payload.get("access_token") or access)
                row.access_token_encrypted = encrypt_secret(access)
                refresh = payload.get("refresh_token")
                if refresh:
                    row.refresh_token_encrypted = encrypt_secret(str(refresh))
                expires_in = payload.get("expires_in")
                if expires_in:
                    row.token_expires_at = now + timedelta(seconds=max(60, int(expires_in) - 120))
                db.flush()
            except Exception as exc:
                _logger.warning("HubSpot refresh company=%s: %s", company_id, exc)
                row.status = IntegrationStatus.error.value
                db.flush()
                return None
        return access
    if hubspot_enabled():
        return (os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip() or None
    return None


def get_salesforce_auth(db: Session, company_id: int) -> tuple[str, str] | None:
    import os

    row = get_integration(db, company_id, PROVIDER_SALESFORCE)
    if row and row.status == IntegrationStatus.connected.value:
        meta = _metadata(row)
        instance_url = (meta.get("instance_url") or row.external_id or "").strip().rstrip("/")
        access = (
            decrypt_secret(row.access_token_encrypted) if row.access_token_encrypted else None
        )
        now = datetime.now(UTC)
        needs_refresh = (
            not access
            or (row.token_expires_at and row.token_expires_at <= now)
        )
        if needs_refresh and row.refresh_token_encrypted:
            from app.services.crm import salesforce_oauth

            try:
                payload = salesforce_oauth.refresh_access_token(
                    decrypt_secret(row.refresh_token_encrypted)
                )
                access = str(payload.get("access_token") or "")
                instance_url = str(
                    payload.get("instance_url") or instance_url or ""
                ).strip().rstrip("/")
                row.access_token_encrypted = encrypt_secret(access)
                if instance_url:
                    meta["instance_url"] = instance_url
                    row.external_id = instance_url[:255]
                    _save_metadata(row, meta)
                expires_in = payload.get("expires_in")
                if expires_in:
                    row.token_expires_at = now + timedelta(seconds=max(60, int(expires_in) - 120))
                db.flush()
            except Exception as exc:
                _logger.warning("Salesforce refresh company=%s: %s", company_id, exc)
                row.status = IntegrationStatus.error.value
                db.flush()
                return None
        if access and instance_url:
            return access, instance_url
    if salesforce_enabled():
        from app.services.crm import salesforce as sf_legacy

        return sf_legacy._refresh_access_token()
    return None
