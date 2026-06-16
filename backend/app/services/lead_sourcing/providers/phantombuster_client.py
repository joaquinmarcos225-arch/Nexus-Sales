"""Cliente PhantomBuster API v2 — launch, poll, output, debug."""

from __future__ import annotations

import json
import logging
import csv
import io
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.lead_sourcing.env_config import getenv, refresh_lead_sourcing_env
from app.services.lead_sourcing.providers.base import ProviderAPIError
from app.services.lead_sourcing.timeouts_config import (
    PHANTOMBUSTER_OUTPUT_FETCH_MAX_SEC,
    PHANTOMBUSTER_POLL_MAX_SEC,
)

_logger = logging.getLogger(__name__)

_PB_BASE = "https://api.phantombuster.com/api/v2"
_S3_BASE = "https://phantombuster.s3.amazonaws.com"
_DEFAULT_S3_RESULT_NAMES = (
    "result.csv",
    "result.json",
    "filtered_result.csv",
    "database.csv",
)
_S3_URL_RE = re.compile(
    r"https://phantombuster\.s3\.amazonaws\.com/[^\s\"'<>]+?\.(?:csv|json)",
    re.IGNORECASE,
)


def auth_diagnostics(query_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    """Safe auth diagnostics for logs/UI. Never includes the API key value."""
    refresh_lead_sourcing_env()
    api_key = getenv("PHANTOMBUSTER_API_KEY")
    agent_id = getenv("PHANTOMBUSTER_LINKEDIN_AGENT_ID")
    data = {
        "api_key_present": bool(api_key),
        "api_key_length": len(api_key),
        "agent_id_present": bool(agent_id),
        "agent_id": agent_id or None,
        "auth_header": "X-Phantombuster-Key-1",
        "query_key_supported_for_debug": True,
    }
    if query_probe:
        data["query_key_probe"] = query_probe
    return data


def _headers() -> dict[str, str]:
    refresh_lead_sourcing_env()
    api_key = getenv("PHANTOMBUSTER_API_KEY")
    return {
        "X-Phantombuster-Key-1": api_key,
        "Content-Type": "application/json",
    }


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def _parse_json(resp: httpx.Response) -> dict[str, Any]:
    if not resp.text:
        return {}
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except json.JSONDecodeError:
        return {"raw_text": resp.text[:2000]}


def _query_key_probe(
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Debug-only auth probe using ?key=. Never returns the key."""
    refresh_lead_sourcing_env()
    api_key = getenv("PHANTOMBUSTER_API_KEY")
    if not api_key:
        return {"tested": False, "reason": "missing_api_key"}
    query = dict(params or {})
    query["key"] = api_key
    try:
        with _client(timeout) as client:
            resp = client.get(f"{_PB_BASE}{endpoint}", params=query)
        body = resp.text[:220] if resp.text else ""
        return {
            "tested": True,
            "endpoint": endpoint,
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 400,
            "body_prefix": body,
        }
    except httpx.RequestError as e:
        return {"tested": True, "endpoint": endpoint, "error": str(e)[:220]}


def _auth_error_message(
    label: str,
    resp: httpx.Response,
    *,
    endpoint: str,
    params: dict[str, Any] | None = None,
) -> str:
    probe = _query_key_probe(endpoint, params=params) if resp.status_code == 401 else None
    diag = auth_diagnostics(probe)
    query_status = ""
    if probe:
        query_status = (
            f" query_key_status={probe.get('status_code')} "
            f"query_key_ok={probe.get('ok')}"
        )
    return (
        f"{label} {resp.status_code}: {resp.text[:500]} | "
        f"auth={diag['auth_header']} present={diag['api_key_present']} "
        f"len={diag['api_key_length']} agent_id={diag['agent_id']}"
        f"{query_status}"
    )


def fetch_agent(agent_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    params = {"id": agent_id, "withArgument": "true"}
    with _client(timeout) as client:
        resp = client.get(f"{_PB_BASE}/agents/fetch", headers=_headers(), params=params)
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster agents/fetch",
                resp,
                endpoint="/agents/fetch",
                params=params,
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def launch_agent(
    agent_id: str,
    argument: dict[str, Any] | None = None,
    *,
    timeout: float = 45.0,
) -> dict[str, Any]:
    params = {"id": agent_id}
    body: dict[str, Any] = {"id": agent_id}
    if argument:
        body["argument"] = argument
    with _client(timeout) as client:
        resp = client.post(
            f"{_PB_BASE}/agents/launch",
            headers=_headers(),
            params=params,
            json=body,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster launch",
                resp,
                endpoint="/agents/fetch",
                params=params,
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def fetch_container(
    container_id: str,
    *,
    timeout: float = 30.0,
    with_result_object: bool = False,
    with_output: bool = False,
) -> dict[str, Any]:
    params: dict[str, Any] = {"id": container_id}
    if with_result_object:
        params["withResultObject"] = "true"
    if with_output:
        params["withOutput"] = "true"
    with _client(timeout) as client:
        resp = client.get(
            f"{_PB_BASE}/containers/fetch",
            headers=_headers(),
            params=params,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster containers/fetch",
                resp,
                endpoint="/containers/fetch",
                params=params,
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def fetch_container_result_object(container_id: str, *, timeout: float = 45.0) -> dict[str, Any]:
    """Resultado estructurado del launch (no logs de consola)."""
    params = {"id": container_id}
    with _client(timeout) as client:
        resp = client.get(
            f"{_PB_BASE}/containers/fetch-result-object",
            headers=_headers(),
            params=params,
        )
    if resp.status_code == 404:
        return {}
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster containers/fetch-result-object",
                resp,
                endpoint="/containers/fetch-result-object",
                params=params,
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def fetch_container_output(container_id: str, *, timeout: float = 45.0) -> dict[str, Any]:
    params = {"id": container_id}
    with _client(timeout) as client:
        resp = client.get(
            f"{_PB_BASE}/containers/fetch-output",
            headers=_headers(),
            params=params,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster containers/fetch-output",
                resp,
                endpoint="/containers/fetch-output",
                params=params,
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def fetch_agent_output(agent_id: str, *, timeout: float = 45.0) -> dict[str, Any]:
    params = {"id": agent_id}
    with _client(timeout) as client:
        resp = client.get(
            f"{_PB_BASE}/agents/fetch-output",
            headers=_headers(),
            params=params,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster agents/fetch-output",
                resp,
                endpoint="/agents/fetch-output",
                params=params,
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def fetch_leads_by_list(
    list_id: str,
    *,
    timeout: float = 60.0,
    with_companies: bool = True,
) -> dict[str, Any]:
    """Leads List de PhantomBuster (misma fuente que la UI de listas)."""
    body: dict[str, Any] = {}
    if with_companies:
        body["withCompanies"] = True
    with _client(timeout) as client:
        resp = client.post(
            f"{_PB_BASE}/org-storage/leads/by-list/{list_id}",
            headers=_headers(),
            json=body,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            _auth_error_message(
                "PhantomBuster org-storage/leads/by-list",
                resp,
                endpoint=f"/org-storage/leads/by-list/{list_id}",
            ),
            provider="phantombuster",
            status_code=resp.status_code,
        )
    return _parse_json(resp)


def extract_s3_folders(payload: Any) -> tuple[str | None, str | None]:
    """Busca orgS3Folder + s3Folder en respuestas agent/container/output."""
    if not isinstance(payload, dict):
        return None, None

    def _from_dict(d: dict[str, Any]) -> tuple[str | None, str | None]:
        org = d.get("orgS3Folder") or d.get("org_s3_folder")
        s3 = d.get("s3Folder") or d.get("s3_folder")
        if isinstance(org, str) and isinstance(s3, str) and org.strip() and s3.strip():
            return org.strip(), s3.strip()
        return None, None

    org, s3 = _from_dict(payload)
    if org and s3:
        return org, s3

    for key in ("resultObject", "json", "data", "container", "agent"):
        nested = payload.get(key)
        if isinstance(nested, str) and nested.strip().startswith("{"):
            try:
                nested = json.loads(nested)
            except json.JSONDecodeError:
                nested = None
        if isinstance(nested, dict):
            org, s3 = _from_dict(nested)
            if org and s3:
                return org, s3

    return None, None


def build_s3_result_urls(org_s3_folder: str, s3_folder: str) -> list[str]:
    custom = (getenv("PHANTOMBUSTER_RESULT_FILENAME") or "").strip()
    names: list[str] = []
    if custom:
        names.append(custom)
    names.extend(_DEFAULT_S3_RESULT_NAMES)
    seen: set[str] = set()
    urls: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        urls.append(f"{_S3_BASE}/{org_s3_folder}/{s3_folder}/{name}")
    return urls


def download_remote_result(url: str, *, timeout: float = 60.0) -> tuple[Any, str]:
    """Descarga CSV/JSON desde S3 o URL de export manual."""
    with _client(timeout) as client:
        resp = client.get(url)
    if resp.status_code >= 400:
        return [], f"HTTP {resp.status_code} para {url[:120]}"
    parsed = _parse_text_payload(resp.text)
    if isinstance(parsed, list):
        return parsed, f"descargado {url} ({len(parsed)} filas)"
    if isinstance(parsed, dict):
        rows, note = parse_output_rows(parsed)
        if rows:
            return rows, f"descargado {url}; {note}"
        return parsed, f"descargado {url}; dict sin filas"
    return [], f"descargado {url}; contenido no tabular"


def download_s3_results(
    metadata: dict[str, Any],
    *,
    timeout: float = 60.0,
) -> tuple[Any, str, list[str]]:
    """Intenta result.csv/json desde orgS3Folder/s3Folder del agente o container."""
    org, s3 = extract_s3_folders(metadata)
    if not org or not s3:
        return [], "sin orgS3Folder/s3Folder", []
    urls = build_s3_result_urls(org, s3)
    errors: list[str] = []
    for url in urls:
        data, note = download_remote_result(url, timeout=timeout)
        rows, parse_note = parse_output_rows(data)
        if not rows and isinstance(data, list) and data and isinstance(data[0], dict):
            rows = [r for r in data if isinstance(r, dict)]
            parse_note = f"lista CSV ({len(rows)} filas)"
        if rows:
            return rows, f"S3 {note}; {parse_note}", urls
        errors.append(note)
    return [], f"S3 sin filas ({'; '.join(errors[:3])})", urls


def extract_result_urls(payload: Any) -> list[str]:
    """URLs csv/json embebidas en fetch-output o campos dedicados."""
    urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        u = url.strip().rstrip(").,;]")
        if u and u not in seen and _looks_like_url(u):
            seen.add(u)
            urls.append(u)

    if isinstance(payload, dict):
        for key in (
            "csvUrl",
            "jsonUrl",
            "csvURL",
            "jsonURL",
            "resultUrl",
            "resultFileUrl",
            "downloadUrl",
        ):
            val = payload.get(key)
            if isinstance(val, str):
                _add(val)
        output = payload.get("output")
        if isinstance(output, str):
            for match in _S3_URL_RE.findall(output):
                _add(match)
            for line in output.splitlines():
                if "http" in line and (".csv" in line.lower() or ".json" in line.lower()):
                    for token in re.split(r"\s+", line):
                        if token.startswith("http"):
                            _add(token)
    return urls


def fetch_result_from_output_urls(
    *payloads: Any,
    timeout: float = 60.0,
) -> tuple[Any, str, list[str]]:
    """Descarga el primer CSV/JSON encontrado en fetch-output."""
    tried: list[str] = []
    for payload in payloads:
        for url in extract_result_urls(payload):
            tried.append(url)
            data, note = download_remote_result(url, timeout=timeout)
            rows, parse_note = parse_output_rows(data)
            if not rows and isinstance(data, list) and data and isinstance(data[0], dict):
                rows = [r for r in data if isinstance(r, dict)]
            if rows:
                return rows, f"URL en output: {note}; {parse_note}", tried
    return [], "sin URLs csv/json en fetch-output", tried


def parse_org_storage_leads(payload: Any) -> tuple[list[dict], str]:
    """Normaliza respuesta de /org-storage/leads/by-list/{id}."""
    if not isinstance(payload, dict):
        return [], "org-storage: payload no dict"

    raw: Any = None
    for key in ("leads", "data", "items", "results"):
        val = payload.get(key)
        if isinstance(val, list):
            raw = val
            break
        if isinstance(val, dict):
            for inner in ("leads", "items", "data"):
                inner_val = val.get(inner)
                if isinstance(inner_val, list):
                    raw = inner_val
                    break
        if raw is not None:
            break

    if not isinstance(raw, list):
        return [], f"org-storage: sin lista leads; keys={summarize_output_keys(payload)}"

    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        nested = item.get("lead") or item.get("leadObject")
        if isinstance(nested, dict):
            for k, v in nested.items():
                row.setdefault(k, v)
        rows.append(row)
    return rows, f"org-storage leads ({len(rows)} filas)"


def container_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Desenvuelve `data` de respuestas API v2 y fusiona campos del container."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data")
    if isinstance(inner, dict):
        merged = dict(payload)
        merged.update(inner)
        return merged
    return payload


def container_status_text(container: dict[str, Any]) -> str:
    """Status legible del container (no el status HTTP de la API)."""
    c = container_record(container)
    for key in (
        "lastEndStatus",
        "status",
        "state",
        "endType",
        "launchStatus",
        "containerStatus",
    ):
        val = c.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def container_is_terminal(container: dict[str, Any]) -> tuple[bool, str]:
    """
    Detecta si el launch terminó.
    PhantomBuster puede devolver 'Agent finished (success)', lastEndStatus, endDate, etc.
    """
    c = container_record(container)
    status_low = container_status_text(container).lower()

    end_ts = c.get("endDate") or c.get("endedAt") or c.get("finishedAt")
    if end_ts not in (None, "", 0, "0"):
        if "running" not in status_low and "queue" not in status_low:
            return True, "end_timestamp"

    last_end = str(c.get("lastEndStatus") or "").lower().strip()
    if last_end:
        if last_end in (
            "finished",
            "success",
            "done",
            "complete",
            "completed",
            "error",
            "failed",
            "aborted",
            "crash",
        ):
            return True, f"lastEndStatus={last_end}"
        if "finish" in last_end:
            return True, f"lastEndStatus={last_end}"

    if status_low:
        if any(tok in status_low for tok in ("error", "fail", "abort", "crash")):
            return True, f"status={status_low}"
        if "running" in status_low or status_low in ("launching", "queued", "queue", "starting"):
            return False, "still_running"
        if any(tok in status_low for tok in ("finish", "complete", "done", "success")):
            return True, f"status={status_low}"
        if status_low in ("finished", "done", "complete", "completed", "idle"):
            return True, f"status={status_low}"

    exit_code = c.get("exitCode")
    if exit_code is not None and end_ts not in (None, "", 0, "0"):
        return True, f"exitCode={exit_code}"

    return False, "running"


def poll_max_sec() -> float:
    raw = (getenv("PHANTOMBUSTER_POLL_MAX_SEC") or "").strip()
    if raw:
        try:
            return max(15.0, float(raw))
        except ValueError:
            pass
    return float(PHANTOMBUSTER_POLL_MAX_SEC)


def poll_container(
    container_id: str,
    *,
    timeout_sec: float | None = None,
    interval_sec: float = 2.5,
) -> dict[str, Any]:
    """Espera a que el container termine; corta siempre por timeout hard."""
    max_wait = timeout_sec if timeout_sec is not None else poll_max_sec()
    deadline = time.monotonic() + max_wait
    last: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    iteration = 0

    while time.monotonic() < deadline:
        iteration += 1
        elapsed = round(time.monotonic() - (deadline - max_wait), 2)
        try:
            last = fetch_container(container_id)
        except ProviderAPIError as e:
            trace.append(
                {
                    "iteration": iteration,
                    "elapsed_sec": elapsed,
                    "error": str(e)[:200],
                    "break": "fetch_error_continue",
                }
            )
            if time.monotonic() >= deadline:
                break
            time.sleep(interval_sec)
            continue

        terminal, break_reason = container_is_terminal(last)
        status_seen = container_status_text(last)
        trace.append(
            {
                "iteration": iteration,
                "elapsed_sec": elapsed,
                "status": status_seen or None,
                "last_end_status": container_record(last).get("lastEndStatus"),
                "end_date": container_record(last).get("endDate"),
                "terminal": terminal,
                "break": break_reason if terminal else "sleep",
            }
        )
        if terminal:
            last["_poll_break"] = break_reason
            last["_poll_iterations"] = iteration
            last["_poll_elapsed_sec"] = elapsed
            last["_poll_trace"] = trace
            return last

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_sec, max(0.5, remaining)))

    elapsed_total = round(max_wait, 2)
    last["_poll_timeout"] = True
    last["_poll_break"] = "max_wait_exceeded"
    last["_poll_iterations"] = iteration
    last["_poll_elapsed_sec"] = elapsed_total
    last["_poll_trace"] = trace
    trace.append(
        {
            "iteration": iteration,
            "elapsed_sec": elapsed_total,
            "status": container_status_text(last) or None,
            "terminal": False,
            "break": "max_wait_exceeded",
        }
    )
    last["_poll_trace"] = trace
    _logger.warning(
        "[phantombuster] poll timeout container=%s iterations=%s status=%s",
        container_id,
        iteration,
        container_status_text(last),
    )
    return last


def agent_script_name(agent: dict[str, Any]) -> str:
    for key in ("script", "scriptSlug", "name", "scriptTitle"):
        val = agent.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def agent_has_session_cookie(agent: dict[str, Any]) -> bool | None:
    """Heurística: cookie de LinkedIn en argumentos guardados del agente."""
    arg = agent.get("argument")
    if isinstance(arg, str) and arg.strip():
        try:
            arg = json.loads(arg)
        except json.JSONDecodeError:
            arg = {}
    if not isinstance(arg, dict):
        return None
    for key in ("sessionCookie", "session cookie", "linkedinSessionCookie", "cookie"):
        val = arg.get(key)
        if isinstance(val, str) and len(val.strip()) > 20:
            return True
    return False


def summarize_output_keys(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return list(payload.keys())[:20]
    if isinstance(payload, list):
        return [f"list[{len(payload)}]"]
    return [type(payload).__name__]


def hydrate_output_payload(payload: Any, *, timeout: float = 45.0) -> tuple[Any, str]:
    """If PhantomBuster returns an output URL/string wrapper, fetch/expand it."""
    if not isinstance(payload, dict):
        return payload, "payload directo"

    raw = payload.get("output")
    if not isinstance(raw, str) or not raw.strip():
        return payload, "sin output externo"

    text = raw.strip()
    if _looks_like_url(text):
        try:
            with _client(timeout) as client:
                resp = client.get(text)
            if resp.status_code >= 400:
                return payload, f"output URL HTTP {resp.status_code}"
            fetched = _parse_text_payload(resp.text)
            return fetched, f"output URL {resp.status_code}"
        except httpx.RequestError as e:
            return payload, f"output URL error: {e}"

    parsed = _parse_text_payload(text)
    if parsed is not text:
        return parsed, "output string expandido"
    return payload, "output string no estructurado"


def _looks_like_url(text: str) -> bool:
    try:
        parsed = urlparse(text)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _parse_text_payload(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    if "," in stripped and "\n" in stripped:
        try:
            rows = list(csv.DictReader(io.StringIO(stripped)))
            if rows:
                return rows
        except csv.Error:
            pass
    if "\n" in stripped:
        rows = [
            {"name": line.strip()}
            for line in stripped.splitlines()
            if line.strip() and len(line.strip()) <= 180
        ]
        if rows:
            return rows
    return text


def parse_output_rows(payload: Any) -> tuple[list[dict], str]:
    """Devuelve (filas, nota de parseo)."""
    if isinstance(payload, list):
        rows = [r for r in payload if isinstance(r, dict)]
        return rows, f"lista directa ({len(rows)} filas)"

    if not isinstance(payload, dict):
        return [], f"tipo inesperado: {type(payload).__name__}"

    for key in ("data", "output", "resultObject", "json", "container", "results", "profiles", "people", "leads"):
        raw = payload.get(key)
        if isinstance(raw, list):
            rows = [r for r in raw if isinstance(r, dict)]
            return rows, f"campo {key} lista ({len(rows)} filas)"
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    rows = [r for r in parsed if isinstance(r, dict)]
                    return rows, f"campo {key} JSON lista ({len(rows)} filas)"
                if isinstance(parsed, dict):
                    for inner in ("data", "results", "profiles", "people"):
                        inner_val = parsed.get(inner)
                        if isinstance(inner_val, list):
                            rows = [r for r in inner_val if isinstance(r, dict)]
                            return rows, f"{key}.{inner} ({len(rows)} filas)"
            except json.JSONDecodeError:
                if raw.strip().startswith("["):
                    _logger.warning("[phantombuster] JSON inválido en %s", key)

    # Último recurso: buscar primera lista de dicts en valores
    for key, val in payload.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val, f"fallback campo {key} ({len(val)} filas)"

    return [], f"sin filas; keys={summarize_output_keys(payload)}"
