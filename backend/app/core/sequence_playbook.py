"""
Fuente única de verdad — secuencia Nexus (7 toques + reactivación día 42).

Playbook operativo: días 1, 4, 7, 10, 13, 16, 19 con canal fijo por paso.
La simulación multicanal, la ejecución SDR y la UI deben importar desde acá.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.services.lead_sourcing.mvp_outreach_playbook import (
    DEFAULT_MVP_PLAYBOOK,
    Channel,
    PlaybookStepDef,
    lead_available_channels,
)

PLAYBOOK_NAME = "SDR Nexus 7 toques"
PLAYBOOK_VERSION = "1.0"

PLAYBOOK_DAYS: tuple[int, ...] = tuple(step.day for step in DEFAULT_MVP_PLAYBOOK)
PLAYBOOK_LAST_TOUCH_DAY: int = PLAYBOOK_DAYS[-1]
REACTIVATION_DAY = 42
COOLDOWN_START_DAY = PLAYBOOK_LAST_TOUCH_DAY + 1

TOUCH_MILESTONE_DAYS = PLAYBOOK_DAYS
ALL_MILESTONE_DAYS: tuple[int, ...] = (*PLAYBOOK_DAYS, REACTIVATION_DAY)

# Mapeo legacy (motor multicanal 21d) → playbook actual
LEGACY_MILESTONE_DAY_MAP: dict[int, int] = {14: 13, 18: 16, 21: 19}

PLAYBOOK_EMAIL_DAYS = frozenset(s.day for s in DEFAULT_MVP_PLAYBOOK if s.channel == "email")
PLAYBOOK_LINKEDIN_DAYS = frozenset(s.day for s in DEFAULT_MVP_PLAYBOOK if s.channel == "linkedin")
PLAYBOOK_WHATSAPP_DAYS = frozenset(s.day for s in DEFAULT_MVP_PLAYBOOK if s.channel == "whatsapp")

_CHANNEL_FALLBACK: dict[Channel, tuple[Channel, ...]] = {
    "linkedin": ("email",),
    "whatsapp": ("email",),
    "call": ("email",),
    "email": (),
}


def normalize_milestone_day(day: int) -> int:
    """Normaliza hitos guardados con el esquema antiguo (14/18/21)."""
    try:
        d = int(day)
    except (TypeError, ValueError):
        return day
    return LEGACY_MILESTONE_DAY_MAP.get(d, d)


def normalize_fired_milestones(days: list[int]) -> list[int]:
    return sorted({normalize_milestone_day(d) for d in days})


def playbook_step_for_day(day: int) -> PlaybookStepDef | None:
    d = normalize_milestone_day(day)
    return next((s for s in DEFAULT_MVP_PLAYBOOK if s.day == d), None)


def playbook_channel_for_day(day: int) -> Channel | None:
    step = playbook_step_for_day(day)
    return step.channel if step else None


def resolve_touch_channel(
    day: int,
    *,
    email: str | None,
    linkedin_url: str | None,
    phone: str | None,
    whatsapp_number: str | None,
    allowed_channels: list[str] | None = None,
    channel_plan: dict[int, str] | None = None,
) -> str:
    """
    Canal para un hito: primario del playbook; fallback email si el canal no está disponible.
  Reactivación (42): WhatsApp si hay teléfono, si no email.

    `channel_plan` (opcional): mapa día→canal de una plantilla de secuencia
    personalizada. Si define el día, ese canal pasa a ser el primario (mantiene
    el fallback por disponibilidad). En modo IA se pasa None y se decide por
    disponibilidad como siempre.
    """
    d = normalize_milestone_day(day)
    allowed = [str(c).lower() for c in (allowed_channels or [])]
    planned = None
    if channel_plan:
        planned_raw = channel_plan.get(d)
        if planned_raw:
            planned = str(planned_raw).lower()

    if d == REACTIVATION_DAY:
        available = lead_available_channels(
            email=email,
            linkedin_url=linkedin_url,
            phone=phone,
            whatsapp_number=whatsapp_number,
        )
        # Preferí email mientras WhatsApp no esté en canales permitidos / go-live.
        preferred = (planned, "email", "whatsapp") if planned else ("email", "whatsapp")
        for ch in preferred:
            if ch in available and (not allowed or ch in allowed):
                return ch
        return "email"

    step = playbook_step_for_day(d)
    if step is None and not planned:
        return "email"

    available = lead_available_channels(
        email=email,
        linkedin_url=linkedin_url,
        phone=phone,
        whatsapp_number=whatsapp_number,
    )
    primary: Channel = planned or (step.channel if step else "email")  # type: ignore[assignment]
    candidates: list[Channel] = [primary, *_CHANNEL_FALLBACK.get(primary, ())]
    for ch in candidates:
        if ch not in available:
            continue
        if allowed and ch not in allowed:
            continue
        return ch
    if allowed:
        for ch in ("email", "linkedin", "whatsapp"):
            if ch in allowed and ch in available:
                return ch
    return primary


def is_playbook_touch_day(day: int) -> bool:
    return normalize_milestone_day(day) in PLAYBOOK_DAYS


def is_linkedin_touch_day(day: int) -> bool:
    return normalize_milestone_day(day) in PLAYBOOK_LINKEDIN_DAYS


def sequence_calendar_day_index(
    sequence_started_at: datetime | None,
    now: datetime | None = None,
) -> int:
    """Día calendario 1-based desde el anclaje de secuencia (UTC)."""
    if sequence_started_at is None:
        return 0
    now = now or datetime.now(UTC)
    start = sequence_started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    delta = (now.date() - start.date()).days
    return max(1, delta + 1)


def scheduled_touch_at(sequence_started_at: datetime, day: int) -> datetime:
    start = sequence_started_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start + timedelta(days=max(0, day - 1))


def is_touch_calendar_due(
    sequence_started_at: datetime | None,
    day: int,
    *,
    now: datetime | None = None,
) -> bool:
    if sequence_started_at is None:
        return False
    now = now or datetime.now(UTC)
    if sequence_calendar_day_index(sequence_started_at, now) < day:
        return False
    return scheduled_touch_at(sequence_started_at, day) <= now


def sequence_playbook_public() -> dict[str, Any]:
    """Payload estable para API / contrato con frontend."""
    return {
        "name": PLAYBOOK_NAME,
        "version": PLAYBOOK_VERSION,
        "touch_days": list(PLAYBOOK_DAYS),
        "reactivation_day": REACTIVATION_DAY,
        "all_milestone_days": list(ALL_MILESTONE_DAYS),
        "last_touch_day": PLAYBOOK_LAST_TOUCH_DAY,
        "cooldown_start_day": COOLDOWN_START_DAY,
        "legacy_day_map": dict(LEGACY_MILESTONE_DAY_MAP),
        "steps": [
            {
                "day": s.day,
                "channel": s.channel,
                "objective": s.objective,
            }
            for s in DEFAULT_MVP_PLAYBOOK
        ],
    }
