"""Delay humano antes de mostrar réplicas LinkedIn en la cola SDR."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from app.models.prospect import Prospect


def linkedin_reply_delay_minutes() -> int:
    raw = (os.getenv("NEXUS_LINKEDIN_REPLY_DELAY_MINUTES") or "0").strip()
    try:
        return max(0, min(30, int(raw)))
    except ValueError:
        return 0


def apply_reply_queue_delay(prospect: Prospect, *, minutes: int | None = None) -> datetime | None:
    """Oculta el borrador de la cola hasta `when` (inbound ya quedó registrado)."""
    mins = linkedin_reply_delay_minutes() if minutes is None else max(0, min(30, int(minutes)))
    if mins <= 0:
        prospect.linkedin_reply_available_at = None
        return None
    when = datetime.now(UTC) + timedelta(minutes=mins)
    prospect.linkedin_reply_available_at = when
    return when


def reply_visible_in_queue(prospect: Prospect, *, now: datetime | None = None) -> bool:
    when = getattr(prospect, "linkedin_reply_available_at", None)
    if when is None:
        return True
    ref = now or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when <= ref
