"""Brave / SerpAPI — búsqueda web para empresas (NO Google Custom Search JSON)."""

from __future__ import annotations

import time
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

# Brave Web Search solo acepta este enum (ISO). Códigos LATAM como CO/PE/UY fallan 422.
_BRAVE_COUNTRY_CODES = frozenset(
    {
        "ALL",
        "AR",
        "AU",
        "AT",
        "BE",
        "BR",
        "CA",
        "CL",
        "CN",
        "DK",
        "FI",
        "FR",
        "DE",
        "HK",
        "IN",
        "ID",
        "IT",
        "JP",
        "KR",
        "MY",
        "MX",
        "NL",
        "NZ",
        "NO",
        "PL",
        "PT",
        "PH",
        "RU",
        "SA",
        "ZA",
        "ES",
        "SE",
        "CH",
        "TW",
        "TR",
        "GB",
        "US",
    }
)


def brave_country_param(country: str | None) -> str | None:
    """Normaliza country para Brave; None = no enviar el param (evita 422)."""
    if not country:
        return None
    code = country.strip().upper()
    if len(code) != 2:
        return None
    if code in _BRAVE_COUNTRY_CODES:
        return code
    return None



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
    brave_country = brave_country_param(country)
    if brave_country:
        params["country"] = brave_country
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
    if resp.status_code == 402:
        raise ProviderAPIError(
            f"Web Search ({label}): límite de uso agotado (402). "
            "Renová el plan de Brave o agregá SERPAPI_API_KEY como fallback.",
            provider=provider,
            status_code=402,
        )
    if resp.status_code == 429:
        raise ProviderAPIError(
            f"Web Search ({label}): rate limit (429). Reintentá en unos minutos.",
            provider=provider,
            status_code=429,
        )
    if resp.status_code >= 400:
        raise ProviderAPIError(
            f"Web Search ({label}) {resp.status_code}: {resp.text[:300]}",
            provider=provider,
            status_code=resp.status_code,
        )
    return resp.json() if resp.text else {}


_BRAVE_QUOTA_EXHAUSTED_UNTIL: float = 0.0


def brave_quota_exhausted() -> bool:
    return time.monotonic() < _BRAVE_QUOTA_EXHAUSTED_UNTIL


def mark_brave_quota_exhausted(*, cooldown_sec: int = 1800) -> None:
    global _BRAVE_QUOTA_EXHAUSTED_UNTIL
    _BRAVE_QUOTA_EXHAUSTED_UNTIL = time.monotonic() + max(300, int(cooldown_sec))


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
    if backend.name == "brave" and brave_quota_exhausted():
        # No martillar Brave 402: ir directo a SerpAPI / DDG.
        serp = next((b for b in BACKENDS if b.name == "serpapi" and getenv(b.env_key)), None)
        if serp is not None:
            try:
                hits = _search_serpapi(
                    query,
                    limit=limit,
                    country=country,
                    provider=provider,
                    backend=serp,
                )
                _record_web_cogs("serpapi")
                return hits
            except ProviderAPIError:
                pass
        hits = _search_ddg_html(query, limit=limit, provider=provider)
        if hits:
            _record_web_cogs("ddg")
            return hits
        raise ProviderAPIError(
            "Web Search (Brave): límite de uso agotado (402). "
            "Renová el plan de Brave o agregá SERPAPI_API_KEY como fallback.",
            provider=provider,
            status_code=402,
        )
    try:
        hits = fn(query, limit=limit, country=country, provider=provider, backend=backend)
        _record_web_cogs(backend.name)
        return hits
    except ProviderAPIError as exc:
        # Brave quota / outage → SerpAPI (si hay key) → HTML DuckDuckGo.
        if backend.name == "brave" and exc.status_code in (402, 429, 503):
            if exc.status_code == 402:
                mark_brave_quota_exhausted()
            # Contar el intento Brave fallido por cuota (igual consumió request).
            if exc.status_code in (402, 429):
                _record_web_cogs("brave")
            serp = next((b for b in BACKENDS if b.name == "serpapi" and getenv(b.env_key)), None)
            if serp is not None:
                try:
                    hits = _search_serpapi(
                        query,
                        limit=limit,
                        country=country,
                        provider=provider,
                        backend=serp,
                    )
                    _record_web_cogs("serpapi")
                    return hits
                except ProviderAPIError:
                    pass
            hits = _search_ddg_html(query, limit=limit, provider=provider)
            if hits:
                _record_web_cogs("ddg")
                return hits
        raise


def _record_web_cogs(backend: str) -> None:
    try:
        from app.services.lead_sourcing.cogs_runtime_metrics import record_web_search

        record_web_search(backend=backend, n=1)
    except Exception:  # noqa: BLE001
        pass


def _search_ddg_html(
    query: str,
    *,
    limit: int,
    provider: str,
) -> list[SearchHit]:
    """Fallback sin API key cuando Brave está sin cuota."""
    import re
    from html import unescape
    from urllib.parse import quote_plus, unquote

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    # UA de navegador: el bot-style a veces devuelve HTML vacío / sin result__a.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    }
    try:
        with httpx.Client(timeout=WEB_SEARCH_HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.RequestError:
        return []
    if resp.status_code >= 400 or not resp.text:
        return []

    # class puede ir antes o después de href en el HTML lite de DDG.
    patterns = (
        re.compile(
            r'class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r'<a[^>]*href="([^"]+)"[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        ),
    )
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for pattern in patterns:
        for href, title_html in pattern.findall(resp.text):
            title = re.sub(r"<[^>]+>", "", unescape(title_html)).strip()
            link = unescape(href).strip().replace("&amp;", "&")
            if link.startswith("//"):
                link = "https:" + link
            if "uddg=" in link:
                m = re.search(r"uddg=([^&]+)", link)
                if m:
                    link = unquote(m.group(1))
            if not link.startswith("http") or not title:
                continue
            key = link.lower().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            hits.append((link, title, ""))
            if len(hits) >= max(limit, 1):
                return hits
    return hits


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
