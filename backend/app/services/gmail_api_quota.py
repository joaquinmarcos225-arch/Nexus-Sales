"""
Presupuesto de unidades Gmail API (query cost) para inbound sync.

Variables de entorno (defaults conservadores si no están definidas):
- NEXUS_GMAIL_QUOTA_UNITS_PER_TICK: unidades por operador/tick o sync manual (default 80).
- NEXUS_GMAIL_INBOUND_PROSPECTS_PER_CAMPAIGN: prospectos máx. por campaña/tick (default 20).
- NEXUS_GMAIL_DRAFT_RECONCILE_MIN_UNITS: piso para reconcile de drafts (default 20).
- NEXUS_GMAIL_RETRY_DEADLINE_SEC: tope de reintentos vs lock ~90s (default 85).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

# Costes estimados (conservadores) por llamada Gmail API.
UNIT_PROFILE = 1
UNIT_MESSAGES_LIST = 5
UNIT_MESSAGE_METADATA = 2
UNIT_MESSAGE_FULL = 5
UNIT_THREAD_GET = 5


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def quota_units_per_tick() -> int:
    return _env_int("NEXUS_GMAIL_QUOTA_UNITS_PER_TICK", 80)


def inbound_prospects_per_campaign() -> int:
    return _env_int("NEXUS_GMAIL_INBOUND_PROSPECTS_PER_CAMPAIGN", 20)


def draft_reconcile_min_units() -> int:
    return _env_int("NEXUS_GMAIL_DRAFT_RECONCILE_MIN_UNITS", 20)


def retry_deadline_seconds() -> int:
    return _env_int("NEXUS_GMAIL_RETRY_DEADLINE_SEC", 85)


class GmailApiError(Exception):
    """Error parseado de la Gmail API."""

    def __init__(
        self,
        *,
        http_status: int,
        status: str | None = None,
        reasons: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        self.http_status = http_status
        self.status = status
        self.reasons = reasons or []
        self.message = message
        super().__init__(message or f"Gmail API HTTP {http_status}")


class GmailRateLimitError(GmailApiError):
    """Cuota Google: rateLimitExceeded / Total Query Cost."""


class GmailQuotaExhausted(Exception):
    """Presupuesto local de unidades agotado (diferir, no es error de permisos)."""


def parse_gmail_error_response(response: httpx.Response | None) -> GmailApiError | None:
    if response is None:
        return None
    try:
        body = response.json()
    except Exception:
        return GmailApiError(
            http_status=response.status_code,
            message=(response.text or "")[:300],
        )
    if not isinstance(body, dict) or "error" not in body:
        return GmailApiError(http_status=response.status_code, message=(response.text or "")[:300])
    err = body["error"]
    if not isinstance(err, dict):
        return GmailApiError(http_status=response.status_code)
    reasons = [
        str(x.get("reason") or "")
        for x in (err.get("errors") or [])
        if isinstance(x, dict) and x.get("reason")
    ]
    message = str(err.get("message") or "")
    base = GmailApiError(
        http_status=response.status_code,
        status=str(err.get("status") or "") or None,
        reasons=reasons,
        message=message[:500],
    )
    if response.status_code in (403, 429) and is_rate_limit_error(base):
        return GmailRateLimitError(
            http_status=response.status_code,
            status=base.status,
            reasons=reasons,
            message=message[:500],
        )
    return base


def is_rate_limit_error(err: GmailApiError) -> bool:
    if "rateLimitExceeded" in err.reasons:
        return True
    msg = (err.message or "").lower()
    return "total query cost" in msg or "quota exceeded" in msg


def is_google_invalid_grant(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "invalid_grant" in msg
        or "revocó el acceso" in msg
        or "revoc" in msg
        or ("google" in msg and "desconect" in msg)
        or ("conectalo de nuevo" in msg and "integraciones" in msg)
    )


@dataclass
class GmailQuotaBudget:
    """Presupuesto de unidades por operador (tick o sync manual)."""

    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def try_charge(self, units: int) -> bool:
        if units <= 0:
            return True
        if self.used + units > self.limit:
            return False
        self.used += units
        return True

    def charge_or_raise(self, units: int) -> None:
        if not self.try_charge(units):
            raise GmailQuotaExhausted(f"quota exhausted need={units} remaining={self.remaining}")


@dataclass
class GmailRetryPolicy:
    """Reintentos cortos solo para rateLimitExceeded (no bloquear lock 90s)."""

    max_retries: int = 2
    delays_sec: tuple[int, ...] = (5, 15)
    deadline_monotonic: float = field(default_factory=lambda: time.monotonic() + retry_deadline_seconds())

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - time.monotonic())

    def sleep_before_retry(self, attempt_index: int) -> bool:
        if attempt_index >= len(self.delays_sec):
            return False
        delay = self.delays_sec[attempt_index]
        cap = self.remaining_seconds() - 1.0
        if cap <= 0:
            return False
        time.sleep(min(float(delay), cap))
        return True


@dataclass
class GmailQuotaContext:
    budget: GmailQuotaBudget
    retry: GmailRetryPolicy = field(default_factory=GmailRetryPolicy)
    thread_resolve_cache: dict[int, str | None] = field(default_factory=dict)
    rate_limited: bool = False
    quota_exhausted: bool = False
    deferred_prospect_ids: list[int] = field(default_factory=list)
    draft_reconcile_deferred: bool = False

    @classmethod
    def for_tick(cls) -> GmailQuotaContext:
        return cls(
            budget=GmailQuotaBudget(limit=quota_units_per_tick()),
            retry=GmailRetryPolicy(),
        )

    @classmethod
    def for_manual_sync(cls) -> GmailQuotaContext:
        return cls(
            budget=GmailQuotaBudget(limit=quota_units_per_tick()),
            retry=GmailRetryPolicy(),
        )

    def mark_rate_limited(self) -> None:
        self.rate_limited = True

    def mark_quota_exhausted(self) -> None:
        self.quota_exhausted = True

    def should_stop_prospect_loop(self) -> bool:
        return self.rate_limited or self.quota_exhausted

    def public_stats(self) -> dict[str, Any]:
        return {
            "quota_units_used": self.budget.used,
            "quota_units_limit": self.budget.limit,
            "quota_units_remaining": self.budget.remaining,
            "rate_limited": self.rate_limited,
            "quota_exhausted": self.quota_exhausted,
            "prospects_deferred_count": len(self.deferred_prospect_ids),
            "draft_reconcile_deferred": self.draft_reconcile_deferred,
        }
