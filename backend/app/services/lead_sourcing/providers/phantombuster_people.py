"""PhantomBuster — extracción de personas con debug, poll y diagnóstico."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.campaign import Campaign
from app.schemas.lead_sourcing import CompanyCandidateRead, LeadCandidateRead
from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.providers.base import (
    PeopleExtractionProvider,
    ProviderAPIError,
    ProviderNotConfiguredError,
)
from app.services.lead_sourcing.lead_sourcing_company_targeting import (
    TargetCompany,
    build_linkedin_keywords_query,
    collect_target_companies,
    fuzzy_match_target_company,
    is_contaminated_person_name,
    phantom_role_fallback_order,
)
from app.services.lead_sourcing.providers.phantombuster_linkedin_export import (
    agent_argument_schema_debug,
    allow_stale_output_sources,
    build_linkedin_people_search_url,
    build_linkedin_search_export_launch_argument,
    launch_argument_diff_note,
    minimal_single_search_query,
    parse_agent_argument,
    use_saved_agent_config_only,
)
from app.services.lead_sourcing.providers.phantombuster_args import (
    build_phantom_argument,
    diagnose_empty_run,
)
from app.services.lead_sourcing.providers.phantombuster_client import (
    agent_has_session_cookie,
    agent_script_name,
    auth_diagnostics,
    container_is_terminal,
    container_status_text,
    download_remote_result,
    download_s3_results,
    extract_s3_folders,
    fetch_agent,
    fetch_agent_output,
    fetch_container,
    fetch_container_output,
    fetch_container_result_object,
    fetch_leads_by_list,
    fetch_result_from_output_urls,
    hydrate_output_payload,
    launch_agent,
    parse_org_storage_leads,
    parse_output_rows,
    poll_container,
    poll_max_sec,
    summarize_output_keys,
)
from app.services.lead_sourcing.phantom_runtime import (
    is_phantom_test_mode,
    phantom_output_fetch_max_sec,
    phantom_poll_timeout_sec,
    phantom_max_roles_per_company,
    phantom_skip_company_match_filter,
    phantom_test_max_results,
)
from app.services.lead_sourcing.timeouts_config import PHANTOMBUSTER_OUTPUT_FETCH_MAX_SEC

_logger = logging.getLogger(__name__)


_LOG_NOISE_FRAGMENTS = (
    "aws sdk",
    "maintenance mode",
    "migrate your code",
    "container",
    "node --trace-warnings",
    "started in",
    " gmt",
    "coordinated universal time",
    "for more information",
    "blog post",
    "a.co/",
    "sdk for javascript",
)
_DATE_NOISE_WORDS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


@dataclass
class PhantomExtractResult:
    leads: list[LeadCandidateRead] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


class PhantomBusterPeopleProvider(PeopleExtractionProvider):
    """
    Requiere PHANTOMBUSTER_API_KEY + PHANTOMBUSTER_LINKEDIN_AGENT_ID.
    Opcional: PHANTOMBUSTER_SALES_NAVIGATOR_SEARCH_URL, PHANTOMBUSTER_LINKEDIN_SEARCH_URL
    """

    def is_configured(self) -> bool:
        return bool(getenv("PHANTOMBUSTER_API_KEY"))

    def can_extract(self) -> bool:
        return bool(getenv("PHANTOMBUSTER_API_KEY")) and bool(
            getenv("PHANTOMBUSTER_LINKEDIN_AGENT_ID")
        )

    def _targets_from_input(
        self,
        companies: list[CompanyCandidateRead],
        phantom_queue: dict | None,
        input_meta: dict[str, Any],
    ) -> list[TargetCompany]:
        raw = input_meta.get("target_companies")
        if isinstance(raw, list) and raw:
            out: list[TargetCompany] = []
            for item in raw:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                out.append(
                    TargetCompany(
                        name=str(item["name"]),
                        url=item.get("url") if isinstance(item.get("url"), str) else None,
                        icp_relevance_score=int(item.get("icp_relevance_score") or 0),
                        canonical_key=str(item.get("canonical_key") or ""),
                        source_type=str(item.get("source_type") or ""),
                    )
                )
            if out:
                return out
        from app.services.lead_sourcing.phantom_runtime import is_phantom_test_mode

        return collect_target_companies(
            companies,
            phantom_queue,
            test_mode=is_phantom_test_mode(),
        )

    def _run_one_search(
        self,
        agent_id: str,
        *,
        saved_argument: dict[str, Any],
        plan: dict[str, Any],
        per_limit: int,
        poll_timeout: float | None = None,
        previous_launch_arg: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Un launch Phantom por empresa/query — solo linkedInSearchUrl + sesión."""
        company = str(plan.get("company") or "")
        keywords = str(
            plan.get("linkedin_keywords") or plan.get("site_query") or plan.get("query") or ""
        )
        li_url = str(plan.get("linkedin_url") or "").strip() or build_linkedin_people_search_url(
            keywords
        )
        if use_saved_agent_config_only():
            launch_payload: dict[str, Any] | None = None
        else:
            launch_payload = build_linkedin_search_export_launch_argument(
                keywords=keywords,
                linkedin_search_url=li_url,
                number_of_profiles=per_limit,
                saved_argument=saved_argument,
            )
        run_debug: dict[str, Any] = {
            "company": company,
            "linkedin_keywords": keywords,
            "linkedin_url": li_url,
            "linkedInSearchUrl_sent": li_url,
            "role_term": plan.get("role_term"),
            "poll_timeout_sec": poll_timeout or poll_max_sec(),
            "launch_uses_saved_agent_config": use_saved_agent_config_only(),
            "launch_argument_sent": launch_payload,
            "output_scope": "container_only"
            if not allow_stale_output_sources()
            else "container_then_stale_sources",
        }
        if previous_launch_arg and launch_payload:
            run_debug["launch_diff_vs_previous"] = launch_argument_diff_note(
                previous_launch_arg,
                launch_payload,
            )
        launch = launch_agent(agent_id, launch_payload)
        container_id = launch.get("containerId") or launch.get("container_id")
        run_debug["container_id"] = container_id
        if not container_id:
            run_debug["error"] = "no containerId"
            return [], run_debug

        wait = poll_timeout if poll_timeout is not None else poll_max_sec()
        container = poll_container(str(container_id), timeout_sec=wait)
        agent_after = fetch_agent(agent_id)
        run_debug["container_status"] = container_status_text(container)
        output_payload, output_source, parse_note, rows, output_meta = _read_best_output(
            agent_id,
            str(container_id),
            agent=agent_after,
            container=container,
            launch=launch,
            container_only=not allow_stale_output_sources(),
        )
        run_debug["output_source"] = output_source
        run_debug["output_rows_fingerprint"] = _rows_output_fingerprint(rows)
        run_debug["parse_note"] = parse_note
        run_debug["rows"] = len(rows)
        run_debug["output_endpoint"] = output_meta.get("output_endpoint")
        tagged: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                copy = dict(row)
                copy["_nexus_target_company"] = company
                tagged.append(copy)
            else:
                tagged.append(row)
        return tagged, run_debug

    def _analyze_role_batch(
        self,
        batch: list[dict[str, Any]],
        targets: list[TargetCompany],
        *,
        default_company: str,
        skip_company_filter: bool,
    ) -> tuple[int, int, int]:
        """(válidas, raw, descartadas) para debug y fallback por rol."""
        raw_n = sum(1 for row in batch if isinstance(row, dict))
        valid_n = 0
        discarded_n = 0
        for idx, row in enumerate(batch):
            if not isinstance(row, dict):
                continue
            lead = _row_to_lead(
                row,
                default_company=default_company or "—",
                row_index=idx,
            )
            ok, _reason = _validate_lead_row(row, lead)
            if not ok or not lead:
                discarded_n += 1
                continue
            matched, _match_name, _ratio, _note = fuzzy_match_target_company(
                lead.company_name,
                targets,
            )
            if not matched and not skip_company_filter:
                discarded_n += 1
                continue
            valid_n += 1
        return valid_n, raw_n, discarded_n

    def extract_people(
        self,
        campaign: Campaign,
        companies: list[CompanyCandidateRead],
        *,
        role_hint: str | None = None,
        limit: int = 50,
        phantom_queue: dict | None = None,
    ) -> PhantomExtractResult:
        if not self.can_extract():
            if not getenv("PHANTOMBUSTER_API_KEY"):
                raise ProviderNotConfiguredError(
                    "PhantomBuster no configurado. Definí PHANTOMBUSTER_API_KEY en backend/.env"
                )
            raise ProviderNotConfiguredError(
                "PhantomBuster: falta PHANTOMBUSTER_LINKEDIN_AGENT_ID "
                "(ID del agente de exportación LinkedIn)."
            )

        agent_id = getenv("PHANTOMBUSTER_LINKEDIN_AGENT_ID")
        debug: dict[str, Any] = {
            "agent_id": agent_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        debug["auth_debug"] = auth_diagnostics()

        argument, input_meta = build_phantom_argument(
            campaign,
            companies,
            role_hint=role_hint,
            limit=limit,
            phantom_queue=phantom_queue,
        )
        debug["argument_sent"] = argument
        debug["input_summary"] = input_meta
        debug["linkedin_query_exact"] = input_meta.get("linkedin_query_exact")
        debug["company_searches"] = input_meta.get("company_searches") or []
        debug["search_strategy"] = input_meta.get("search_strategy") or "per_company"
        test_mode = bool(input_meta.get("phantom_test_mode"))
        debug["phantom_test_mode"] = test_mode
        skip_company_filter = bool(input_meta.get("skip_company_match_filter")) or (
            phantom_skip_company_match_filter()
        )
        effective_limit = (
            min(phantom_test_max_results(), limit) if test_mode else limit
        )
        poll_timeout = phantom_poll_timeout_sec()

        try:
            agent = fetch_agent(agent_id)
            saved_argument = parse_agent_argument(agent)
            debug["agent_name"] = agent_script_name(agent)
            debug["agent_last_end_message"] = agent.get("lastEndMessage")
            debug["agent_last_end_status"] = agent.get("lastEndStatus")
            debug["session_cookie_in_agent"] = agent_has_session_cookie(agent)
            debug["agent_argument_schema"] = agent_argument_schema_debug(agent)
            debug["launch_uses_saved_agent_config"] = use_saved_agent_config_only()
            debug["allow_stale_output_sources"] = allow_stale_output_sources()

            company_searches = input_meta.get("company_searches") or []
            single_query = minimal_single_search_query()
            if single_query:
                company_searches = [
                    {
                        "company": "Saas Labs",
                        "linkedin_keywords": single_query,
                        "linkedin_url": build_linkedin_people_search_url(single_query),
                        "role_term": "minimal_test",
                        "role_try_order": ["Founder"],
                    }
                ]
                debug["minimal_single_search"] = True
                debug["minimal_single_search_query"] = single_query
            if not company_searches:
                debug["outcome"] = "missing_search_input"
                debug["outcome_message"] = (
                    "Modo test sin query."
                    if test_mode
                    else (
                        "No hay empresas ICP reales (LinkedIn/Crunchbase) para búsqueda. "
                        "Evitamos nombres genéricos tipo «SaaS Development». "
                        "Ejecutá Web Search y prepará PhantomBuster."
                    )
                )
                debug["finished_at"] = datetime.now(timezone.utc).isoformat()
                return PhantomExtractResult(leads=[], debug=debug)

            targets = self._targets_from_input(companies, phantom_queue, input_meta)
            debug["target_companies"] = [t.to_dict() for t in targets]
            if isinstance(input_meta, dict):
                if input_meta.get("phantom_companies_selected"):
                    debug["phantom_companies_selected"] = input_meta["phantom_companies_selected"]
                if input_meta.get("phantom_target_selection"):
                    debug["phantom_target_selection"] = input_meta["phantom_target_selection"]

            plans = company_searches if not test_mode else company_searches[:2]
            max_roles_per_company = phantom_max_roles_per_company()
            roles_fallback_order = phantom_role_fallback_order()
            per_limit = (
                effective_limit
                if test_mode
                else max(5, min(30, effective_limit // max(1, len(plans))))
            )
            extract_deadline = (
                time.monotonic() + poll_timeout + 8.0 if test_mode else None
            )
            rows: list[dict[str, Any]] = []
            seen_row_keys: set[str] = set()
            search_runs: list[dict[str, Any]] = []
            last_launch_arg: dict[str, Any] | None = None

            for plan in plans:
                if extract_deadline is not None and time.monotonic() > extract_deadline:
                    search_runs.append({"skipped": True, "reason": "test_mode_total_timeout"})
                    break
                if not isinstance(plan, dict):
                    continue
                company = str(plan.get("company") or "")
                roles_to_try = list(roles_fallback_order)

                company_valid = 0
                attempts = 0
                for role in roles_to_try:
                    if attempts >= max_roles_per_company:
                        break
                    if extract_deadline is not None and time.monotonic() > extract_deadline:
                        search_runs.append(
                            {
                                "company": company,
                                "skipped": True,
                                "reason": "test_mode_total_timeout",
                            }
                        )
                        break
                    keywords = build_linkedin_keywords_query(role, company_name=company or None)
                    role_plan = {
                        **plan,
                        "role_term": role,
                        "linkedin_keywords": keywords,
                        "linkedin_url": build_linkedin_people_search_url(keywords),
                        "role_try_order": roles_fallback_order,
                    }
                    try:
                        batch, run_dbg = self._run_one_search(
                            agent_id,
                            saved_argument=saved_argument,
                            plan=role_plan,
                            per_limit=per_limit,
                            poll_timeout=poll_timeout,
                            previous_launch_arg=last_launch_arg,
                        )
                        if isinstance(run_dbg.get("launch_argument_sent"), dict):
                            last_launch_arg = run_dbg["launch_argument_sent"]
                    except Exception as role_err:
                        _logger.warning(
                            "[phantombuster] role search failed company=%s role=%s: %s",
                            company,
                            role,
                            role_err,
                        )
                        search_runs.append(
                            {
                                "company": company,
                                "role_attempted": role,
                                "linkedin_keywords": keywords,
                                "raw_rows": 0,
                                "valid_matches": 0,
                                "discarded_rows": 0,
                                "role_error": str(role_err),
                                "summary_line": f"{role} → error: {role_err}",
                                "fallback_stopped": False,
                            }
                        )
                        attempts += 1
                        continue

                    valid_n, raw_n, discarded_n = self._analyze_role_batch(
                        batch,
                        targets,
                        default_company=company,
                        skip_company_filter=skip_company_filter,
                    )
                    summary_line = (
                        f"{role} → raw {raw_n} → válidas {valid_n}"
                        + (f" (descartadas {discarded_n})" if discarded_n else "")
                    )
                    run_dbg["role_attempted"] = role
                    run_dbg["raw_rows"] = raw_n
                    run_dbg["valid_matches"] = valid_n
                    run_dbg["discarded_rows"] = discarded_n
                    run_dbg["summary_line"] = summary_line
                    run_dbg["attempt_index"] = attempts + 1
                    run_dbg["max_attempts"] = max_roles_per_company
                    run_dbg["fallback_stopped"] = valid_n > 0
                    search_runs.append(run_dbg)
                    attempts += 1
                    company_valid += valid_n

                    for row in batch:
                        if not isinstance(row, dict):
                            continue
                        key = (
                            str(row.get("linkedinUrl") or row.get("profileUrl") or "")
                            or str(row.get("fullName") or row.get("name") or "")
                            or str(row.get("id") or "")
                        ).lower()
                        if key and key in seen_row_keys:
                            continue
                        if key:
                            seen_row_keys.add(key)
                        rows.append(row)

                    if valid_n > 0:
                        break

            debug["company_search_runs"] = search_runs
            debug["role_search_summary"] = [
                {
                    "company": r.get("company"),
                    "role_attempted": r.get("role_attempted"),
                    "raw_rows": r.get("raw_rows"),
                    "valid_matches": r.get("valid_matches"),
                    "discarded_rows": r.get("discarded_rows"),
                    "summary_line": r.get("summary_line"),
                    "fallback_stopped": r.get("fallback_stopped"),
                    "linkedin_keywords": r.get("linkedin_keywords"),
                }
                for r in search_runs
                if isinstance(r, dict) and r.get("role_attempted")
            ]
            debug["roles_fallback_order"] = roles_fallback_order
            debug["max_roles_per_company"] = max_roles_per_company
            debug["launch_payload_sent"] = {
                "id": agent_id,
                "strategy": "test_mode" if test_mode else "per_company",
                "runs": len(search_runs),
                "poll_timeout_sec": poll_timeout,
                "max_results": effective_limit,
            }
            debug["rows_parsed"] = len(rows)
            if rows:
                debug["first_row_keys"] = [
                    str(k) for k in list(rows[0].keys())[:40] if k is not None
                ]
                debug["first_row_sample"] = _safe_row_sample(rows[0])
            parse_note = f"merged {len(rows)} rows from {len(search_runs)} company searches"
            debug["parse_note"] = parse_note

            leads: list[LeadCandidateRead] = []
            discarded_rows: list[dict[str, Any]] = []
            company_match_debug: list[dict[str, Any]] = []

            for idx, row in enumerate(rows[: effective_limit * 3]):
                plan_company = str(row.get("_nexus_target_company") or "")
                default_co = plan_company or (targets[0].name if targets else "—")
                lead = _row_to_lead(row, default_company=default_co, row_index=idx)
                valid, reason = _validate_lead_row(row, lead)
                if not valid or not lead:
                    row_name = lead.name if lead else _guess_name_from_row(row)
                    discarded_rows.append(
                        {
                            "index": idx,
                            "reason": reason,
                            "name": row_name,
                            "company_name": (
                                lead.company_name
                                if lead
                                else (_pick(row, "company", "companyName") or None)
                            ),
                            "sample": _safe_row_sample(row, max_value_len=100),
                        }
                    )
                    continue

                matched, match_name, ratio, match_note = fuzzy_match_target_company(
                    lead.company_name,
                    targets,
                )
                if skip_company_filter and not matched and targets:
                    matched = True
                    match_note = "test_mode: company filter relaxed"
                company_match_debug.append(
                    {
                        "name": lead.name,
                        "lead_company": lead.company_name,
                        "matched_icp_company": match_name,
                        "company_match_ratio": round(ratio, 3),
                        "match_note": match_note,
                        "passed": matched,
                    }
                )
                if not matched:
                    discarded_rows.append(
                        {
                            "index": idx,
                            "reason": "company_not_in_pipeline",
                            "name": lead.name,
                            "company_name": lead.company_name,
                            "matched_icp_company": match_name,
                            "company_match_ratio": round(ratio, 3),
                            "match_note": match_note,
                            "sample": _safe_row_sample(row, max_value_len=80),
                        }
                    )
                    continue

                leads.append(
                    lead.model_copy(
                        update={
                            "company_name": match_name or lead.company_name,
                            "matched_icp_company": match_name,
                            "company_match_ratio": ratio,
                        }
                    )
                )
                if len(leads) >= effective_limit:
                    break

            debug["company_match_audit"] = company_match_debug[:80]

            debug["raw_rows_count"] = len(rows)
            debug["valid_rows_count"] = len(leads)
            debug["discarded_rows_count"] = len(discarded_rows)
            debug["discarded_rows_sample"] = discarded_rows[:24]
            debug["phantom_parse_discards"] = discarded_rows[:120]

            outcome, message = diagnose_empty_run(
                agent=agent,
                container={},
                input_meta=input_meta,
                parse_note=parse_note,
                rows_count=len(leads),
            )
            debug["outcome"] = outcome
            debug["outcome_message"] = message
            if len(leads) == 0 and len(rows) > 0 and discarded_rows:
                debug["outcome"] = "no_valid_leads"
                debug["outcome_message"] = (
                    "Phantom ejecutó pero no devolvió leads válidos. "
                    f"Filas raw: {len(rows)}; descartadas: {len(discarded_rows)}."
                )
            debug["leads_count"] = len(leads)
            debug["step_completion"] = "completed" if leads else "skipped"
            debug["finished_at"] = datetime.now(timezone.utc).isoformat()

            _logger.info(
                "[phantombuster] agent=%s company_runs=%s rows=%s leads=%s outcome=%s",
                agent_id,
                len(search_runs),
                len(rows),
                len(leads),
                outcome,
            )

            if len(leads) == 0 and outcome != "ok":
                debug["user_action"] = _action_hint(outcome)

            return PhantomExtractResult(leads=leads, debug=debug)

        except ProviderAPIError as e:
            debug["auth_debug"] = auth_diagnostics()
            debug["outcome"] = "auth_error" if e.status_code == 401 else "phantom_error"
            debug["outcome_message"] = (
                "PhantomBuster rechazó la autenticación. Revisá PHANTOMBUSTER_API_KEY "
                "en backend/.env y reiniciá el backend."
                if e.status_code == 401
                else str(e)
            )
            debug["finished_at"] = datetime.now(timezone.utc).isoformat()
            setattr(e, "debug", debug)
            raise
        except Exception as e:
            debug["outcome"] = "phantom_error"
            debug["outcome_message"] = str(e)
            debug["finished_at"] = datetime.now(timezone.utc).isoformat()
            _logger.exception("[phantombuster] unexpected error")
            raise ProviderAPIError(f"PhantomBuster: {e}", provider=self.name) from e


def _action_hint(outcome: str) -> str:
    hints = {
        "missing_session": (
            "Conectá LinkedIn/Sales Navigator en app.phantombuster.com → tu agente → Session / Cookie."
        ),
        "missing_search_input": (
            "Agregá PHANTOMBUSTER_SALES_NAVIGATOR_SEARCH_URL o PHANTOMBUSTER_LINKEDIN_SEARCH_URL en .env."
        ),
        "output_not_ready": "Esperá a que termine el agente en PhantomBuster y reintentá.",
        "phantom_error": "Revisá el log del agente en PhantomBuster dashboard.",
        "auth_error": "Pegá una API key válida de Workspace Settings → Technical → API keys y reiniciá el backend.",
        "no_valid_leads": (
            "Nexus no encontró leads en S3/result-object/lista. "
            "Definí PHANTOMBUSTER_LEADS_LIST_ID o PHANTOMBUSTER_RESULT_FILE_URL en .env, "
            "o verificá que el Phantom exporte result.csv."
        ),
        "no_results": "Ajustá búsqueda/ICP o verificá que el Phantom sea el correcto para tu flujo.",
    }
    return hints.get(outcome, "")


def _rows_output_fingerprint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sample_names: list[str] = []
    sample_urls: list[str] = []
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("fullName") or row.get("name") or "")[:60]
        url = str(row.get("profileUrl") or row.get("linkedinUrl") or "")[:80]
        if name:
            sample_names.append(name)
        if url:
            sample_urls.append(url)
    return {
        "row_count": len(rows),
        "sample_names": sample_names,
        "sample_profile_urls": sample_urls,
    }


def _read_best_output(
    agent_id: str,
    container_id: str,
    *,
    agent: dict[str, Any],
    container: dict[str, Any],
    launch: dict[str, Any],
    container_only: bool = False,
) -> tuple[Any, str, str, list[dict], dict[str, Any]]:
    """
    Prioriza output del container del launch actual.
    Si container_only=True (default), NO usa S3 del agente ni Leads List (evita ~973 filas cacheadas).
    """
    meta: dict[str, Any] = {
        "launch_id": launch.get("containerId") or container_id,
        "output_attempts": [],
    }
    fetch_budget = phantom_output_fetch_max_sec()
    output_deadline = time.monotonic() + fetch_budget
    org, s3 = extract_s3_folders(agent)
    if not org or not s3:
        c_org, c_s3 = extract_s3_folders(container)
        org, s3 = c_org or org, c_s3 or s3
    if org and s3:
        meta["s3_folders"] = {"orgS3Folder": org, "s3Folder": s3}

    leads_list_id = (getenv("PHANTOMBUSTER_LEADS_LIST_ID") or "").strip()
    if leads_list_id:
        meta["leads_list_id"] = leads_list_id
    manual_url = (getenv("PHANTOMBUSTER_RESULT_FILE_URL") or "").strip()
    if manual_url:
        meta["manual_result_url"] = manual_url[:200]

    def _record(
        source: str,
        endpoint: str,
        *,
        rows: list[dict],
        parse_note: str,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "source": source,
            "endpoint": endpoint,
            "rows": len(rows),
            "lead_like_rows": _count_lead_like_rows(rows),
            "parse_note": parse_note[:240],
        }
        if error:
            entry["error"] = error[:240]
        if extra:
            entry.update(extra)
        meta["output_attempts"].append(entry)

    def _try(
        source: str,
        endpoint: str,
        loader,
    ) -> tuple[Any, str, list[dict]] | None:
        try:
            payload = loader()
            if source == "org_storage_leads_list":
                rows, parse_note = parse_org_storage_leads(payload)
                hydrated: Any = payload
                hydrate_note = "org-storage"
            elif source in ("s3_agent", "s3_container", "s3_manual"):
                rows, parse_note, urls = payload  # type: ignore[misc]
                hydrated = rows
                hydrate_note = parse_note
                if urls:
                    meta.setdefault("s3_urls_tried", [])
                    meta["s3_urls_tried"].extend(urls)
            elif source == "fetch_output_urls":
                rows, parse_note, urls = payload  # type: ignore[misc]
                hydrated = rows
                hydrate_note = parse_note
                if urls:
                    meta.setdefault("result_urls_tried", [])
                    meta["result_urls_tried"].extend(urls)
            elif source == "manual_result_url":
                data, note = payload  # type: ignore[misc]
                hydrated, hydrate_note = hydrate_output_payload(data)
                rows, parse_note = parse_output_rows(hydrated)
                if not rows and isinstance(data, list):
                    rows = [r for r in data if isinstance(r, dict)]
                    parse_note = note
            else:
                hydrated, hydrate_note = hydrate_output_payload(payload)
                rows, parse_note = parse_output_rows(hydrated)
                if source == "container_fetch_result_object" and isinstance(payload, dict):
                    meta["has_result_object"] = bool(payload)

            _record(source, endpoint, rows=rows, parse_note=parse_note)
            if rows and _rows_look_like_leads(rows):
                meta["output_endpoint"] = endpoint
                return hydrated, f"{hydrate_note}; {parse_note}", rows
            if rows:
                return ("__fallback__", source, endpoint, hydrated, f"{hydrate_note}; {parse_note}", rows)
            return None
        except Exception as e:
            _record(source, endpoint, rows=[], parse_note="", error=str(e))
            return None

    fallback: tuple[str, str, Any, str, list[dict]] | None = None

    strategies: list[tuple[str, str, Any]] = []
    if not container_only:
        if leads_list_id:
            strategies.append(
                (
                    "org_storage_leads_list",
                    f"POST /org-storage/leads/by-list/{leads_list_id}",
                    lambda: fetch_leads_by_list(leads_list_id),
                )
            )
        if manual_url:
            strategies.append(
                (
                    "manual_result_url",
                    "GET PHANTOMBUSTER_RESULT_FILE_URL",
                    lambda: download_remote_result(manual_url),
                )
            )
    strategies.extend(
        [
            (
                "container_fetch_result_object",
                "GET /containers/fetch-result-object",
                lambda: fetch_container_result_object(container_id),
            ),
            (
                "container_with_result_object",
                "GET /containers/fetch?withResultObject=true",
                lambda: fetch_container(container_id, with_result_object=True),
            ),
            (
                "s3_container",
                "S3 result.csv/json (container metadata)",
                lambda: download_s3_results(container),
            ),
            (
                "fetch_output_urls",
                "GET csv/json URLs from fetch-output (container)",
                lambda: fetch_result_from_output_urls(
                    fetch_container_output(container_id),
                    {},
                ),
            ),
            (
                "container_fetch_output",
                "GET /containers/fetch-output (logs)",
                lambda: fetch_container_output(container_id),
            ),
        ]
    )
    if not container_only:
        strategies.extend(
            [
                (
                    "s3_agent",
                    "S3 result.csv/json (agent metadata — puede ser cache viejo)",
                    lambda: download_s3_results(agent),
                ),
                (
                    "fetch_output_urls_agent",
                    "GET csv/json URLs from fetch-output (agent)",
                    lambda: fetch_result_from_output_urls(
                        {},
                        fetch_agent_output(agent_id),
                    ),
                ),
                (
                    "agent_fetch_output",
                    "GET /agents/fetch-output (logs/latest)",
                    lambda: fetch_agent_output(agent_id),
                ),
            ]
        )

    for source, endpoint, loader in strategies:
        if time.monotonic() > output_deadline:
            meta["output_attempts"].append(
                {
                    "source": "output_budget",
                    "endpoint": "timeout",
                    "rows": 0,
                    "lead_like_rows": 0,
                    "parse_note": f"output fetch budget {fetch_budget}s exceeded",
                    "break": "max_output_fetch_sec",
                }
            )
            break
        result = _try(source, endpoint, loader)
        if not result:
            continue
        if result[0] == "__fallback__":
            _, fb_source, fb_endpoint, payload, note, rows = result
            if fallback is None:
                fallback = (fb_source, fb_endpoint, payload, note, rows)
            continue
        hydrated, note, rows = result
        meta["output_endpoint"] = endpoint
        return hydrated, source, note, rows, meta

    if fallback:
        fb_source, fb_endpoint, payload, note, rows = fallback
        meta["output_endpoint"] = fb_endpoint
        return payload, fb_source, note, rows, meta

    meta["output_endpoint"] = "none"
    return {}, "none", "sin datos en ninguna fuente", [], meta


def _count_lead_like_rows(rows: list[dict]) -> int:
    return sum(1 for row in rows[:30] if _row_looks_like_lead(row))


def _row_looks_like_lead(row: dict) -> bool:
    if _row_has_log_noise(row):
        return False
    name = _pick(row, "fullName", "full_name", "name", "profileName", "Names", "names")
    if not _is_reasonable_name(name):
        first = _pick(row, "firstName", "first_name", "First Name")
        last = _pick(row, "lastName", "last_name", "Last Name")
        name = f"{first} {last}".strip()
    if not _is_reasonable_name(name):
        return False
    li = _first_linkedin_url(row) or _pick(
        row,
        "linkedinProfileUrl",
        "linkedin_url",
        "linkedinUrl",
        "profileUrl",
    )
    role = _pick(row, "jobTitle", "headline", "title", "occupation", "Title")
    return bool(li) or _is_reasonable_role(role)


def _rows_look_like_leads(rows: list[dict]) -> bool:
    return _count_lead_like_rows(rows) > 0


def _safe_preview(payload: Any, max_len: int = 1200) -> str:
    try:
        import json

        text = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        text = str(payload)
    return text[:max_len]


def _safe_row_sample(row: dict, max_value_len: int = 140) -> dict[str, str]:
    sample: dict[str, str] = {}
    sensitive = ("email", "phone", "mobile", "cookie", "token")
    for key, val in list(row.items())[:40]:
        k = str(key)
        if any(s in k.lower() for s in sensitive):
            sample[k] = "[redacted]"
            continue
        text = str(val).strip()
        sample[k] = text[:max_value_len]
    return sample


def _contains_log_noise(text: str) -> bool:
    low = f" {text.lower()} "
    return any(fragment in low for fragment in _LOG_NOISE_FRAGMENTS)


def _looks_like_date_or_runtime(text: str) -> bool:
    low = text.lower().strip()
    if low.startswith("on ") and any(month in low for month in _DATE_NOISE_WORDS):
        return True
    if any(month in low for month in _DATE_NOISE_WORDS) and any(ch.isdigit() for ch in low):
        return True
    return False


def _is_reasonable_name(text: str) -> bool:
    name = (text or "").strip()
    if not name:
        return False
    if len(name) > 90:
        return False
    if _contains_log_noise(name) or _looks_like_date_or_runtime(name):
        return False
    low = name.lower()
    if low.startswith(("please ", "use ", "for ", "container ", "warning", "error")):
        return False
    if name.startswith("http") or "://" in name or "@" in name:
        return False
    letters = sum(1 for ch in name if ch.isalpha())
    if letters < 3:
        return False
    words = [w for w in name.replace("-", " ").split() if w]
    if len(words) > 6:
        return False
    return True


def _is_reasonable_role(text: str | None) -> bool:
    role = (text or "").strip()
    if not role:
        return False
    if len(role) > 180:
        return False
    if _contains_log_noise(role) or _looks_like_date_or_runtime(role):
        return False
    low = role.lower()
    if low.startswith(("please ", "use ", "for more", "container ", "warning", "error")):
        return False
    if role.startswith("http") or "a.co/" in low:
        return False
    letters = sum(1 for ch in role if ch.isalpha())
    return letters >= 3


def _row_has_log_noise(row: dict) -> bool:
    return any(_contains_log_noise(text) or _looks_like_date_or_runtime(text) for text in _all_text_values(row))


def _guess_name_from_row(row: dict) -> str | None:
    name = _pick(row, "fullName", "name", "firstName", "lastName", "profileName")
    if name and _is_reasonable_name(name):
        return name.strip()
    first = _pick(row, "firstName")
    last = _pick(row, "lastName")
    if first and last:
        combined = f"{first} {last}".strip()
        if _is_reasonable_name(combined):
            return combined
    return None


def _validate_lead_row(
    row: dict,
    lead: LeadCandidateRead | None,
) -> tuple[bool, str]:
    if _row_has_log_noise(row):
        return False, "runtime_log_or_warning"
    if lead is None:
        return False, "missing_name"
    if not _is_reasonable_name(lead.name):
        return False, "invalid_name"
    if is_contaminated_person_name(lead.name):
        return False, "contaminated_surname_match"
    if not (lead.linkedin_url or _is_reasonable_role(lead.role)):
        return False, "missing_linkedin_or_role"
    if lead.role and not _is_reasonable_role(lead.role):
        return False, "invalid_role"
    return True, "ok"


def _value_to_texts(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        out: list[str] = []
        for item in val:
            out.extend(_value_to_texts(item))
        return out
    if isinstance(val, dict):
        out: list[str] = []
        for item in val.values():
            out.extend(_value_to_texts(item))
        return out
    text = str(val).strip()
    if not text or text.lower() in ("none", "null", "nan", "n/a", "-"):
        return []
    return [text]


def _norm_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _pick(row: dict, *keys: str) -> str:
    normalized = {_norm_key(k): v for k, v in row.items()}
    for key in keys:
        val = normalized.get(_norm_key(key))
        if val is not None:
            texts = _value_to_texts(val)
            if texts:
                text = texts[0]
                return text
    return ""


def _first_text_value(row: dict, *, exclude_keys: tuple[str, ...] = ()) -> str:
    exclude_norm = {_norm_key(k) for k in exclude_keys}
    for key, val in row.items():
        nk = _norm_key(str(key))
        if nk in exclude_norm:
            continue
        for text in _value_to_texts(val):
            if text.startswith("http") or "linkedin.com/" in text.lower() or "@" in text:
                continue
            if len(text) > 180:
                continue
            return text
    return ""


def _all_text_values(row: dict) -> list[str]:
    out: list[str] = []
    for val in row.values():
        out.extend(_value_to_texts(val))
    return out


def _first_linkedin_url(row: dict) -> str:
    for text in _all_text_values(row):
        if "linkedin.com/" in text.lower():
            return text
    return ""


def _fallback_role(row: dict, *, name: str) -> str | None:
    name_norm = name.strip().lower()
    for text in _all_text_values(row):
        low = text.lower()
        if low == name_norm or "linkedin.com/" in low or text.startswith("http") or "@" in text:
            continue
        if len(text) > 180:
            continue
        return text
    return None


def _split_name(name: str) -> tuple[str | None, str | None]:
    parts = [p for p in (name or "").split() if p]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return None, None


def _row_to_lead(
    row: dict,
    *,
    default_company: str,
    row_index: int,
) -> LeadCandidateRead | None:
    name = (
        _pick(
            row,
            "fullName",
            "full_name",
            "name",
            "names",
            "Names",
            "profileName",
            "profile_name",
            "leadName",
            "personName",
            "memberName",
            "output",
            "result",
            "lead",
            "title",
            "Title",
        )
        or f"{_pick(row, 'firstName', 'first_name', 'First Name')} {_pick(row, 'lastName', 'last_name', 'Last Name')}".strip()
        or _first_text_value(
            row,
            exclude_keys=(
                "jobTitle",
                "headline",
                "currentJob",
                "company",
                "companyName",
                "location",
            ),
        )
    )
    if not name:
        return None
    first_name = _pick(row, "firstName", "first_name", "First Name") or None
    last_name = _pick(row, "lastName", "last_name", "Last Name") or None
    if not first_name and not last_name:
        first_name, last_name = _split_name(name)
    li = (
        _pick(
            row,
            "linkedinProfileUrl",
            "linkedin_url",
            "linkedinUrl",
            "linkedinProfile",
            "linkedInProfile",
            "profileUrl",
            "profileLink",
            "url",
            "LinkedIn URL",
        )
        or _first_linkedin_url(row)
    )
    email = _pick(row, "email", "professionalEmail", "Email") or None
    company = (
        _pick(
            row,
            "companyName",
            "company",
            "currentCompany",
            "currentCompanyName",
            "company_name",
            "organization",
            "Company",
        )
        or default_company
    ).strip()
    role = _pick(
        row,
        "jobTitle",
        "headline",
        "currentJob",
        "currentJobTitle",
        "position",
        "occupation",
        "description",
        "Title",
        "Job Title",
    ) or _fallback_role(row, name=name)
    location = _pick(row, "location", "Location", "geo", "address") or None
    image_url = _pick(row, "imageUrl", "imgUrl", "avatar", "profileImageUrl", "pictureUrl") or None
    ext = (_pick(row, "id", "profileId", "vmid", "memberId", "Profile ID") or li or f"{name}-{row_index}").strip()
    return LeadCandidateRead(
        external_id=f"pb-{ext}"[:64],
        provider="phantombuster",
        first_name=first_name,
        last_name=last_name,
        name=name,
        company_name=company or "—",
        role=role,
        country=location,
        email=email,
        linkedin_url=li or None,
        image_url=image_url,
        has_email=bool(email),
        has_linkedin=bool(li),
    )
