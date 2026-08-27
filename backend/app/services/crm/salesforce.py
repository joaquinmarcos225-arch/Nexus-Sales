from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.services.crm import company_credentials as cc
from app.services.crm.config import salesforce_configured, salesforce_enabled

_logger = logging.getLogger("nexus.crm.salesforce")

_token_cache: dict[str, Any] = {
    "access_token": None,
    "instance_url": None,
    "expires_at": 0.0,
}


def _login_url() -> str:
    return (os.getenv("SALESFORCE_LOGIN_URL") or "https://login.salesforce.com").strip().rstrip("/")


def _api_version() -> str:
    raw = (os.getenv("SALESFORCE_API_VERSION") or "v59.0").strip()
    return raw if raw.startswith("v") else f"v{raw}"


def _configured_instance_url() -> str:
    return (os.getenv("SALESFORCE_INSTANCE_URL") or "").strip().rstrip("/")


def _escape_soql(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _clear_token_cache() -> None:
    _token_cache["access_token"] = None
    _token_cache["instance_url"] = None
    _token_cache["expires_at"] = 0.0


def _refresh_access_token_env(*, force: bool = False) -> tuple[str, str] | None:
    """Fallback: refresh token global en env (legacy)."""
    if not salesforce_configured():
        return None
    now = time.time()
    if (
        not force
        and _token_cache.get("access_token")
        and now < float(_token_cache.get("expires_at") or 0)
    ):
        return str(_token_cache["access_token"]), str(_token_cache["instance_url"])

    client_id = (os.getenv("SALESFORCE_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("SALESFORCE_CLIENT_SECRET") or "").strip()
    refresh_token = (os.getenv("SALESFORCE_REFRESH_TOKEN") or "").strip()

    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(
                f"{_login_url()}/services/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
        if resp.status_code != 200:
            _logger.warning("Salesforce token refresh %s: %s", resp.status_code, resp.text[:300])
            _clear_token_cache()
            return None
        data = resp.json()
        access = str(data.get("access_token") or "").strip()
        instance = str(data.get("instance_url") or _configured_instance_url()).strip().rstrip("/")
        if not access or not instance:
            _clear_token_cache()
            return None
        ttl = int(data.get("expires_in") or 3600)
        _token_cache["access_token"] = access
        _token_cache["instance_url"] = instance
        _token_cache["expires_at"] = now + max(60, ttl - 120)
        return access, instance
    except Exception as exc:
        _logger.warning("Salesforce token refresh failed: %s", exc)
        _clear_token_cache()
        return None


# Compat alias usado por company_credentials fallback
_refresh_access_token = _refresh_access_token_env


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _api_root(instance_url: str) -> str:
    return f"{instance_url.rstrip('/')}/services/data/{_api_version()}"


def verify_salesforce(
    db: Session,
    company_id: int,
    *,
    deep: bool = True,
) -> dict[str, Any]:
    oauth_ready = False
    try:
        from app.services.crm import salesforce_oauth

        oauth_ready = salesforce_oauth.oauth_is_configured()
    except Exception:
        oauth_ready = False

    company_connected = cc.company_salesforce_connected(db, company_id)
    env_configured = salesforce_configured()

    if not company_connected and not env_configured and not oauth_ready:
        return {
            "configured": False,
            "enabled": False,
            "oauth_configured": False,
            "company_connected": False,
            "api_reachable": False,
            "org_name": None,
            "instance_url": None,
            "http_status": None,
            "verification_summary": "Conectá Salesforce con el botón o configurá SALESFORCE_CLIENT_ID en el servidor",
            "api_error": None,
        }

    if not cc.salesforce_active(db, company_id):
        return {
            "configured": True,
            "enabled": False,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "org_name": None,
            "instance_url": _configured_instance_url() or None,
            "http_status": None,
            "verification_summary": "Salesforce deshabilitado",
            "api_error": None,
        }

    row = cc.get_integration(db, company_id, cc.PROVIDER_SALESFORCE)
    if not deep:
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": True,
            "org_name": row.external_label if row else None,
            "instance_url": row.external_id if row else _configured_instance_url() or None,
            "http_status": None,
            "verification_summary": (
                "Salesforce conectado (OAuth)"
                if company_connected
                else "Credenciales configuradas (verificación rápida)"
            ),
            "api_error": None,
        }

    pair = cc.get_salesforce_auth(db, company_id)
    if pair is None:
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "org_name": None,
            "instance_url": _configured_instance_url() or None,
            "http_status": None,
            "verification_summary": "No se pudo renovar el token OAuth de Salesforce",
            "api_error": "refresh_token inválido o app desconectada",
        }

    access_token, instance_url = pair
    try:
        q = quote("SELECT Name FROM Organization LIMIT 1", safe="")
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"{_api_root(instance_url)}/query?q={q}",
                headers=_auth_headers(access_token),
            )
        if resp.status_code == 200:
            rows = resp.json().get("records") or []
            org_name = rows[0].get("Name") if rows else None
            return {
                "configured": True,
                "enabled": True,
                "oauth_configured": oauth_ready,
                "company_connected": company_connected,
                "api_reachable": True,
                "org_name": org_name,
                "instance_url": instance_url,
                "http_status": resp.status_code,
                "verification_summary": "Salesforce conectado — sync de contactos y tareas activo",
                "api_error": None,
            }
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "org_name": None,
            "instance_url": instance_url,
            "http_status": resp.status_code,
            "verification_summary": "Token Salesforce válido pero API rechazó la consulta",
            "api_error": resp.text[:400],
        }
    except Exception as exc:
        _logger.warning("Salesforce verify failed: %s", exc)
        return {
            "configured": True,
            "enabled": True,
            "oauth_configured": oauth_ready,
            "company_connected": company_connected,
            "api_reachable": False,
            "org_name": None,
            "instance_url": instance_url,
            "http_status": None,
            "verification_summary": "No se pudo contactar Salesforce API",
            "api_error": str(exc)[:400],
        }


def _find_contact_id(*, access_token: str, instance_url: str, email: str) -> str | None:
    email_norm = email.strip().lower()
    soql = f"SELECT Id FROM Contact WHERE Email = '{_escape_soql(email_norm)}' LIMIT 1"
    q = quote(soql, safe="")
    with httpx.Client(timeout=25.0) as client:
        resp = client.get(
            f"{_api_root(instance_url)}/query?q={q}",
            headers=_auth_headers(access_token),
        )
    if resp.status_code != 200:
        _logger.warning("Salesforce contact query %s: %s", resp.status_code, resp.text[:300])
        return None
    records = resp.json().get("records") or []
    if not records:
        return None
    return str(records[0].get("Id") or "") or None


def upsert_contact(
    *,
    access_token: str,
    instance_url: str,
    email: str,
    first_name: str | None,
    last_name: str | None,
    company_name: str | None,
    job_title: str | None = None,
) -> str | None:
    email_norm = email.strip().lower()
    if not email_norm or not access_token or not instance_url:
        return None

    existing = _find_contact_id(
        access_token=access_token, instance_url=instance_url, email=email_norm
    )
    last = (last_name or "").strip() or (first_name or "").strip() or "Prospecto"
    first = (first_name or "").strip() or None
    payload: dict[str, Any] = {
        "Email": email_norm,
        "LastName": last[:80],
    }
    if first:
        payload["FirstName"] = first[:40]
    if job_title:
        payload["Title"] = job_title[:128]
    if company_name:
        payload["Description"] = f"Empresa: {company_name[:500]}"

    with httpx.Client(timeout=25.0) as client:
        if existing:
            resp = client.patch(
                f"{_api_root(instance_url)}/sobjects/Contact/{existing}",
                headers=_auth_headers(access_token),
                json=payload,
            )
            if resp.status_code in (200, 204):
                return existing
            _logger.warning("Salesforce contact patch %s: %s", resp.status_code, resp.text[:300])
            return existing if resp.status_code < 500 else None

        resp = client.post(
            f"{_api_root(instance_url)}/sobjects/Contact",
            headers=_auth_headers(access_token),
            json=payload,
        )
    if resp.status_code not in (200, 201):
        _logger.warning("Salesforce contact create %s: %s", resp.status_code, resp.text[:300])
        return None
    return str(resp.json().get("id") or "") or None


def create_task_for_contact(
    *,
    access_token: str,
    instance_url: str,
    contact_id: str,
    subject: str,
    body: str,
) -> bool:
    if not contact_id or not access_token or not instance_url:
        return False
    subject_clean = subject.strip()[:255] or "Nexus outreach"
    description = body.strip()[:32000]
    if not description:
        return False

    payload = {
        "WhoId": contact_id,
        "Subject": subject_clean,
        "Description": description,
        "Status": "Completed",
        "Priority": "Normal",
        "ActivityDate": date.today().isoformat(),
    }
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            f"{_api_root(instance_url)}/sobjects/Task",
            headers=_auth_headers(access_token),
            json=payload,
        )
    if resp.status_code not in (200, 201):
        _logger.warning("Salesforce task %s: %s", resp.status_code, resp.text[:300])
        return False
    return True
