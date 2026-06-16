"""Google OAuth 2.0: URL de autorización, intercambio de code, estado firmado."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Gmail + Calendar completo (incluye calendarList, events, calendarios compartidos, etc.)
# Tras ampliar scopes, el usuario debe volver a conectar Google (consent + refresh token).
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
)


def _client_id() -> str:
    v = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    if not v:
        raise RuntimeError("GOOGLE_CLIENT_ID no configurado")
    return v


def _client_secret() -> str:
    v = (os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not v:
        raise RuntimeError("GOOGLE_CLIENT_SECRET no configurado")
    return v


def oauth_client_id() -> str:
    return _client_id()


def oauth_client_secret() -> str:
    return _client_secret()


def _redirect_uri() -> str:
    v = (os.getenv("GOOGLE_REDIRECT_URI") or "").strip()
    if not v:
        raise RuntimeError("GOOGLE_REDIRECT_URI no configurado")
    return v


def _state_secret() -> bytes:
    raw = (os.getenv("GOOGLE_OAUTH_STATE_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()
    if not raw:
        raise RuntimeError(
            "Definí GOOGLE_OAUTH_STATE_SECRET (recomendado) o al menos GOOGLE_CLIENT_SECRET para firmar el state OAuth.",
        )
    return raw.encode("utf-8")


def _integrations_path() -> str:
    return "/configuracion/integraciones"


def frontend_redirect_success() -> str:
    base = (os.getenv("NEXUS_FRONTEND_URL") or "http://127.0.0.1:5173").rstrip("/")
    return f"{base}{_integrations_path()}?google=connected"


def frontend_redirect_error(code: str, detail: str = "") -> str:
    base = (os.getenv("NEXUS_FRONTEND_URL") or "http://127.0.0.1:5173").rstrip("/")
    q = urllib.parse.urlencode({"google_error": code, "msg": detail[:200]})
    return f"{base}{_integrations_path()}?{q}"


def encode_oauth_state(company_id: int, user_id: int) -> str:
    iat = int(time.time())
    msg = f"{company_id}:{user_id}:{iat}"
    sig = hmac.new(_state_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{msg}:{sig}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("utf-8")


def decode_oauth_state(state: str, *, max_age_sec: int = 900) -> tuple[int, int]:
    try:
        token = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
        msg, sig = token.rsplit(":", 1)
        company_s, user_s, iat_s = msg.split(":", 2)
        company_id = int(company_s)
        user_id = int(user_s)
        iat = int(iat_s)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("state inválido") from e
    if int(time.time()) - iat > max_age_sec:
        raise ValueError("state expirado")
    expected = hmac.new(_state_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("state corrupto")
    return company_id, user_id


def build_authorization_url(*, state: str) -> str:
    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(DEFAULT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    data = {
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=30.0) as client:
        res = client.post(GOOGLE_TOKEN_URL, data=data)
        res.raise_for_status()
        return res.json()


def fetch_google_user_email(access_token: str) -> str | None:
    with httpx.Client(timeout=20.0) as client:
        res = client.get(GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        res.raise_for_status()
        body = res.json()
        return body.get("email")
