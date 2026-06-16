"""HTTP fetch para páginas de directorios (httpx)."""

from __future__ import annotations

import logging
import time
from urllib.parse import urljoin, urlparse

import httpx

from app.services.lead_sourcing.timeouts_config import DIRECTORY_FETCH_TIMEOUT

_logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (compatible; NexusSales/1.0; +https://github.com/nexus-sales) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_LAST_FETCH_AT = 0.0
_MIN_INTERVAL_SEC = 0.8


class DirectoryFetchError(RuntimeError):
    pass


def fetch_html(url: str, *, timeout: float | None = None) -> str:
    timeout = timeout if timeout is not None else DIRECTORY_FETCH_TIMEOUT
    global _LAST_FETCH_AT
    elapsed = time.monotonic() - _LAST_FETCH_AT
    if elapsed < _MIN_INTERVAL_SEC:
        time.sleep(_MIN_INTERVAL_SEC - elapsed)

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    parsed = urlparse(url)
    if parsed.netloc:
        headers["Referer"] = f"{parsed.scheme or 'https'}://{parsed.netloc}/"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
        _LAST_FETCH_AT = time.monotonic()
    except httpx.RequestError as e:
        raise DirectoryFetchError(f"No se pudo abrir {url}: {e}") from e

    if resp.status_code >= 400:
        raise DirectoryFetchError(f"HTTP {resp.status_code} al abrir {url}")

    return resp.text or ""


def build_page_urls(base_url: str, max_pages: int) -> list[str]:
    """Genera URLs de paginación comunes (?page=, ?p=)."""
    urls = [base_url]
    if max_pages <= 1:
        return urls

    parsed = urlparse(base_url)
    sep = "&" if parsed.query else "?"
    for page in range(2, max_pages + 1):
        urls.append(f"{base_url}{sep}page={page}")
        if len(urls) >= max_pages:
            break
    return urls[:max_pages]


def absolutize(base: str, href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("http"):
        return href
    return urljoin(base, href)
