from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.services.crm import company_credentials as cc
from app.services.crm.config import hubspot_configured, hubspot_enabled

_logger = logging.getLogger("nexus.crm.hubspot")

API_BASE = "https://api.hubapi.com"


def _headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def verify_hubspot(
    db: Session,
    company_id: int,
    *,
    deep: bool = True,
) -> dict[str, Any]:
    oauth_ready = False
    try:
        from app.services.crm import hubspot_oauth

        oauth_ready = hubspot_oauth.oauth_is_configured()
    except Exception:
        oauth_ready = False

    company_connected = cc.company_hubspot_connected(db, company_id)
    env_configured = hubspot_configured()

    if not company_connected and not env_configured and not oauth_ready:
        return {
            "configured": False,
            "enabled": False,
            "oauth_configured": False,
            "company_connected": False,
            "api_reachable": False,
            "portal_name": None,
            "portal_id": None,
            "http_status": None,
            "verification_summary": "Conectá HubSpot con el botón o configurá HUBSPOT_CLIENT_ID en el servidor",
            "api_error": None,
        }

    if not cc.hubspot_active(db, company_id):
        return {
            "configured": True,
            "enabled": False,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "portal_name": None,
            "portal_id": None,
            "http_status": None,
            "verification_summary": "HubSpot deshabilitado",
            "api_error": None,
        }

    row = cc.get_integration(db, company_id, cc.PROVIDER_HUBSPOT)
    if not deep:
        summary = (
            "HubSpot conectado (OAuth)"
            if company_connected
            else "Token configurado (verificación rápida)"
        )
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": True,
            "portal_name": row.external_label if row else None,
            "portal_id": row.external_id if row else None,
            "http_status": None,
            "verification_summary": summary,
            "api_error": None,
        }

    token = cc.get_hubspot_access_token(db, company_id)
    if not token:
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "portal_name": None,
            "portal_id": None,
            "http_status": None,
            "verification_summary": "No se pudo obtener token HubSpot",
            "api_error": None,
        }
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{API_BASE}/account-info/v3/details", headers=_headers(token))
        if resp.status_code == 200:
            data = resp.json()
            return {
                "configured": True,
                "enabled": True,
                "oauth_configured": oauth_ready,
                "company_connected": company_connected,
                "api_reachable": True,
                "portal_name": data.get("companyName") or data.get("portalName"),
                "portal_id": str(data.get("portalId") or "") or None,
                "http_status": resp.status_code,
                "verification_summary": "HubSpot conectado — sync de contactos y notas activo",
                "api_error": None,
            }
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "portal_name": None,
            "portal_id": None,
            "http_status": resp.status_code,
            "verification_summary": "Token HubSpot rechazado",
            "api_error": resp.text[:400],
        }
    except Exception as exc:
        _logger.warning("HubSpot verify failed: %s", exc)
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "portal_name": None,
            "portal_id": None,
            "http_status": None,
            "verification_summary": "No se pudo contactar HubSpot API",
            "api_error": str(exc)[:400],
        }


def upsert_contact(
    *,
    access_token: str,
    email: str,
    first_name: str | None,
    last_name: str | None,
    company_name: str | None,
    job_title: str | None = None,
) -> str | None:
    """Crea o actualiza contacto por email. Devuelve HubSpot contact id."""
    email_norm = email.strip().lower()
    if not email_norm or not access_token:
        return None

    props: dict[str, str] = {"email": email_norm}
    if first_name:
        props["firstname"] = first_name[:200]
    if last_name:
        props["lastname"] = last_name[:200]
    if company_name:
        props["company"] = company_name[:200]
    if job_title:
        props["jobtitle"] = job_title[:200]

    payload = {
        "inputs": [
            {
                "idProperty": "email",
                "id": email_norm,
                "properties": props,
            }
        ]
    }
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            f"{API_BASE}/crm/v3/objects/contacts/batch/upsert",
            headers=_headers(access_token),
            json=payload,
        )
    if resp.status_code not in (200, 201):
        _logger.warning("HubSpot upsert %s: %s", resp.status_code, resp.text[:300])
        return None
    data = resp.json()
    results = data.get("results") or []
    if not results:
        return None
    return str(results[0].get("id") or "") or None


def create_note_for_contact(*, access_token: str, contact_id: str, body: str) -> bool:
    if not contact_id or not access_token:
        return False
    note_body = body.strip()[:65000]
    if not note_body:
        return False
    ts_ms = int(datetime.now(UTC).timestamp() * 1000)
    payload = {
        "properties": {
            "hs_note_body": note_body,
            "hs_timestamp": str(ts_ms),
        },
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": 202,
                    }
                ],
            }
        ],
    }
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            f"{API_BASE}/crm/v3/objects/notes",
            headers=_headers(access_token),
            json=payload,
        )
    if resp.status_code not in (200, 201):
        _logger.warning("HubSpot note %s: %s", resp.status_code, resp.text[:300])
        return False
    return True
