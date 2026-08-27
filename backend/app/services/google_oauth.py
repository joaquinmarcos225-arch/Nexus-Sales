"""Google OAuth 2.0: URL de autorización, intercambio de code, estado firmado."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
import urllib.parse
from typing import Any

import httpx

# Si al pegar en Railway queda un path de Windows delante, Google responde 400.
_CLIENT_ID_RE = re.compile(
    r"(\d{6,}-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com)",
    re.IGNORECASE,
)
_CLIENT_SECRET_RE = re.compile(r"(GOCSPX-[A-Za-z0-9_-]+)")
_HTTP_URL_RE = re.compile(r"https?://[^\s\\\"']+", re.IGNORECASE)


def _clean_env(raw: str | None) -> str:
    v = (raw or "").strip().strip('"').strip("'").replace("\r", "").strip()
    return v.lstrip("\ufeff")


def sanitize_google_client_id(raw: str | None) -> str:
    v = _clean_env(raw)
    m = _CLIENT_ID_RE.search(v)
    return m.group(1) if m else v


def sanitize_google_client_secret(raw: str | None) -> str:
    v = _clean_env(raw)
    m = _CLIENT_SECRET_RE.search(v)
    return m.group(1) if m else v


def sanitize_google_redirect_uri(raw: str | None) -> str:
    v = _clean_env(raw)
    lowered = v.lower()
    idx = lowered.find("https:")
    if idx < 0:
        idx = lowered.find("http:")
    if idx >= 0:
        v = v[idx:].replace("\\", "/")
        v = re.sub(r"^(https?:)/+", r"\1//", v, flags=re.I)
        return v
    m = _HTTP_URL_RE.search(v)
    if m:
        return m.group(0).rstrip("\\")
    return v.rstrip("\\")


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Mínimo que cubre el producto actual (enviar, borradores, leer replies, agenda).
# gmail.modify sobraba: no modificamos labels ni borramos correo.
# calendar completo sobraba: no creamos calendarios ni tocamos ACL.
# Tras cambiar scopes, el usuario debe volver a conectar Google (consent + refresh token).
DEFAULT_SCOPES = (
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
)

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def _client_id() -> str:
    v = sanitize_google_client_id(os.getenv("GOOGLE_CLIENT_ID"))
    if not v:
        raise RuntimeError("GOOGLE_CLIENT_ID no configurado")
    return v


def _client_secret() -> str:
    v = sanitize_google_client_secret(os.getenv("GOOGLE_CLIENT_SECRET"))
    if not v:
        raise RuntimeError("GOOGLE_CLIENT_SECRET no configurado")
    return v


def oauth_is_configured() -> bool:
    try:
        _client_id()
        _client_secret()
        _redirect_uri()
        _state_secret()
        return True
    except RuntimeError:
        return False


def oauth_client_id() -> str:
    return _client_id()


def oauth_client_secret() -> str:
    return _client_secret()


def oauth_configuration_error() -> str | None:
    try:
        _client_id()
        _client_secret()
        _redirect_uri()
        _state_secret()
        return None
    except RuntimeError as e:
        return str(e)


def _redirect_uri() -> str:
    v = sanitize_google_redirect_uri(os.getenv("GOOGLE_REDIRECT_URI"))
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
        "include_granted_scopes": "false",
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


def revoke_google_token(token: str | None) -> None:
    """Best-effort: invalida access/refresh en Google. No lanza si falla."""
    raw = (token or "").strip()
    if not raw:
        return
    try:
        with httpx.Client(timeout=15.0) as client:
            client.post(
                GOOGLE_REVOKE_URL,
                data={"token": raw},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception:
        return
