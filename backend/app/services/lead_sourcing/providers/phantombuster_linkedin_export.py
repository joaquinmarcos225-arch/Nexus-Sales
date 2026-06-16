"""
Argumentos de launch para PhantomBuster «LinkedIn Search Export.js».

PB documenta: linkedInSearchUrl (URL de búsqueda people en linkedin.com) + cookie/session.
No usa companyNames, arrays de queries ni texto suelto sin URL.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from app.services.lead_sourcing.env_config import getenv

# Campos de sesión que se copian del agente guardado (no sobrescriben la búsqueda).
_SESSION_ARG_KEYS = frozenset(
    {
        "sessionCookie",
        "session cookie",
        "linkedinSessionCookie",
        "cookie",
        "userAgent",
        "user agent",
    }
)

# Claves conocidas del script LinkedIn Search Export (para debug / introspección).
SEARCH_EXPORT_INPUT_KEYS = frozenset(
    {
        "linkedInSearchUrl",
        "linkedinsearchurl",
        "searchUrl",
        "search",
        "keywords",
        "numberOfProfiles",
        "numberOfProfilesPerLaunch",
        "numberOfProfilesPerSearch",
        "sessionCookie",
        "userAgent",
    }
)


def parse_agent_argument(agent: dict[str, Any]) -> dict[str, Any]:
    raw = agent.get("argument")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def agent_argument_schema_debug(agent: dict[str, Any]) -> dict[str, Any]:
    """Resumen de inputs guardados en el agente (sin secretos)."""
    saved = parse_agent_argument(agent)
    keys = sorted(saved.keys())
    redacted: dict[str, Any] = {}
    for k in keys:
        v = saved[k]
        if k in _SESSION_ARG_KEYS or "cookie" in k.lower():
            redacted[k] = "[redacted]" if v else None
        elif isinstance(v, str) and len(v) > 120:
            redacted[k] = v[:120] + "…"
        else:
            redacted[k] = v
    return {
        "argument_keys": keys,
        "argument_redacted": redacted,
        "saved_linkedInSearchUrl": _pick_search_url(saved),
        "script": agent.get("script") or agent.get("name"),
    }


def _pick_search_url(arg: dict[str, Any]) -> str | None:
    for key in ("linkedInSearchUrl", "linkedinSearchUrl", "searchUrl", "search"):
        val = arg.get(key)
        if isinstance(val, str) and "linkedin.com/search" in val.lower():
            return val.strip()
    return None


def build_linkedin_people_search_url(keywords: str) -> str:
    """URL people search estándar de LinkedIn (lo que PB Search Export espera)."""
    q = (keywords or "").strip()
    encoded = quote(q, safe="")
    return (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={encoded}&origin=GLOBAL_SEARCH_HEADER"
    )


def use_saved_agent_config_only() -> bool:
    raw = (getenv("PHANTOMBUSTER_USE_SAVED_AGENT_CONFIG") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def allow_stale_output_sources() -> bool:
    """Si false (default), solo leemos output del container actual (no S3 agent / leads list)."""
    raw = (getenv("PHANTOMBUSTER_ALLOW_STALE_OUTPUT") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def minimal_single_search_query() -> str | None:
    """Prueba mínima: una sola query fija (ej. «"Saas Labs" Founder»)."""
    q = (getenv("PHANTOMBUSTER_SINGLE_SEARCH_QUERY") or "").strip()
    return q or None


def build_linkedin_search_export_launch_argument(
    *,
    keywords: str,
    linkedin_search_url: str | None = None,
    number_of_profiles: int = 25,
    saved_argument: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Payload mínimo para LinkedIn Search Export.js.
    La URL es la fuente de verdad; keywords/search son fallback documentado por PB.
    """
    kw = (keywords or "").strip()
    url = (linkedin_search_url or "").strip() or build_linkedin_people_search_url(kw)
    limit = max(1, min(100, int(number_of_profiles)))

    launch: dict[str, Any] = {
        "linkedInSearchUrl": url,
        "searchUrl": url,
        "numberOfProfiles": limit,
        "numberOfProfilesPerLaunch": limit,
        "numberOfProfilesPerSearch": limit,
    }
    if kw:
        launch["search"] = kw
        launch["keywords"] = kw

    saved = saved_argument or {}
    for key, val in saved.items():
        if key in _SESSION_ARG_KEYS:
            launch[key] = val
        elif key.replace(" ", "").lower() in {"sessioncookie", "useragent"}:
            launch[key] = val

    return launch


def launch_argument_diff_note(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Compara dos payloads de launch (debug: ¿cambió la URL?)."""
    url_a = a.get("linkedInSearchUrl") or a.get("searchUrl")
    url_b = b.get("linkedInSearchUrl") or b.get("searchUrl")
    if url_a == url_b:
        return "misma linkedInSearchUrl"
    return f"url_a={str(url_a)[:80]} | url_b={str(url_b)[:80]}"
