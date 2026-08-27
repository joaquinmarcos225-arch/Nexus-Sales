"""Contadores en proceso por módulo (Prospeo / Brave / OpenAI / imports / WA).

Sin DB: se resetean al reiniciar el proceso. Ver GET /health/cogs-metrics.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

_logger = logging.getLogger(__name__)
_lock = threading.Lock()

# Precios de lista aproximados (CostGuard / Prospeo Starter / Brave / gpt-4.1-mini).
_PROSPEO_CREDIT_USD = 0.0245
_BRAVE_QUERY_USD = 0.005
_OPENAI_INPUT_PER_MTOK_USD = 0.40
_OPENAI_OUTPUT_PER_MTOK_USD = 1.60

_prospeo_search = 0
_enrich_mobile_calls = 0
_enrich_email_only_calls = 0
_prospeo_enrich_company = 0
_brave_queries = 0
_web_search_other = 0  # serpapi / ddg
_nexus_fetch_calls = 0
_research_skipped = 0
_openai_calls = 0
_openai_input_tokens = 0
_openai_output_tokens = 0
_openai_skipped_trivial = 0
_imports = 0
_wa_sent = 0
_log_every = 25


def _event_total() -> int:
    return (
        _prospeo_search
        + _enrich_mobile_calls
        + _enrich_email_only_calls
        + _brave_queries
        + _openai_calls
        + _imports
        + _wa_sent
    )


def _maybe_log() -> None:
    total = _event_total()
    if total <= 0 or total % _log_every != 0:
        return
    snap = snapshot()
    _logger.info(
        "[cogs-metrics] prospeo_search=%s enrich_mobile=%s enrich_email=%s "
        "brave=%s openai_calls=%s openai_tok=%s imports=%s wa_sent=%s "
        "est_total_usd=%.3f est_per_import=%.3f",
        snap["prospeo_search_calls"],
        snap["enrich_mobile_calls"],
        snap["enrich_email_only_calls"],
        snap["brave_queries"],
        snap["openai_calls"],
        snap["openai_total_tokens"],
        snap["imports"],
        snap["wa_sent"],
        snap["est_total_usd"],
        snap["est_cogs_per_import_usd"],
    )


def record_prospeo_search(n: int = 1) -> None:
    global _prospeo_search
    if n <= 0:
        return
    with _lock:
        _prospeo_search += int(n)
    _maybe_log()


def record_prospeo_enrich_company(n: int = 1) -> None:
    global _prospeo_enrich_company
    if n <= 0:
        return
    with _lock:
        _prospeo_enrich_company += int(n)
    _maybe_log()


def record_enrich(*, enrich_mobile: bool) -> None:
    global _enrich_mobile_calls, _enrich_email_only_calls
    with _lock:
        if enrich_mobile:
            _enrich_mobile_calls += 1
        else:
            _enrich_email_only_calls += 1
    _maybe_log()


def record_nexus_fetch(n: int = 1) -> None:
    """Fetch HTTP directo a sitio corporativo (sin Brave)."""
    global _nexus_fetch_calls
    if n <= 0:
        return
    with _lock:
        _nexus_fetch_calls += int(n)
    _maybe_log()


def record_web_search(*, backend: str = "brave", n: int = 1) -> None:
    global _brave_queries, _web_search_other
    if n <= 0:
        return
    name = (backend or "").strip().lower()
    with _lock:
        if name == "brave":
            _brave_queries += int(n)
        else:
            _web_search_other += int(n)
    _maybe_log()


def record_research_skipped(n: int = 1) -> None:
    """Investigación omitida (follow-up / brief ya guardado)."""
    global _research_skipped
    if n <= 0:
        return
    with _lock:
        _research_skipped += int(n)
    _maybe_log()


def record_openai_skipped_trivial(n: int = 1) -> None:
    """OpenAI evitado: tarea resuelta con heurísticas / plantillas."""
    global _openai_skipped_trivial
    if n <= 0:
        return
    with _lock:
        _openai_skipped_trivial += int(n)
    _maybe_log()


def record_openai(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    global _openai_calls, _openai_input_tokens, _openai_output_tokens
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    if inp <= 0 and out <= 0 and total_tokens:
        # Split desconocido: atribuir todo a input para no inflar output.
        inp = int(total_tokens)
    with _lock:
        _openai_calls += 1
        _openai_input_tokens += max(0, inp)
        _openai_output_tokens += max(0, out)
    _maybe_log()


def record_import(n: int = 1) -> None:
    global _imports
    if n <= 0:
        return
    with _lock:
        _imports += int(n)
    _maybe_log()


def record_wa_sent(n: int = 1) -> None:
    global _wa_sent
    if n <= 0:
        return
    with _lock:
        _wa_sent += int(n)
    _maybe_log()


def snapshot() -> dict[str, Any]:
    with _lock:
        search = _prospeo_search
        mobile = _enrich_mobile_calls
        email_only = _enrich_email_only_calls
        enrich_co = _prospeo_enrich_company
        brave = _brave_queries
        web_other = _web_search_other
        nexus_fetch = _nexus_fetch_calls
        research_skipped = _research_skipped
        oai_calls = _openai_calls
        oai_in = _openai_input_tokens
        oai_out = _openai_output_tokens
        oai_skipped = _openai_skipped_trivial
        imports = _imports
        wa = _wa_sent

    # Créditos Prospeo estimados: search 1 + email enrich 1 + mobile 10 + company ~1.
    prospeo_credits = search + email_only + (mobile * 10) + enrich_co
    prospeo_usd = round(prospeo_credits * _PROSPEO_CREDIT_USD, 4)
    brave_usd = round(brave * _BRAVE_QUERY_USD, 4)
    openai_usd = round(
        (oai_in / 1_000_000) * _OPENAI_INPUT_PER_MTOK_USD
        + (oai_out / 1_000_000) * _OPENAI_OUTPUT_PER_MTOK_USD,
        4,
    )
    total_usd = round(prospeo_usd + brave_usd + openai_usd, 4)

    return {
        "prospeo_search_calls": search,
        "enrich_mobile_calls": mobile,
        "enrich_email_only_calls": email_only,
        "prospeo_enrich_company_calls": enrich_co,
        "prospeo_credits_est": prospeo_credits,
        "brave_queries": brave,
        "nexus_fetch_calls": nexus_fetch,
        "research_skipped": research_skipped,
        "web_search_other_queries": web_other,
        "openai_calls": oai_calls,
        "openai_skipped_trivial": oai_skipped,
        "openai_input_tokens": oai_in,
        "openai_output_tokens": oai_out,
        "openai_total_tokens": oai_in + oai_out,
        "imports": imports,
        "wa_sent": wa,
        "mobile_per_import": round(mobile / imports, 3) if imports else 0.0,
        "mobile_per_wa_sent": round(mobile / wa, 3) if wa else 0.0,
        "est_prospeo_usd": prospeo_usd,
        "est_brave_usd": brave_usd,
        "est_openai_usd": openai_usd,
        "est_total_usd": total_usd,
        "est_cogs_per_import_usd": round(total_usd / imports, 3) if imports else 0.0,
        "unit_costs_usd": {
            "prospeo_credit": _PROSPEO_CREDIT_USD,
            "brave_query": _BRAVE_QUERY_USD,
            "openai_input_per_mtok": _OPENAI_INPUT_PER_MTOK_USD,
            "openai_output_per_mtok": _OPENAI_OUTPUT_PER_MTOK_USD,
        },
    }


def reset_for_tests() -> None:
    global _prospeo_search, _enrich_mobile_calls, _enrich_email_only_calls
    global _prospeo_enrich_company, _brave_queries, _web_search_other, _nexus_fetch_calls
    global _research_skipped
    global _openai_calls, _openai_input_tokens, _openai_output_tokens, _openai_skipped_trivial
    global _imports, _wa_sent
    with _lock:
        _prospeo_search = 0
        _enrich_mobile_calls = 0
        _enrich_email_only_calls = 0
        _prospeo_enrich_company = 0
        _brave_queries = 0
        _web_search_other = 0
        _nexus_fetch_calls = 0
        _research_skipped = 0
        _openai_calls = 0
        _openai_input_tokens = 0
        _openai_output_tokens = 0
        _openai_skipped_trivial = 0
        _imports = 0
        _wa_sent = 0
