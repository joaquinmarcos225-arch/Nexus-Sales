"""
Plantillas de secuencia — el cliente elige canales y cantidad de toques.

Reglas:
- Máximo 7 toques; cadencia en los días del playbook (1, 4, 7, 10, 13, 16, 19).
- Secuencia personalizada: 1–7 toques (prefijo de esos días); canal por toque.
- Nexus 7 / modo IA: siempre los 7 toques.
- Follow-up (reactivación) opcional a nivel campaña; canal configurable si está activo.
"""

from __future__ import annotations

from typing import Any, Literal

from app.core.sequence_playbook import PLAYBOOK_DAYS, REACTIVATION_DAY
from app.services.lead_sourcing.mvp_outreach_playbook import (
    DEFAULT_MVP_PLAYBOOK,
    Channel,
)

VALID_CHANNELS: tuple[Channel, ...] = ("email", "linkedin", "whatsapp", "call")
VALID_FOLLOWUP_CHANNELS: tuple[str, ...] = ("auto", *VALID_CHANNELS)
SequenceMode = Literal["fixed", "ia"]

# IA necesita al menos esta cantidad de canales distintos para poder variar.
IA_MIN_CHANNELS = 2

SYSTEM_TEMPLATE_NEXUS_7 = "nexus_7"
SYSTEM_TEMPLATE_NEXUS_IA = "nexus_ia"
SYSTEM_TEMPLATE_NEXUS_3_LI_EMAIL_WA = "nexus_3_li_email_wa"
SYSTEM_TEMPLATE_NEXUS_4_LI_EMAIL_CALL = "nexus_4_li_email_call"


def _default_steps() -> list[dict[str, Any]]:
    return [{"day": s.day, "channel": s.channel} for s in DEFAULT_MVP_PLAYBOOK]


def nexus_3_li_email_wa_plan() -> dict[str, Any]:
    """Plantilla recomendada SDR: LinkedIn → Email → WhatsApp (3 toques)."""
    return {
        "template_id": SYSTEM_TEMPLATE_NEXUS_3_LI_EMAIL_WA,
        "template_name": "LinkedIn → Email → WhatsApp",
        "mode": "fixed",
        "is_system": True,
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 4, "channel": "email"},
            {"day": 7, "channel": "whatsapp"},
        ],
        "follow_up": {"enabled": True, "channel": "auto"},
    }


def nexus_4_li_email_call_plan() -> dict[str, Any]:
    """Plantilla SDR con llamada: LinkedIn → Email → Llamada (3 toques)."""
    return {
        "template_id": SYSTEM_TEMPLATE_NEXUS_4_LI_EMAIL_CALL,
        "template_name": "LinkedIn → Email → Llamada",
        "mode": "fixed",
        "is_system": True,
        "steps": [
            {"day": 1, "channel": "linkedin"},
            {"day": 4, "channel": "email"},
            {"day": 7, "channel": "call"},
        ],
        "follow_up": {"enabled": True, "channel": "auto"},
    }


def nexus_7_plan() -> dict[str, Any]:
    """Plantilla de sistema fija: la secuencia aprobada de 7 toques."""
    return {
        "template_id": SYSTEM_TEMPLATE_NEXUS_7,
        "template_name": "Nexus 7 toques",
        "mode": "fixed",
        "is_system": True,
        "steps": _default_steps(),
        "follow_up": {"enabled": True, "channel": "auto"},
    }


def nexus_ia_plan() -> dict[str, Any]:
    """Plantilla de sistema adaptativa: la IA elige el canal de cada toque."""
    return {
        "template_id": SYSTEM_TEMPLATE_NEXUS_IA,
        "template_name": "Nexus IA (adaptativa)",
        "mode": "ia",
        "is_system": True,
        # En modo IA los canales por día se deciden en runtime; se guarda el
        # default como referencia visual.
        "steps": _default_steps(),
        "follow_up": {"enabled": True, "channel": "auto"},
    }


def system_templates() -> list[dict[str, Any]]:
    return [nexus_3_li_email_wa_plan(), nexus_4_li_email_call_plan(), nexus_7_plan(), nexus_ia_plan()]


def default_plan() -> dict[str, Any]:
    return nexus_3_li_email_wa_plan()


def validate_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Normaliza y valida un plan. Lanza ValueError si viola las reglas."""
    if not isinstance(plan, dict):
        raise ValueError("El plan de secuencia debe ser un objeto.")

    mode = str(plan.get("mode") or "fixed").lower()
    if mode not in ("fixed", "ia"):
        raise ValueError("mode debe ser 'fixed' o 'ia'.")

    raw_steps = plan.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("La secuencia necesita al menos 1 toque.")

    n = len(raw_steps)
    max_n = len(PLAYBOOK_DAYS)
    if n > max_n:
        raise ValueError(f"La secuencia admite como máximo {max_n} toques.")
    if mode == "ia" and n != max_n:
        raise ValueError(
            f"La secuencia IA usa los {max_n} toques del playbook "
            f"(días {', '.join(str(d) for d in PLAYBOOK_DAYS)})."
        )

    expected_days = PLAYBOOK_DAYS[:n]
    steps: list[dict[str, Any]] = []
    for expected_day, step in zip(expected_days, raw_steps, strict=True):
        if not isinstance(step, dict):
            raise ValueError("Cada toque debe ser un objeto {day, channel}.")
        try:
            day = int(step.get("day"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Día de toque inválido.") from exc
        if day != expected_day:
            raise ValueError(
                f"Los toques siguen la cadencia Nexus (esperado día {expected_day}, llegó {day})."
            )
        channel = str(step.get("channel") or "").lower()
        if channel not in VALID_CHANNELS:
            raise ValueError(f"Canal inválido en día {day}: {channel!r}.")
        steps.append({"day": day, "channel": channel})

    distinct_channels = {s["channel"] for s in steps}
    if mode == "ia" and len(distinct_channels) < IA_MIN_CHANNELS:
        raise ValueError(
            "La secuencia IA necesita al menos 2 canales distintos para poder variar."
        )

    follow_up_raw = plan.get("follow_up") or {}
    fu_channel = str(follow_up_raw.get("channel") or "auto").lower()
    if fu_channel not in VALID_FOLLOWUP_CHANNELS:
        raise ValueError(f"Canal de follow-up inválido: {fu_channel!r}.")
    follow_up = {
        "enabled": bool(follow_up_raw.get("enabled", True)),
        "channel": fu_channel,
    }

    template_id = str(plan.get("template_id") or "").strip() or None
    template_name = str(plan.get("template_name") or "").strip() or "Secuencia personalizada"

    return {
        "template_id": template_id,
        "template_name": template_name,
        "mode": mode,
        "is_system": bool(plan.get("is_system", False)),
        "steps": steps,
        "follow_up": follow_up,
    }


def plan_channel_map(plan: dict[str, Any] | None) -> dict[int, str] | None:
    """Mapa día→canal para modo 'fixed'. None en modo 'ia' (se decide en runtime)."""
    if not isinstance(plan, dict):
        return None
    if str(plan.get("mode") or "fixed").lower() == "ia":
        return None
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return None
    out: dict[int, str] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        try:
            day = int(step.get("day"))
        except (TypeError, ValueError):
            continue
        channel = str(step.get("channel") or "").lower()
        if channel in VALID_CHANNELS:
            out[day] = channel
    return out or None


def plan_touch_days(plan: dict[str, Any] | None) -> tuple[int, ...]:
    """Días de toque del plan (1–7 del playbook). IA / sin plan → los 7."""
    if not isinstance(plan, dict) or plan_is_ia(plan):
        return tuple(PLAYBOOK_DAYS)
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return tuple(PLAYBOOK_DAYS)
    days: list[int] = []
    playbook_set = set(PLAYBOOK_DAYS)
    for step in steps:
        if not isinstance(step, dict):
            continue
        try:
            day = int(step.get("day"))
        except (TypeError, ValueError):
            continue
        if day in playbook_set and day not in days:
            days.append(day)
    if not days:
        return tuple(PLAYBOOK_DAYS)
    # Mantener orden de cadencia Nexus
    return tuple(d for d in PLAYBOOK_DAYS if d in days)


def plan_last_touch_day(plan: dict[str, Any] | None) -> int:
    days = plan_touch_days(plan)
    return int(days[-1]) if days else int(PLAYBOOK_DAYS[-1])


def plan_is_ia(plan: dict[str, Any] | None) -> bool:
    return isinstance(plan, dict) and str(plan.get("mode") or "fixed").lower() == "ia"


def prospect_has_min_channels(
    *,
    email: str | None,
    linkedin_url: str | None,
    phone: str | None,
    whatsapp_number: str | None,
    allowed_channels: list[str] | None = None,
) -> bool:
    """IA requiere ≥2 canales disponibles; si no, el prospecto se saltea."""
    from app.services.lead_sourcing.mvp_outreach_playbook import lead_available_channels

    available = lead_available_channels(
        email=email,
        linkedin_url=linkedin_url,
        phone=phone,
        whatsapp_number=whatsapp_number,
    )
    allowed = {str(c).lower() for c in (allowed_channels or [])}
    if allowed:
        available = {ch for ch in available if ch in allowed}
    return len(available) >= IA_MIN_CHANNELS


def resolve_ia_touch_channel(
    day: int,
    *,
    email: str | None,
    linkedin_url: str | None,
    phone: str | None,
    whatsapp_number: str | None,
    allowed_channels: list[str] | None = None,
    prior_channels: list[str] | None = None,
) -> str | None:
    """
    Elige canal para un toque en modo IA (sin OpenAI — heurística barata).
    Retorna None si el prospecto no tiene ≥2 canales → se saltea.
    """
    from app.services.lead_sourcing.mvp_outreach_playbook import lead_available_channels

    available_list: list[Channel] = []
    available = lead_available_channels(
        email=email,
        linkedin_url=linkedin_url,
        phone=phone,
        whatsapp_number=whatsapp_number,
    )
    allowed = [str(c).lower() for c in (allowed_channels or [])]
    for ch in ("email", "linkedin", "whatsapp", "call"):
        if ch not in available:
            continue
        if allowed and ch not in allowed:
            continue
        available_list.append(ch)  # type: ignore[arg-type]

    if len(available_list) < IA_MIN_CHANNELS:
        return None

    prior = [str(c).lower() for c in (prior_channels or []) if c]
    last = prior[-1] if prior else None

    # Rotación: preferir un canal distinto al último usado.
    candidates = [c for c in available_list if c != last] or list(available_list)

    # Heurística por día del playbook (sin agregar toques).
    d = int(day)
    if d == REACTIVATION_DAY:
        for pref in ("whatsapp", "email", "linkedin"):
            if pref in candidates:
                return pref
        return candidates[0]

    day_bias: dict[int, tuple[Channel, ...]] = {
        1: ("email", "linkedin", "whatsapp"),
        4: ("linkedin", "email", "whatsapp"),
        7: ("whatsapp", "email", "linkedin"),
        10: ("email", "whatsapp", "linkedin"),
        13: ("linkedin", "whatsapp", "email"),
        16: ("whatsapp", "linkedin", "email"),
        19: ("email", "linkedin", "whatsapp"),
    }
    for pref in day_bias.get(d, ("email", "linkedin", "whatsapp")):
        if pref in candidates:
            return pref
    return candidates[0]


def followup_channel(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return "auto"
    fu = plan.get("follow_up") or {}
    ch = str(fu.get("channel") or "auto").lower()
    return ch if ch in VALID_FOLLOWUP_CHANNELS else "auto"


def followup_enabled(plan: dict[str, Any] | None) -> bool:
    if not isinstance(plan, dict):
        return True
    fu = plan.get("follow_up") or {}
    return bool(fu.get("enabled", True))
