"""Construye argumentos PhantomBuster desde cola Nexus + env."""

from __future__ import annotations

from typing import Any

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead
from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.linkedin_phantom_query import build_phantom_search_bundle
from app.services.lead_sourcing.providers.phantombuster_client import (
    container_is_terminal,
    container_status_text,
)


def build_phantom_argument(
    campaign: Campaign,
    companies: list[CompanyCandidateRead],
    *,
    role_hint: str | None,
    limit: int,
    phantom_queue: dict | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Retorna (argument para launch, meta de input para debug UI).
    Query principal: boolean LinkedIn (títulos OR) AND industria — sin keywords sueltas.
    """
    bundle = build_phantom_search_bundle(campaign, companies, phantom_queue=phantom_queue)

    sales_nav_url = (getenv("PHANTOMBUSTER_SALES_NAVIGATOR_SEARCH_URL") or "").strip()
    env_linkedin_url = (getenv("PHANTOMBUSTER_LINKEDIN_SEARCH_URL") or "").strip()
    strategy = bundle.meta.get("search_strategy") or ""
    per_company = strategy.startswith("per_company")

    # Meta/debug Nexus — NO es el payload de launch (eso va por búsqueda en phantombuster_people).
    argument: dict[str, Any] = {
        "numberOfProfiles": min(limit, 100),
        "numberOfProfilesPerLaunch": min(limit, 100),
        "nexus_companySearchPlans": bundle.meta.get("company_searches") or [],
        "nexus_search_strategy": strategy,
    }

    linkedin_url_used = bundle.linkedin_search_url
    url_source = "built_per_search"

    if per_company:
        url_source = "per_company_dynamic_launch"
    elif sales_nav_url:
        argument["searchUrl"] = sales_nav_url
        argument["salesNavigatorSearchUrl"] = sales_nav_url
        url_source = "env_sales_navigator"

    prefer_built = (getenv("PHANTOMBUSTER_PREFER_BUILT_SEARCH") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if not per_company and env_linkedin_url and not prefer_built:
        argument["linkedInSearchUrl"] = env_linkedin_url
        argument["searchUrl"] = env_linkedin_url
        linkedin_url_used = env_linkedin_url
        url_source = "env_linkedin_search_url"
    elif env_linkedin_url and prefer_built and not per_company:
        url_source = "built_overrides_env_linkedin"

    input_meta = {
        **bundle.meta,
        "linkedin_query_exact": bundle.linkedin_query_exact,
        "linkedin_search_url_used": linkedin_url_used,
        "linkedin_search_url_source": url_source,
        "role_hint_campaign": (role_hint or campaign.target_role or "").strip() or None,
        "sales_navigator_search_url": sales_nav_url or None,
        "linkedin_search_url_env": env_linkedin_url or None,
        "has_sales_nav_url": bool(sales_nav_url),
        "has_linkedin_search_url": bool(linkedin_url_used),
        "has_company_names": bool(bundle.meta.get("target_companies")),
        "has_company_searches": bool(bundle.meta.get("company_searches")),
        "has_linkedin_company_urls": bool(bundle.meta.get("linkedin_company_urls")),
        "has_directory_seeds": bool(bundle.meta.get("directory_seed_urls")),
        "search_phrase": bundle.linkedin_query_exact,
        "search_strategy": bundle.meta.get("search_strategy"),
        "company_searches": bundle.meta.get("company_searches") or [],
        "target_companies": bundle.meta.get("target_companies") or [],
        "phantom_test_mode": bundle.meta.get("phantom_test_mode"),
        "skip_company_match_filter": bundle.meta.get("skip_company_match_filter"),
        "phantom_launch_note": (
            "Cada búsqueda envía linkedInSearchUrl al agente LinkedIn Search Export "
            "(ver company_search_runs[].launch_argument_sent)."
        ),
        "roles_fallback_order": bundle.meta.get("roles_fallback_order"),
        "phantom_companies_selected": bundle.meta.get("phantom_companies_selected"),
        "phantom_target_selection": bundle.meta.get("phantom_target_selection"),
    }
    return argument, input_meta


def diagnose_empty_run(
    *,
    agent: dict[str, Any],
    container: dict[str, Any],
    input_meta: dict[str, Any],
    parse_note: str,
    rows_count: int,
) -> tuple[str, str]:
    """
    Retorna (outcome_code, mensaje_usuario).
    outcome: missing_session | missing_search_input | phantom_error | no_results | output_empty
    """
    exit_msg = (
        (container.get("exitMessage") or container.get("message") or container.get("error") or "")
    ).strip()
    status = (container.get("status") or container.get("state") or "").lower()
    last_end = (agent.get("lastEndMessage") or agent.get("lastEndStatus") or "").strip()

    combined = f"{exit_msg} {last_end}".lower()
    if any(w in combined for w in ("cookie", "session", "login", "logged in", "connect")):
        return (
            "missing_session",
            "Falta conectar sesión LinkedIn en PhantomBuster (cookie/session). "
            "Abrí el agente en app.phantombuster.com y conectá tu cuenta LinkedIn o Sales Navigator.",
        )

    if status and any(tok in status for tok in ("error", "fail", "abort", "crash")):
        detail = exit_msg or last_end or status or "error desconocido"
        return ("phantom_error", f"PhantomBuster falló: {detail[:300]}")

    if container.get("_poll_timeout"):
        if container_is_terminal(container)[0]:
            pass
        else:
            return (
                "output_not_ready",
                "Nexus cortó el polling por timeout; PhantomBuster seguía en curso según la API. "
                f"Último status: {status or 'desconocido'}. "
                "Reintentá o aumentá PHANTOMBUSTER_POLL_MAX_SEC.",
            )

    needs_search = not input_meta.get("has_sales_nav_url") and not input_meta.get(
        "has_linkedin_search_url"
    )
    has_targets = (
        input_meta.get("has_company_searches")
        or input_meta.get("has_company_names")
        or input_meta.get("has_linkedin_company_urls")
        or input_meta.get("has_directory_seeds")
    )

    script = (agent.get("script") or agent.get("name") or "").lower()
    if "sales navigator" in script and not input_meta.get("has_sales_nav_url"):
        return (
            "missing_search_input",
            "Este Phantom requiere Sales Navigator Search URL. "
            "Definí PHANTOMBUSTER_SALES_NAVIGATOR_SEARCH_URL en backend/.env "
            "o configurá searchUrl en el agente.",
        )

    if needs_search and not has_targets:
        return (
            "missing_search_input",
            "No hay Search URL ni empresas/URLs LinkedIn suficientes para el Phantom. "
            "Agregá PHANTOMBUSTER_SALES_NAVIGATOR_SEARCH_URL o PHANTOMBUSTER_LINKEDIN_SEARCH_URL, "
            "o incluí empresas LinkedIn en la cola.",
        )

    if rows_count == 0:
        return (
            "no_results",
            f"PhantomBuster terminó OK pero no devolvió personas ({parse_note}). "
            "Puede ser búsqueda vacía o Phantom distinto al esperado.",
        )

    return ("ok", "OK")
