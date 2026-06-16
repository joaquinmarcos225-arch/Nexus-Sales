"""Estado Prospeo: créditos, plan, rate limit — distinguir 0 real vs API bloqueada."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.timeouts_config import PROSPEO_HTTP_TIMEOUT

_ACCOUNT_INFO = "https://api.prospeo.io/account-information"

# A/B/C/D — ver prospeo_search_diagnostic.search_outcome
SEARCH_OUTCOME_NO_RESULTS = "no_results"
SEARCH_OUTCOME_OK = "ok"
SEARCH_OUTCOME_BLOCKED_CREDITS = "blocked_credits"
SEARCH_OUTCOME_BLOCKED_RATE_LIMIT = "blocked_rate_limit"
SEARCH_OUTCOME_BLOCKED_PLAN = "blocked_plan"
SEARCH_OUTCOME_BLOCKED_AUTH = "blocked_auth"
SEARCH_OUTCOME_INTEGRATION_ERROR = "integration_error"

BLOCKED_SEARCH_OUTCOMES: frozenset[str] = frozenset(
    {
        SEARCH_OUTCOME_BLOCKED_CREDITS,
        SEARCH_OUTCOME_BLOCKED_RATE_LIMIT,
        SEARCH_OUTCOME_BLOCKED_PLAN,
        SEARCH_OUTCOME_BLOCKED_AUTH,
        SEARCH_OUTCOME_INTEGRATION_ERROR,
    }
)

BANNER_BLOCKED = "Prospeo sin créditos o limitado por plan"
STATUS_SEARCH_BLOCKED = "Prospeo no pudo ejecutar búsqueda"

REAL_BLOCK_ERROR_CODES: frozenset[str] = frozenset(
    {
        "INSUFFICIENT_CREDITS",
        "RATE_LIMITED",
        "INVALID_API_KEY",
        "PLAN_REQUIRED",
        "UNAUTHORIZED",
    }
)


def is_http_success_error_code(error_code: str | None) -> bool:
    """HTTP_200 / HTTP_201 no son errores de API."""
    code = (error_code or "").strip().upper()
    if not code.startswith("HTTP_"):
        return False
    suffix = code[5:]
    if not suffix.isdigit():
        return False
    return 200 <= int(suffix) < 300


def effective_prospeo_search_blocked(
    *,
    error_code: str | None = None,
    remaining_credits: int | None = None,
    insufficient_credits: bool = False,
    rate_limited: bool = False,
    search_blocked: bool = False,
) -> bool:
    """True solo si Prospeo no puede buscar por créditos/plan/auth/rate limit real."""
    if is_http_success_error_code(error_code):
        return False
    if remaining_credits is not None and remaining_credits <= 0:
        return True
    code = (error_code or "").strip().upper()
    if code in REAL_BLOCK_ERROR_CODES:
        return True
    if insufficient_credits or rate_limited:
        return True
    return False


@dataclass
class ProspeoHealth:
    configured: bool = False
    remaining_credits: int | None = None
    used_credits: int | None = None
    current_plan: str | None = None
    rate_limited: bool = False
    insufficient_credits: bool = False
    search_blocked: bool = False
    error_code: str | None = None
    banner_message: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "remaining_credits": self.remaining_credits,
            "used_credits": self.used_credits,
            "current_plan": self.current_plan,
            "rate_limited": self.rate_limited,
            "insufficient_credits": self.insufficient_credits,
            "search_blocked": self.search_blocked,
            "error_code": self.error_code,
            "banner_message": self.banner_message,
            "detail": self.detail,
        }


def classify_prospeo_error(
    *,
    error_code: str | None = None,
    status_code: int | None = None,
    message: str | None = None,
    remaining_credits: int | None = None,
) -> tuple[str, str | None]:
    """Devuelve (search_outcome, error_code normalizado)."""
    code = (error_code or "").strip().upper()
    msg = (message or "").strip()
    msg_l = msg.lower()

    if is_http_success_error_code(code):
        return SEARCH_OUTCOME_OK, None

    if remaining_credits is not None and remaining_credits <= 0:
        return SEARCH_OUTCOME_BLOCKED_CREDITS, code or "INSUFFICIENT_CREDITS"

    if code == "INSUFFICIENT_CREDITS" or "insufficient credit" in msg_l:
        return SEARCH_OUTCOME_BLOCKED_CREDITS, code or "INSUFFICIENT_CREDITS"

    if (
        code == "RATE_LIMITED"
        or status_code == 429
        or "rate limit" in msg_l
        or "rate_limit" in msg_l
    ):
        return SEARCH_OUTCOME_BLOCKED_RATE_LIMIT, code or "RATE_LIMITED"

    if code == "PLAN_REQUIRED" or "plan_required" in msg_l:
        return SEARCH_OUTCOME_BLOCKED_PLAN, code or "PLAN_REQUIRED"

    if code in ("INVALID_API_KEY", "UNAUTHORIZED") or status_code == 401:
        return SEARCH_OUTCOME_BLOCKED_AUTH, code or "INVALID_API_KEY"

    if code in ("NO_RESULTS", "NO_MATCH", "INVALID_DATAPOINTS"):
        return SEARCH_OUTCOME_NO_RESULTS, code

    # 2xx sin error_code de API = respuesta HTTP válida (vacía o con datos)
    if status_code is not None and 200 <= status_code < 300:
        if not code or str(code).upper().startswith("HTTP_"):
            return SEARCH_OUTCOME_OK, None
        return SEARCH_OUTCOME_NO_RESULTS, code

    if code:
        return SEARCH_OUTCOME_INTEGRATION_ERROR, code

    if status_code is not None and status_code >= 400:
        return SEARCH_OUTCOME_INTEGRATION_ERROR, f"HTTP_{status_code}"

    return SEARCH_OUTCOME_OK, None


def outcome_status_message(outcome: str, *, error_code: str | None = None) -> str:
    if outcome in BLOCKED_SEARCH_OUTCOMES:
        suffix = f" ({error_code})" if error_code else ""
        return f"{STATUS_SEARCH_BLOCKED}{suffix}"
    if outcome == SEARCH_OUTCOME_NO_RESULTS:
        return "0 resultados reales"
    return ""


def outcome_discard_reason(outcome: str, *, error_code: str | None = None, detail: str | None = None) -> str:
    if outcome == SEARCH_OUTCOME_BLOCKED_CREDITS:
        return f"Sin créditos Prospeo ({error_code or 'INSUFFICIENT_CREDITS'})"
    if outcome == SEARCH_OUTCOME_BLOCKED_RATE_LIMIT:
        return f"Rate limit Prospeo ({error_code or 'RATE_LIMITED'})"
    if outcome == SEARCH_OUTCOME_BLOCKED_PLAN:
        return f"Plan insuficiente ({error_code or 'PLAN_REQUIRED'})"
    if outcome == SEARCH_OUTCOME_BLOCKED_AUTH:
        return f"API key inválida ({error_code or 'INVALID_API_KEY'})"
    if outcome == SEARCH_OUTCOME_INTEGRATION_ERROR:
        return detail or f"Error integración Prospeo ({error_code or '?'})"
    if outcome == SEARCH_OUTCOME_NO_RESULTS:
        return "0 resultados reales (búsqueda vacía en Prospeo)"
    return "—"


def is_search_blocked_outcome(outcome: str | None) -> bool:
    return (outcome or "") in BLOCKED_SEARCH_OUTCOMES


def fetch_prospeo_account_health() -> ProspeoHealth:
    key = getenv("PROSPEO_API_KEY")
    if not key:
        return ProspeoHealth(configured=False, detail="PROSPEO_API_KEY no configurada")

    try:
        with httpx.Client(timeout=PROSPEO_HTTP_TIMEOUT) as client:
            resp = client.get(_ACCOUNT_INFO, headers={"X-KEY": key})
    except httpx.RequestError as e:
        return ProspeoHealth(
            configured=True,
            search_blocked=True,
            error_code="INTEGRATION_ERROR",
            banner_message=BANNER_BLOCKED,
            detail=str(e)[:200],
        )

    try:
        body = resp.json() if resp.text else {}
    except Exception:
        body = {}

    if resp.status_code == 401:
        return ProspeoHealth(
            configured=True,
            search_blocked=True,
            error_code="INVALID_API_KEY",
            banner_message=BANNER_BLOCKED,
            detail="API key inválida (401)",
        )

    if resp.status_code == 429:
        return ProspeoHealth(
            configured=True,
            rate_limited=True,
            search_blocked=True,
            error_code="RATE_LIMITED",
            banner_message=BANNER_BLOCKED,
            detail="Rate limit al consultar cuenta",
        )

    info = body.get("response") if isinstance(body, dict) else {}
    if not isinstance(info, dict):
        info = {}

    err_code = body.get("error_code") if isinstance(body, dict) else None
    remaining = info.get("remaining_credits")
    if isinstance(remaining, str) and remaining.isdigit():
        remaining = int(remaining)
    elif not isinstance(remaining, int):
        remaining = None

    plan = info.get("current_plan")
    plan_str = str(plan).strip() if plan else None

    outcome, norm_code = classify_prospeo_error(
        error_code=str(err_code) if err_code else None,
        status_code=resp.status_code if resp.status_code >= 400 else None,
        message=resp.text[:300] if resp.status_code >= 400 else None,
        remaining_credits=remaining,
    )

    insufficient = outcome == SEARCH_OUTCOME_BLOCKED_CREDITS
    blocked = effective_prospeo_search_blocked(
        error_code=norm_code,
        remaining_credits=remaining,
        insufficient_credits=insufficient,
        rate_limited=outcome == SEARCH_OUTCOME_BLOCKED_RATE_LIMIT,
        search_blocked=is_search_blocked_outcome(outcome),
    )
    if is_http_success_error_code(norm_code):
        norm_code = None

    health = ProspeoHealth(
        configured=True,
        remaining_credits=remaining,
        used_credits=info.get("used_credits") if isinstance(info.get("used_credits"), int) else None,
        current_plan=plan_str,
        rate_limited=outcome == SEARCH_OUTCOME_BLOCKED_RATE_LIMIT,
        insufficient_credits=insufficient,
        search_blocked=blocked,
        error_code=norm_code,
        detail=None if not blocked else outcome_discard_reason(outcome, error_code=norm_code),
    )
    if blocked:
        health.banner_message = BANNER_BLOCKED
    return health


def search_outcome_from_health(health: dict[str, Any] | ProspeoHealth) -> str:
    data = health.to_dict() if isinstance(health, ProspeoHealth) else health
    if not isinstance(data, dict):
        return SEARCH_OUTCOME_INTEGRATION_ERROR
    if is_http_success_error_code(data.get("error_code")):
        return SEARCH_OUTCOME_OK
    if data.get("insufficient_credits"):
        return SEARCH_OUTCOME_BLOCKED_CREDITS
    if data.get("rate_limited"):
        return SEARCH_OUTCOME_BLOCKED_RATE_LIMIT
    code = data.get("error_code")
    outcome, _ = classify_prospeo_error(error_code=str(code) if code else None)
    if is_search_blocked_outcome(outcome):
        return outcome
    return SEARCH_OUTCOME_OK


def sanitize_prospeo_health_dict(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Quita falsos bloqueos HTTP_2xx del meta guardado."""
    if not isinstance(raw, dict):
        return ProspeoHealth(configured=bool(getenv("PROSPEO_API_KEY"))).to_dict()
    out = dict(raw)
    if is_http_success_error_code(out.get("error_code")):
        out["error_code"] = None
        out["search_blocked"] = False
        out["banner_message"] = None
        out["detail"] = None
    remaining = out.get("remaining_credits")
    if isinstance(remaining, str) and remaining.isdigit():
        remaining = int(remaining)
    blocked = effective_prospeo_search_blocked(
        error_code=out.get("error_code"),
        remaining_credits=remaining if isinstance(remaining, int) else None,
        insufficient_credits=bool(out.get("insufficient_credits")),
        rate_limited=bool(out.get("rate_limited")),
        search_blocked=bool(out.get("search_blocked")),
    )
    out["search_blocked"] = blocked
    if not blocked:
        out["banner_message"] = None
        detail = str(out.get("detail") or "")
        if "HTTP_200" in detail or "integración Prospeo (HTTP_200)" in detail:
            out["detail"] = None
    return out


def _sanitize_search_debug_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return row
    out = dict(row)
    if is_http_success_error_code(out.get("error_code")):
        out["error_code"] = None
        out["search_blocked"] = False
        out["api_error"] = None
        if (out.get("valid_results") or 0) > 0:
            out["search_outcome"] = SEARCH_OUTCOME_OK
            out["status_message"] = (
                f"{out.get('valid_results')} válidos / {out.get('prospeo_results') or 0} Prospeo"
            )
        elif (out.get("prospeo_results") or 0) == 0:
            out["search_outcome"] = SEARCH_OUTCOME_NO_RESULTS
            out["status_message"] = "0 resultados reales"
            out["discard_reason"] = "0 resultados reales (búsqueda vacía en Prospeo)"
        else:
            out["search_outcome"] = SEARCH_OUTCOME_NO_RESULTS
    reqs = out.get("requests")
    if isinstance(reqs, list):
        clean_reqs = []
        for q in reqs:
            if not isinstance(q, dict):
                continue
            rq = dict(q)
            if is_http_success_error_code(rq.get("error_code")):
                rq["error_code"] = None
                rq["search_outcome"] = SEARCH_OUTCOME_NO_RESULTS if (rq.get("results_count") or 0) == 0 else SEARCH_OUTCOME_OK
            clean_reqs.append(rq)
        out["requests"] = clean_reqs
    return out


def cleanup_stale_prospeo_meta(meta: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Limpia last_error / prospeo_health / diagnóstico con HTTP_200 falso."""
    if not isinstance(meta, dict):
        return meta, False
    changed = False
    out = dict(meta)
    last_err = out.get("last_error")
    if last_err and ("HTTP_200" in str(last_err) or "integración Prospeo (HTTP_200)" in str(last_err)):
        out.pop("last_error", None)
        changed = True
    ph = out.get("prospeo_health")
    if isinstance(ph, dict):
        cleaned = sanitize_prospeo_health_dict(ph)
        if cleaned != ph:
            out["prospeo_health"] = cleaned
            changed = True
    psd = out.get("prospeo_search_debug")
    if isinstance(psd, list):
        cleaned_rows = [_sanitize_search_debug_row(r) for r in psd if isinstance(r, dict)]
        if cleaned_rows != psd:
            out["prospeo_search_debug"] = cleaned_rows
            changed = True
    return out, changed


def merge_health_from_api_error(health: ProspeoHealth, *, error_code: str | None, message: str) -> ProspeoHealth:
    if is_http_success_error_code(error_code):
        return health
    outcome, code = classify_prospeo_error(error_code=error_code, message=message)
    if not is_search_blocked_outcome(outcome):
        return health
    return ProspeoHealth(
        configured=health.configured,
        remaining_credits=health.remaining_credits,
        used_credits=health.used_credits,
        current_plan=health.current_plan,
        rate_limited=outcome == SEARCH_OUTCOME_BLOCKED_RATE_LIMIT,
        insufficient_credits=outcome == SEARCH_OUTCOME_BLOCKED_CREDITS,
        search_blocked=True,
        error_code=code,
        banner_message=BANNER_BLOCKED,
        detail=outcome_discard_reason(outcome, error_code=code, detail=message[:200]),
    )
