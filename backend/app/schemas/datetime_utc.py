"""Normalización de datetimes para respuestas API (SQLite suele guardar UTC naive)."""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc_datetime(value: datetime) -> datetime:
    """SQLite guarda UTC sin tz; la API siempre devuelve aware en UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def as_utc_datetime_optional(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return as_utc_datetime(value)
