from __future__ import annotations

import os


def _flag(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def hubspot_configured() -> bool:
    return bool((os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip())


def hubspot_enabled() -> bool:
    if not hubspot_configured():
        return False
    raw = (os.getenv("HUBSPOT_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def salesforce_configured() -> bool:
    return bool(
        (os.getenv("SALESFORCE_CLIENT_ID") or "").strip()
        and (os.getenv("SALESFORCE_CLIENT_SECRET") or "").strip()
        and (os.getenv("SALESFORCE_REFRESH_TOKEN") or "").strip()
        and (os.getenv("SALESFORCE_INSTANCE_URL") or "").strip()
    )


def salesforce_enabled() -> bool:
    if not salesforce_configured():
        return False
    raw = (os.getenv("SALESFORCE_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")
