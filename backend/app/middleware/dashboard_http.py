"""Logs y timeout para endpoints base del dashboard (no Lead Sourcing)."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

_logger = logging.getLogger("nexus.http")

_DASHBOARD_PREFIXES = ("/companies", "/analytics")

_DEFAULT_TIMEOUT_SEC = 25.0
# Explorar hace varias idas a Prospeo; el servicio corta solo con wall-clock + more_pending.
# Si el middleware mata a 25s, el front ve 504 y deja resultados viejos (parece “filtros rotos”).
_EXPLORAR_LONG_MARKERS = ("/explorar/search", "/explorar/potential-customers")
_DEFAULT_EXPLORAR_TIMEOUT_SEC = 90.0


def _is_dashboard_path(path: str) -> bool:
    if path in _DASHBOARD_PREFIXES:
        return True
    return any(path.startswith(prefix + "/") for prefix in _DASHBOARD_PREFIXES)


def _is_explorar_long_path(path: str) -> bool:
    return any(marker in path for marker in _EXPLORAR_LONG_MARKERS)


def dashboard_http_timeout_sec() -> float:
    raw = (os.getenv("NEXUS_HTTP_DASHBOARD_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SEC
    try:
        return max(5.0, min(float(raw), 120.0))
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC


def explorar_http_timeout_sec() -> float:
    raw = (os.getenv("NEXUS_HTTP_EXPLORAR_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_EXPLORAR_TIMEOUT_SEC
    try:
        return max(30.0, min(float(raw), 180.0))
    except ValueError:
        return _DEFAULT_EXPLORAR_TIMEOUT_SEC


async def dashboard_http_guard(request: Request, call_next):
    path = request.url.path
    if not _is_dashboard_path(path):
        return await call_next(request)

    # Explorar: NUNCA matar con wait_for. El servicio corta por wall-clock + more_pending.
    # Un 504 acá dejaba resultados viejos en la UI y parecía “filtros rotos”.
    if _is_explorar_long_path(path):
        t0 = time.perf_counter()
        _logger.info(
            "[dashboard] >>> %s %s client=%s (explorar no-kill)",
            request.method,
            path,
            request.client.host if request.client else "?",
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            _logger.exception(
                "[dashboard] ERROR %s %s elapsed_ms=%s",
                request.method,
                path,
                elapsed_ms,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": f"Error interno en {path}: {type(exc).__name__}: {exc}"},
            )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        soft_limit = explorar_http_timeout_sec()
        if elapsed_ms > soft_limit * 1000:
            _logger.warning(
                "[dashboard] SLOW explorar %s %s elapsed_ms=%s soft_limit_sec=%s",
                request.method,
                path,
                elapsed_ms,
                soft_limit,
            )
        _logger.info(
            "[dashboard] <<< %s %s status=%s elapsed_ms=%s",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
        return response

    timeout = dashboard_http_timeout_sec()
    t0 = time.perf_counter()
    _logger.info(
        "[dashboard] >>> %s %s client=%s",
        request.method,
        path,
        request.client.host if request.client else "?",
    )
    try:
        response = await asyncio.wait_for(call_next(request), timeout=timeout)
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _logger.error(
            "[dashboard] TIMEOUT %s %s elapsed_ms=%s limit_sec=%s",
            request.method,
            path,
            elapsed_ms,
            timeout,
        )
        return JSONResponse(
            status_code=504,
            content={
                "detail": (
                    f"Timeout del servidor ({timeout:.0f}s) en {path}. "
                    "Revisá bloqueos SQLite o jobs del scheduler en logs."
                ),
            },
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _logger.exception(
            "[dashboard] ERROR %s %s elapsed_ms=%s",
            request.method,
            path,
            elapsed_ms,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error interno en {path}: {type(exc).__name__}: {exc}"},
        )

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _logger.info(
        "[dashboard] <<< %s %s status=%s elapsed_ms=%s",
        request.method,
        path,
        response.status_code,
        elapsed_ms,
    )
    return response
