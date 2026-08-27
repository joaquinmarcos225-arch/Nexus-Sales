"""Canal efectivo por toque según plan de secuencia + canales habilitados (orden preservado)."""

from __future__ import annotations

from typing import Any

from app.core.sequence_playbook import PLAYBOOK_DAYS, normalize_milestone_day, playbook_channel_for_day
from app.core.sequence_templates import plan_channel_map, plan_is_ia, plan_touch_days
from app.models.campaign import Campaign
from app.schemas.campaign_channels import coerce_allowed_channels
from app.services.lead_sourcing.mvp_outreach_playbook import (
    DEFAULT_MVP_PLAYBOOK,
    Channel,
    PlaybookStepDef,
)


def campaign_allowed_channels_ordered(campaign: Campaign | None) -> list[str]:
    if campaign is None:
        return ["linkedin", "email"]
    return coerce_allowed_channels(getattr(campaign, "allowed_channels", None))


def campaign_requires_whatsapp(campaign: Campaign | None) -> bool:
    """True si la campaña usa WhatsApp en canales habilitados o en el plan de secuencia."""
    if campaign is None:
        return False
    if "whatsapp" in campaign_allowed_channels_ordered(campaign):
        return True
    plan = getattr(campaign, "sequence_plan", None)
    if not isinstance(plan, dict):
        return False
    cmap = plan_channel_map(plan)
    wa_channels = {"whatsapp", "wa", "phone"}
    if cmap and any(str(ch).lower() in wa_channels for ch in cmap.values()):
        return True
    for touch in plan.get("touches") or []:
        if isinstance(touch, dict) and str(touch.get("channel") or "").lower() in wa_channels:
            return True
    follow_up = plan.get("follow_up")
    if isinstance(follow_up, dict) and str(follow_up.get("channel") or "").lower() in wa_channels:
        return True
    return False


def campaign_touch_days(campaign: Campaign | None) -> tuple[int, ...]:
    """Días de toque efectivos de la campaña (respeta secuencia personalizada 1–7)."""
    if campaign is None:
        return tuple(PLAYBOOK_DAYS)
    return plan_touch_days(getattr(campaign, "sequence_plan", None))


def _remap_to_allowed(channel: str, allowed: list[str], *, day_index: int) -> str:
    if not allowed:
        return channel
    if channel in allowed:
        return channel
    return allowed[day_index % len(allowed)]


def effective_channel_for_day(campaign: Campaign | None, day: int) -> Channel:
    """
    Canal del toque:
    1) sequence_plan fixed (UI «Crear tu secuencia») si existe
    2) si no hay plan / modo IA: playbook default
    3) siempre se recorta a allowed_channels (orden del usuario)
    """
    d = normalize_milestone_day(day)
    touch_days = list(campaign_touch_days(campaign))
    try:
        day_index = touch_days.index(d)
    except ValueError:
        try:
            day_index = list(PLAYBOOK_DAYS).index(d)
        except ValueError:
            day_index = 0

    allowed = campaign_allowed_channels_ordered(campaign)
    plan = getattr(campaign, "sequence_plan", None) if campaign is not None else None
    cmap = plan_channel_map(plan) if not plan_is_ia(plan) else None

    if cmap and d in cmap:
        primary = str(cmap[d]).lower()
    elif allowed and (plan is None or plan_is_ia(plan)):
        # Sin plan fijo: el orden de allowed_channels define el ciclo de toques.
        primary = allowed[day_index % len(allowed)]
    else:
        primary = playbook_channel_for_day(d) or "email"

    return _remap_to_allowed(primary, allowed, day_index=day_index)  # type: ignore[return-value]


def effective_playbook_steps(campaign: Campaign | None) -> tuple[PlaybookStepDef, ...]:
    """Solo los toques del plan (1–7). La ejecución debe coincidir con lo editado."""
    out: list[PlaybookStepDef] = []
    base_by_day = {s.day: s for s in DEFAULT_MVP_PLAYBOOK}
    for day in campaign_touch_days(campaign):
        base = base_by_day.get(day)
        ch = effective_channel_for_day(campaign, day)
        out.append(
            PlaybookStepDef(
                day=day,
                channel=ch,
                objective=base.objective if base is not None else f"Toque día {day}",
            )
        )
    return tuple(out)


def effective_playbook_step(campaign: Campaign | None, day: int) -> PlaybookStepDef | None:
    d = normalize_milestone_day(day)
    for step in effective_playbook_steps(campaign):
        if step.day == d:
            return step
    if d not in campaign_touch_days(campaign):
        return None
    base = next((s for s in DEFAULT_MVP_PLAYBOOK if s.day == d), None)
    if base is None:
        return None
    return PlaybookStepDef(
        day=base.day,
        channel=effective_channel_for_day(campaign, d),
        objective=base.objective,
    )


def channel_plan_summary(campaign: Campaign | None) -> list[dict[str, Any]]:
    return [{"day": s.day, "channel": s.channel} for s in effective_playbook_steps(campaign)]
