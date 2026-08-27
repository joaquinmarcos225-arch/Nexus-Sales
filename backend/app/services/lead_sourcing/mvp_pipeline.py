"""Pipeline MVP Lead Sourcing — ICP → Web Search → Prospeo → Nexus Outreach."""

from __future__ import annotations

from app.services.lead_sourcing.env_config import getenv

MVP_PIPELINE_STEPS: tuple[str, ...] = (
    "ICP",
    "Web Search",
    "Prospeo",
    "Nexus Outreach",
)

PHANTOM_PIPELINE_STEPS = frozenset(
    {
        "prepare_phantom",
        "extract_companies",
        "people",
        "extracting_people",
        "preparing_phantom",
        "phantom_ready",
    }
)


def mvp_substeps_full() -> list[str]:
    return ["companies", "enrich"]


def get_min_lead_display_score() -> int:
    raw = (getenv("LEAD_SOURCING_MIN_DISPLAY_SCORE") or "30").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 30
    return max(0, min(100, n))


def is_phantom_related_message(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    markers = (
        "phantombuster",
        "phantom buster",
        "phantom_",
        "linkedin_agent_id",
        "container_id",
        "launch_argument",
        "prepare_phantom",
        "extract_companies",
        "extracting_people",
    )
    return any(m in t for m in markers)


def sanitize_panel_last_error(msg: str | None, *, include_phantom: bool = False) -> str | None:
    if not msg or include_phantom:
        return msg
    if is_phantom_related_message(msg):
        return None
    return msg
