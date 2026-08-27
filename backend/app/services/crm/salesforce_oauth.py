"""Salesforce OAuth 2.0 (app credentials en env; tokens por empresa en BD)."""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import httpx

SF_SCOPES = "api refresh_token offline_access"


def _client_id() -> str:
    v = (os.getenv("SALESFORCE_CLIENT_ID") or "").strip()
    if not v:
        raise RuntimeError("SALESFORCE_CLIENT_ID no configurado")
    return v


def _client_secret() -> str:
    v = (os.getenv("SALESFORCE_CLIENT_SECRET") or "").strip()
    if not v:
        raise RuntimeError("SALESFORCE_CLIENT_SECRET no configurado")
    return v


def _redirect_uri() -> str:
    v = (os.getenv("SALESFORCE_REDIRECT_URI") or "").strip()
    if not v:
        raise RuntimeError("SALESFORCE_REDIRECT_URI no configurado")
    return v


def _login_url() -> str:
    return (os.getenv("SALESFORCE_LOGIN_URL") or "https://login.salesforce.com").strip().rstrip("/")


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
        "response_type": "code",
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "scope": SF_SCOPES,
        "state": state,
        "prompt": "consent",
    }
    return f"{_login_url()}/services/oauth2/authorize?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            f"{_login_url()}/services/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "redirect_uri": _redirect_uri(),
                "code": code,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Salesforce token exchange {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    with httpx.Client(timeout=25.0) as client:
        resp = client.post(
            f"{_login_url()}/services/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "refresh_token": refresh_token,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Salesforce refresh {resp.status_code}: {resp.text[:300]}")
    return resp.json()
