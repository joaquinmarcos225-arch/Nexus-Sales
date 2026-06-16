"""Brave / SerpAPI — búsqueda web para empresas (NO Google Custom Search JSON)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import httpx

from app.services.lead_sourcing.env_config import getenv
from app.services.lead_sourcing.timeouts_config import WEB_SEARCH_HTTP_TIMEOUT
from app.services.lead_sourcing.providers.base import ProviderAPIError

SearchHit = tuple[str, str, str]  # (url, title, snippet)

# Único endpoint de búsqueda de empresas en Nexus (Brave Web Search API).
_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
_SERPAPI_URL = "https://serpapi.com/search.json"


@dataclass(frozen=True)
class WebSearchBackend:
    name: str
    env_key: str
    label: str


BACKENDS: tuple[WebSearchBackend, ...] = (
    WebSearchBackend("brave", "BRAVE_SEARCH_API_KEY", "Brave"),
    WebSearchBackend("serpapi", "SERPAPI_API_KEY", "SerpAPI"),
)


def legacy_google_search_env_present() -> bool:
    """Variables legacy — nunca se usan para company sourcing."""
    return bool(getenv("GOOGLE_SEARCH_API_KEY") or getenv("GOOGLE_SEARCH_ENGINE_ID"))


def resolve_backend() -> tuple[WebSearchBackend | None, Callable[..., list[SearchHit]] | None]:
    """
    Elige backend por WEB_SEARCH_PROVIDER o la primera API key válida.
    GOOGLE_SEARCH_* se ignora por completo (Custom Search JSON API descontinuado).
    """
    explicit = getenv("WEB_SEARCH_PROVIDER").lower()
    by_name = {b.name: b for b in BACKENDS}

    if explicit:
        backend = by_name.get(explicit)
        if backend is None:
            return None, None
        if not getenv(backend.env_key):
            return backend, None
        return backend, _search_fn(backend.name)

    for backend in BACKENDS:
        if getenv(backend.env_key):
            return backend, _search_fn(backend.name)
    return None, None


def configured_backend() -> WebSearchBackend | None:
    backend, fn = resolve_backend()
    if backend and fn:
        return backend
    return None


def missing_keys_hint() -> str:
    return (
        "Definí BRAVE_SEARCH_API_KEY (recomendado, api.search.brave.com) "
        "o SERPAPI_API_KEY. GOOGLE_SEARCH_* ya no se usa."
    )


def _search_fn(name: str) -> Callable[..., list[SearchHit]]:
    return {"brave": _search_brave, "serpapi": _search_serpapi}[name]


def search_web(
    query: str,
    *,
    limit: int = 20,
    country: str | None = None,
    provider: str = "web_search",
) -> list[SearchHit]:
    backend, fn = resolve_backend()
    if not backend or not fn:
        hint = missing_keys_hint()
        if legacy_google_search_env_present():
            hint += (
                " Detectamos GOOGLE_SEARCH_API_KEY en .env: esa integración fue "
                "reemplazada; no se llama a Google Custom Search."
            )
        raise ProviderAPIError(f"Web Search no configurado. {hint}", provider=provider)
    return fn(query, limit=limit, country=country, provider=provider, backend=backend)


def _search_brave(
    query: str,
    *,
    limit: int,
    country: str | None,
    provider: str,
    backend: WebSearchBackend,
) -> list[SearchHit]:
    api_key = getenv(backend.env_key)
    params: dict[str, str | int] = {"q": query, "count": min(max(limit, 1), 20)}
    if country and len(country.strip()) == 2:
        params["country"] = country.strip().upper()
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    data = _http_get(_BRAVE_URL, headers=headers, params=params, provider=provider, label="Brave")
    web = data.get("web") if isinstance(data, dict) else {}
    items = (web.get("results") or []) if isinstance(web, dict) else []
    return _hits_from_items(items, url_keys=("url",), title_keys=("title",))


def _search_serpapi(
    query: str,
    *,
    limit: int,
    country: str | None,
    provider: str,
    backend: WebSearchBackend,
) -> list[SearchHit]:
    api_key = getenv(backend.env_key)
    params: dict[str, str | int] = {
        "engine": "google",
        "q": query,
        "api_key": api_key,
        "num": min(max(limit, 1), 20),
    }
    if country:
        gl = country.strip().lower()
        if len(gl) == 2:
            params["gl"] = gl
    data = _http_get(_SERPAPI_URL, params=params, provider=provider, label="SerpAPI")
    items = data.get("organic_results") or []
    return _hits_from_items(items, url_keys=("link", "url"), title_keys=("title",))


def _http_get(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    provider: str,
    label: str,
) -> dict:
    try:
        with httpx.Client(timeout=WEB_SEARCH_HTTP_TIMEOUT) as client:
            resp = client.get(url, headers=headers, params=params)
    except httpx.RequestError as e:
        raise ProviderAPIError(f"Web Search ({label}): {e}", provider=provider) from e
    return _parse_response(resp, provider=provider, label=label)


def _parse_response(resp: httpx.Response, *, provider: str, label: str) -> dict:
    if resp.status_code == 401:
        raise ProviderAPIError(
            f"Web Search ({label}): API key inválida (401).",
            provider=provider,
            status_code=401,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            f"Web Search ({label}) {resp.status_code}: {resp.text[:300]}",
            provider=provider,
            status_code=resp.status_code,
        )
    return resp.json() if resp.text else {}


def _hits_from_items(
    items: list,
    *,
    url_keys: tuple[str, ...],
    title_keys: tuple[str, ...],
    snippet_keys: tuple[str, ...] = ("description", "snippet", "meta_description"),
) -> list[SearchHit]:
    out: list[SearchHit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = ""
        for key in url_keys:
            link = (item.get(key) or "").strip()
            if link:
                break
        title = ""
        for key in title_keys:
            title = (item.get(key) or "").strip()
            if title:
                break
        snippet = ""
        for key in snippet_keys:
            snippet = (item.get(key) or "").strip()
            if snippet:
                break
        if link:
            out.append((link, title, snippet))
    return out
