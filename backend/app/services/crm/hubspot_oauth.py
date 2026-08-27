"""HubSpot OAuth 2.0 (app credentials en env; tokens por empresa en BD)."""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import httpx

HUBSPOT_AUTH_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_SCOPES = (
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
    "crm.objects.notes.write",
)


def _client_id() -> str:
    v = (os.getenv("HUBSPOT_CLIENT_ID") or "").strip()
    if not v:
        raise RuntimeError("HUBSPOT_CLIENT_ID no configurado")
    return v


def _client_secret() -> str:
    v = (os.getenv("HUBSPOT_CLIENT_SECRET") or "").strip()
    if not v:
        raise RuntimeError("HUBSPOT_CLIENT_SECRET no configurado")
    return v


def _redirect_uri() -> str:
    v = (os.getenv("HUBSPOT_REDIRECT_URI") or "").strip()
    if not v:
        raise RuntimeError("HUBSPOT_REDIRECT_URI no configurado")
    return v


def oauth_is_configured() -> bool:
    try:
        _client_id()
        _client_secret()
        _redirect_uri()
        return True
    except RuntimeError:
        return False


def oauth_configuration_error() -> str | None:
    try:
        _client_id()
        _client_secret()
        _redirect_uri()
        return None
    except RuntimeError as e:
        return str(e)


def build_authorization_url(*, state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "scope": " ".join(HUBSPOT_SCOPES),
        "state": state,
    }
    return f"{HUBSPOT_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            HUBSPOT_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": _redirect_uri(),
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HubSpot token exchange {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            HUBSPOT_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HubSpot refresh {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def fetch_portal_details(access_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(
            "https://api.hubapi.com/account-info/v3/details",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HubSpot account info {resp.status_code}")
    return resp.json()
