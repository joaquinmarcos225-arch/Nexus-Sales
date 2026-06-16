"""Validación de URL LinkedIn personal (/in/...) del contacto."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_DEMO_SLUG_RE = re.compile(
    r"demo[-_]|test[-_]|fake[-_]|mock[-_]|sample[-_]|example",
    re.I,
)


def normalize_linkedin_url(raw: str | None) -> str | None:
    """URL absoluta https://www.linkedin.com/in/... o None."""
    url = (raw or "").strip()
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if "linkedin.com" not in (parsed.netloc or "").lower():
        return None
    path = (parsed.path or "").lower()
    if not path.startswith("/in/") and not path.startswith("/sales/people/"):
        return None
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def is_personal_linkedin_url(raw: str | None) -> bool:
    """True si es perfil personal del contacto (no empresa / no demo)."""
    normalized = normalize_linkedin_url(raw)
    if not normalized:
        return False
    try:
        parsed = urlparse(normalized)
    except Exception:
        return False
    path = (parsed.path or "").lower()
    if path.startswith("/sales/people/"):
        return True
    if not path.startswith("/in/"):
        return False
    if _DEMO_SLUG_RE.search(path):
        return False
    slug = path.removeprefix("/in/").split("/")[0].strip()
    if not slug or len(slug) < 2:
        return False
    if slug in ("company", "school", "groups", "feed", "jobs", "posts"):
        return False
    return True
