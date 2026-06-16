"""Telemetría local de llamadas OpenAI (RPM, errores, headers de rate limit)."""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

_LOCK = Lock()
_REQUEST_TS: deque[float] = deque(maxlen=2000)
_LAST_ERRORS: deque[dict[str, Any]] = deque(maxlen=30)
_LAST_SUCCESS_AT: str | None = None
_LAST_PROBE: dict[str, Any] | None = None
_FALLBACK_COUNT = 0


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def mask_api_key(key: str | None) -> str | None:
    k = (key or "").strip()
    if not k:
        return None
    if len(k) <= 12:
        return "****"
    return f"{k[:8]}…{k[-4:]}"


def api_key_meta(key: str | None) -> dict[str, Any]:
    k = (key or "").strip()
    if not k:
        return {
            "configured": False,
            "format_valid": False,
            "key_type": None,
            "masked": None,
            "project_hint": None,
        }
    fmt = k.startswith("sk-proj-") or k.startswith("sk-")
    key_type = "project" if k.startswith("sk-proj-") else "legacy" if k.startswith("sk-") else "unknown"
    return {
        "configured": True,
        "format_valid": fmt,
        "key_type": key_type,
        "masked": mask_api_key(k),
        "project_hint": "sk-proj-* → clave de proyecto OpenAI (revisá proyecto en platform.openai.com)",
    }


def record_request(*, endpoint: str, model: str, success: bool) -> None:
    now = time.monotonic()
    with _LOCK:
        _REQUEST_TS.append(now)
        if success:
            global _LAST_SUCCESS_AT
            _LAST_SUCCESS_AT = _utc_now_iso()


def record_error(
    *,
    endpoint: str,
    model: str,
    error_type: str,
    status_code: int | None,
    error_full: str,
    error_body: Any | None = None,
    rate_limit_headers: dict[str, str] | None = None,
    attempts: int = 1,
) -> dict[str, Any]:
    row = {
        "timestamp": _utc_now_iso(),
        "endpoint": endpoint,
        "model": model,
        "error_type": error_type,
        "status_code": status_code,
        "error_full": error_full[:4000],
        "error_body": error_body,
        "rate_limit_headers": rate_limit_headers or {},
        "attempts": attempts,
    }
    with _LOCK:
        _LAST_ERRORS.appendleft(row)
    return row


def record_fallback() -> None:
    global _FALLBACK_COUNT
    with _LOCK:
        _FALLBACK_COUNT += 1


def set_last_probe(result: dict[str, Any]) -> None:
    global _LAST_PROBE
    with _LOCK:
        _LAST_PROBE = result


def _count_since(seconds: float) -> int:
    cutoff = time.monotonic() - seconds
    with _LOCK:
        return sum(1 for t in _REQUEST_TS if t >= cutoff)


def _extract_rate_limit_headers(exc: Exception) -> dict[str, str]:
    out: dict[str, str] = {}
    response = getattr(exc, "response", None)
    if response is None:
        return out
    headers = getattr(response, "headers", None)
    if headers is None:
        return out
    try:
        items = headers.items() if hasattr(headers, "items") else []
    except Exception:
        return out
    for key, value in items:
        kl = str(key).lower()
        if "ratelimit" in kl or kl in ("x-request-id", "openai-processing-ms"):
            out[str(key)] = str(value)
    return out


def extract_error_details(exc: Exception) -> dict[str, Any]:
    body = getattr(exc, "body", None)
    if body is not None and not isinstance(body, (dict, list)):
        body = str(body)
    return {
        "error_full": str(exc),
        "status_code": getattr(exc, "status_code", None),
        "error_body": body,
        "rate_limit_headers": _extract_rate_limit_headers(exc),
    }


def requests_per_minute() -> int:
    return _count_since(60.0)


def requests_last_5_minutes() -> int:
    return _count_since(300.0)


def possible_request_loop() -> bool:
    rpm = requests_per_minute()
    threshold = int(os.getenv("NEXUS_OPENAI_LOOP_RPM_THRESHOLD", "25") or "25")
    return rpm >= threshold


def build_diagnostics(*, probe: bool = False) -> dict[str, Any]:
    from app.services.openai_fallback import is_openai_fallback_enabled
    from app.services.openai_service import MODEL, OPENAI_ENDPOINT, openai_configured

    key = os.getenv("OPENAI_API_KEY", "").strip()
    meta = api_key_meta(key)

    with _LOCK:
        recent_errors = list(_LAST_ERRORS)[:10]
        fallback_count = _FALLBACK_COUNT
        last_success = _LAST_SUCCESS_AT
        last_probe = dict(_LAST_PROBE) if _LAST_PROBE else None

    diag: dict[str, Any] = {
        "configured": openai_configured(),
        "model": MODEL,
        "model_source": "OPENAI_MODEL env (default: gpt-4.1-mini)",
        "api_key_source": "OPENAI_API_KEY en backend/.env (cargada al iniciar uvicorn)",
        "endpoint": OPENAI_ENDPOINT,
        "api_key": meta,
        "requests_per_minute": requests_per_minute(),
        "requests_last_5_minutes": requests_last_5_minutes(),
        "possible_request_loop": possible_request_loop(),
        "loop_hint": (
            "Muchas llamadas por minuto — el playbook SDR puede hacer hasta 4 intentos de validación "
            "por toque, cada uno con reintentos OpenAI."
            if possible_request_loop()
            else None
        ),
        "fallback_enabled": is_openai_fallback_enabled(),
        "fallback_uses": fallback_count,
        "last_success_at": last_success,
        "recent_errors": recent_errors,
        "last_probe": last_probe,
        "checks": {
            "api_key_configured": meta["configured"],
            "api_key_format_valid": meta["format_valid"],
            "likely_correct_key_type": meta["key_type"] in ("project", "legacy"),
            "credit_or_quota_hint": _credit_hint(recent_errors),
            "rpm_tpm_hint": _rpm_tpm_hint(recent_errors),
        },
    }

    if probe and meta["configured"]:
        diag["last_probe"] = probe_openai_connection()

    return diag


def _credit_hint(errors: list[dict[str, Any]]) -> str | None:
    for e in errors:
        blob = f"{e.get('error_full', '')} {e.get('error_body', '')}".lower()
        if "insufficient_quota" in blob or "billing" in blob or "exceeded your current quota" in blob:
            return "Posible límite de crédito o cuota agotada — revisá billing en platform.openai.com."
    return None


def _rpm_tpm_hint(errors: list[dict[str, Any]]) -> str | None:
    for e in errors:
        headers = e.get("rate_limit_headers") or {}
        if headers:
            parts = [f"{k}={v}" for k, v in headers.items()]
            return "Headers rate limit: " + "; ".join(parts[:8])
        blob = str(e.get("error_full", "")).lower()
        if "rate limit" in blob or "rate_limit" in blob:
            return "Rate limit RPM/TPM — revisá límites del proyecto en OpenAI Platform."
    return None


def probe_openai_connection() -> dict[str, Any]:
    """Probe liviano: models.list (1 request). Solo invocar bajo demanda."""
    from app.services.openai_service import MODEL, _client

    started = _utc_now_iso()
    try:
        client = _client()
        models = client.models.list()
        ids = [m.id for m in getattr(models, "data", [])[:8]]
        result = {
            "ok": True,
            "probed_at": started,
            "probe_type": "models.list",
            "models_sample": ids,
            "target_model_listed": MODEL in ids if ids else None,
            "message": "API key válida — conexión OK.",
        }
    except Exception as exc:
        details = extract_error_details(exc)
        result = {
            "ok": False,
            "probed_at": started,
            "probe_type": "models.list",
            "message": "Falló verificación de API key",
            **details,
        }
    set_last_probe(result)
    return result
