"""Límites y modo test PhantomBuster (velocidad + query LinkedIn válida)."""

from __future__ import annotations

from app.services.lead_sourcing.env_config import getenv


def is_phantom_test_mode() -> bool:
    raw = (getenv("PHANTOMBUSTER_TEST_MODE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def phantom_test_query() -> str:
    return (getenv("PHANTOMBUSTER_TEST_QUERY") or "Head of Sales SaaS").strip() or "Head of Sales SaaS"


def phantom_test_max_results() -> int:
    raw = (getenv("PHANTOMBUSTER_TEST_MAX_RESULTS") or "25").strip()
    try:
        return max(1, min(100, int(raw)))
    except ValueError:
        return 25


def phantom_poll_timeout_sec() -> float:
    if is_phantom_test_mode():
        raw = (getenv("PHANTOMBUSTER_TEST_TIMEOUT_SEC") or "30").strip()
        try:
            return max(10.0, min(120.0, float(raw)))
        except ValueError:
            return 30.0
    from app.services.lead_sourcing.providers.phantombuster_client import poll_max_sec

    return poll_max_sec()


def phantom_max_companies() -> int:
    if is_phantom_test_mode():
        return 2
    raw = (getenv("PHANTOMBUSTER_MAX_COMPANY_SEARCHES") or "3").strip()
    try:
        return max(1, min(15, int(raw)))
    except ValueError:
        return 3


def phantom_max_roles_per_company() -> int:
    """Máx. roles LinkedIn a probar por empresa (fallback Founder → CEO → …)."""
    raw = (getenv("PHANTOMBUSTER_MAX_ROLES_PER_COMPANY") or "3").strip()
    try:
        return max(1, min(5, int(raw)))
    except ValueError:
        return 3


def phantom_queries_per_company() -> int:
    """Alias legacy — usar phantom_max_roles_per_company."""
    return phantom_max_roles_per_company()


def phantom_skip_company_match_filter() -> bool:
    if is_phantom_test_mode():
        return True
    raw = (getenv("PHANTOMBUSTER_REQUIRE_COMPANY_MATCH") or "1").strip().lower()
    return raw in ("0", "false", "no", "off")


def phantom_output_fetch_max_sec() -> float:
    if is_phantom_test_mode():
        return 10.0
    from app.services.lead_sourcing.timeouts_config import PHANTOMBUSTER_OUTPUT_FETCH_MAX_SEC

    return float(PHANTOMBUSTER_OUTPUT_FETCH_MAX_SEC)
