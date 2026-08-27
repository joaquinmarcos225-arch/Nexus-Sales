"""State OAuth firmado para integraciones CRM por empresa."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse

CRM_PROVIDERS = frozenset({"hubspot", "salesforce"})


def _state_secret() -> bytes:
    raw = (
        os.getenv("CRM_OAUTH_STATE_SECRET")
        or os.getenv("NEXUS_TOKEN_FERNET_KEY")
        or os.getenv("GOOGLE_CLIENT_SECRET")
        or ""
    ).strip()
    if not raw:
        raise RuntimeError(
            "Definí CRM_OAUTH_STATE_SECRET o NEXUS_TOKEN_FERNET_KEY para firmar el state OAuth CRM."
        )
    return raw.encode("utf-8")


def frontend_redirect_success(provider: str) -> str:
    base = (os.getenv("NEXUS_FRONTEND_URL") or "http://127.0.0.1:5173").rstrip("/")
    return f"{base}/configuracion/integraciones?{provider}=connected"


def frontend_redirect_error(provider: str, code: str, detail: str = "") -> str:
    base = (os.getenv("NEXUS_FRONTEND_URL") or "http://127.0.0.1:5173").rstrip("/")
    q = urllib.parse.urlencode({f"{provider}_error": code, "msg": detail[:200]})
    return f"{base}/configuracion/integraciones?{q}"


def encode_oauth_state(company_id: int, user_id: int, provider: str) -> str:
    provider = (provider or "").strip().lower()
    if provider not in CRM_PROVIDERS:
        raise ValueError("Proveedor CRM inválido")
    iat = int(time.time())
    msg = f"{company_id}:{user_id}:{provider}:{iat}"
    sig = hmac.new(_state_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{msg}:{sig}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("utf-8")


def decode_oauth_state(state: str, *, max_age_sec: int = 900) -> tuple[int, int, str]:
    try:
        token = base64.urlsafe_b64decode(state.encode("utf-8")).decode("utf-8")
        msg, sig = token.rsplit(":", 1)
        company_s, user_s, provider, iat_s = msg.split(":", 3)
        company_id = int(company_s)
        user_id = int(user_s)
        iat = int(iat_s)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("state inválido") from e
    if provider not in CRM_PROVIDERS:
        raise ValueError("proveedor inválido")
    if int(time.time()) - iat > max_age_sec:
        raise ValueError("state expirado")
    expected = hmac.new(_state_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("state corrupto")
    return company_id, user_id, provider
