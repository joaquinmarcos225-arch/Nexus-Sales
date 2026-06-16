"""Pipeline MVP Lead Sourcing — sin dependencia de PhantomBuster."""

from __future__ import annotations

from app.services.lead_sourcing.env_config import getenv

MVP_PIPELINE_STEPS: tuple[str, ...] = (
    "ICP",
    "Web Search",
    "Prospeo",
    "Nexus Outreach",
)

FULL_PIPELINE_STEPS_WITH_PHANTOM: tuple[str, ...] = (
    "ICP",
    "Web Search",
    "PhantomBuster (experimental)",
    "Prospeo",
    "Nexus Outreach",
)


def phantom_experimental_enabled() -> bool:
    raw = (getenv("ENABLE_PHANTOM_EXPERIMENTAL") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def mvp_substeps_full() -> list[str]:
    if phantom_experimental_enabled():
        return ["companies", "prepare_phantom", "people", "enrich"]
    return ["companies", "enrich"]


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


PHANTOM_PIPELINE_STEPS = frozenset(
    {"prepare_phantom", "extract_companies", "people", "extracting_people", "preparing_phantom", "phantom_ready"}
)


def sanitize_panel_last_error(msg: str | None, *, include_phantom: bool) -> str | None:
    if not msg or include_phantom:
        return msg
    if is_phantom_related_message(msg):
        return None
    return msg
