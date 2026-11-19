"""Ventana inteligente de envío: días hábiles y horario local de la campaña."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Argentina/Buenos_Aires"
DEFAULT_START_HOUR = 8
DEFAULT_START_MINUTE = 30
DEFAULT_END_HOUR = 19
DEFAULT_END_MINUTE = 0


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def send_window_enabled() -> bool:
    v = (os.getenv("NEXUS_SEND_WINDOW_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def resolve_timezone(name: str | None) -> ZoneInfo:
    raw = (name or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def window_clock() -> tuple[int, int, int, int]:
    """(hora_inicio, min_inicio, hora_fin, min_fin) hora local."""
    start_set = bool((os.getenv("NEXUS_SEND_WINDOW_START") or "").strip())
    start_h = max(0, min(23, _int_env("NEXUS_SEND_WINDOW_START", DEFAULT_START_HOUR)))
    start_m = max(
        0,
        min(
            59,
            _int_env(
                "NEXUS_SEND_WINDOW_START_MINUTE",
                0 if start_set else DEFAULT_START_MINUTE,
            ),
        ),
    )
    end_h = max(1, min(24, _int_env("NEXUS_SEND_WINDOW_END", DEFAULT_END_HOUR)))
    end_m = max(0, min(59, _int_env("NEXUS_SEND_WINDOW_END_MINUTE", DEFAULT_END_MINUTE)))
    if end_h * 60 + end_m <= start_h * 60 + start_m:
        return DEFAULT_START_HOUR, DEFAULT_START_MINUTE, DEFAULT_END_HOUR, DEFAULT_END_MINUTE
    return start_h, start_m, end_h, end_m


def window_bounds() -> tuple[int, int]:
    start_h, _start_m, end_h, _end_m = window_clock()
    return start_h, end_h


def weekdays_only() -> bool:
    v = (os.getenv("NEXUS_SEND_WEEKDAYS_ONLY") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def is_within_send_window(
    now: datetime | None = None,
    *,
    timezone: str | None = None,
) -> bool:
    if not send_window_enabled():
        return True
    ref = now or datetime.now(UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    local = ref.astimezone(resolve_timezone(timezone))
    start_h, start_m, end_h, end_m = window_clock()
    if weekdays_only() and local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return start_h * 60 + start_m <= minutes < end_h * 60 + end_m


def snap_to_send_window(
    when: datetime,
    *,
    timezone: str | None = None,
) -> datetime:
    """Mueve `when` al próximo instante permitido (UTC)."""
    if not send_window_enabled():
        if when.tzinfo is None:
            return when.replace(tzinfo=UTC)
        return when.astimezone(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    tz = resolve_timezone(timezone)
    local = when.astimezone(tz)
    start_h, start_m, end_h, end_m = window_clock()
    start_min = start_h * 60 + start_m
    end_min = end_h * 60 + end_m
    workdays = weekdays_only()

    def _at_open(day: datetime) -> datetime:
        return day.replace(hour=start_h, minute=start_m, second=0, microsecond=0)

    candidate = local
    for _ in range(10):
        if workdays and candidate.weekday() >= 5:
            days = 7 - candidate.weekday()
            candidate = _at_open(candidate + timedelta(days=days))
            continue
        minutes = candidate.hour * 60 + candidate.minute
        if minutes < start_min:
            candidate = _at_open(candidate)
            continue
        if minutes >= end_min:
            candidate = _at_open(candidate + timedelta(days=1))
            continue
        break
    return candidate.astimezone(UTC)
