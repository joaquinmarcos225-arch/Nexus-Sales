"""Tests clasificación errores Prospeo."""

from app.services.lead_sourcing.prospeo_api_health import (
    SEARCH_OUTCOME_BLOCKED_CREDITS,
    SEARCH_OUTCOME_BLOCKED_RATE_LIMIT,
    SEARCH_OUTCOME_NO_RESULTS,
    SEARCH_OUTCOME_OK,
    classify_prospeo_error,
    cleanup_stale_prospeo_meta,
    effective_prospeo_search_blocked,
    is_search_blocked_outcome,
    sanitize_prospeo_health_dict,
)


def test_insufficient_credits():
    outcome, code = classify_prospeo_error(error_code="INSUFFICIENT_CREDITS")
    assert outcome == SEARCH_OUTCOME_BLOCKED_CREDITS
    assert code == "INSUFFICIENT_CREDITS"
    assert is_search_blocked_outcome(outcome)


def test_rate_limit_message():
    outcome, code = classify_prospeo_error(message="Rate limit exceeded", status_code=429)
    assert outcome == SEARCH_OUTCOME_BLOCKED_RATE_LIMIT
    assert code == "RATE_LIMITED"
    assert is_search_blocked_outcome(outcome)


def test_rate_limit_exceeded_code_normalized():
    outcome, code = classify_prospeo_error(error_code="Rate limit exceeded", status_code=429)
    assert outcome == SEARCH_OUTCOME_BLOCKED_RATE_LIMIT
    assert code == "RATE_LIMITED"
    assert effective_prospeo_search_blocked(error_code="Rate limit exceeded")
    assert effective_prospeo_search_blocked(error_code="RATE LIMIT EXCEEDED")


def test_zero_credits_remaining():
    outcome, _ = classify_prospeo_error(remaining_credits=0)
    assert outcome == SEARCH_OUTCOME_BLOCKED_CREDITS


def test_no_results_not_blocked():
    outcome, code = classify_prospeo_error(error_code="NO_RESULTS")
    assert outcome == SEARCH_OUTCOME_NO_RESULTS
    assert not is_search_blocked_outcome(outcome)
    assert code == "NO_RESULTS"


def test_http_200_empty_not_integration_error():
    outcome, code = classify_prospeo_error(error_code=None, status_code=200)
    assert outcome == SEARCH_OUTCOME_OK
    assert not is_search_blocked_outcome(outcome)
    assert code is None


def test_http_200_no_results_code():
    outcome, code = classify_prospeo_error(error_code="NO_RESULTS", status_code=200)
    assert outcome == SEARCH_OUTCOME_NO_RESULTS
    assert not is_search_blocked_outcome(outcome)


def test_http_200_error_code_only_is_ok():
    outcome, code = classify_prospeo_error(error_code="HTTP_200")
    assert outcome == SEARCH_OUTCOME_OK
    assert code is None
    assert not effective_prospeo_search_blocked(error_code="HTTP_200", search_blocked=True)


def test_sanitize_stale_http_200_meta():
    raw = {
        "configured": True,
        "remaining_credits": 2000,
        "current_plan": "STARTER",
        "search_blocked": True,
        "error_code": "HTTP_200",
        "banner_message": "Prospeo sin créditos o limitado por plan",
        "detail": "Error integración Prospeo (HTTP_200)",
    }
    cleaned = sanitize_prospeo_health_dict(raw)
    assert cleaned["search_blocked"] is False
    assert cleaned.get("error_code") is None
    assert cleaned.get("banner_message") is None

    meta, changed = cleanup_stale_prospeo_meta(
        {
            "last_error": "Error integración Prospeo (HTTP_200)",
            "prospeo_health": raw,
            "prospeo_search_debug": [
                {
                    "error_code": "HTTP_200",
                    "search_blocked": True,
                    "prospeo_results": 0,
                }
            ],
        }
    )
    assert changed
    assert "last_error" not in meta
    assert meta["prospeo_health"]["search_blocked"] is False
