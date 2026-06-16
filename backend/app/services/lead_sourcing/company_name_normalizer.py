"""Normaliza nombres de empresa y filtra títulos SEO de resultados Web Search."""

from __future__ import annotations

import re

SEO_TITLE_MARKERS = re.compile(
    r"\b("
    r"reviews?|pricing|prices|"
    r"top\s*\d*|best\s*\d*|\btop\b|\bbest\b|"
    r"companies|company\s+list|software\s+picks?|"
    r"\bfree\b|services?|"
    r"management\s+platforms?|"
    r"development|"
    r"vs\.?|versus|comparison|compared|"
    r"alternatives?|competitors?"
    r")\b",
    re.I,
)

SEO_LISTING_TITLE = re.compile(
    r"(^|\b)("
    r"top\s+\d+|best\s+\d+|"
    r"\d+\s+best|\d+\s+top|"
    r"list\s+of|directory|directorio|"
    r"software\s+picks|companies\s+to\s+watch|"
    r"ranking|roundup|guide\s+to"
    r")(\b|$)",
    re.I,
)

_TRAILING_JUNK = re.compile(
    r"\s*[\|\-–—:]\s*.*$|"
    r"\s*\(\d+\)\s*.*$|"
    r"\s*,\s*(pricing|reviews?|services?|features?|alternatives?).*$",
    re.I,
)

_MULTI_SPACE = re.compile(r"\s+")


def is_seo_listing_title(title: str) -> bool:
    t = (title or "").strip()
    if not t or len(t) < 3:
        return False
    if SEO_LISTING_TITLE.search(t):
        return True
    markers = len(SEO_TITLE_MARKERS.findall(t))
    if markers >= 2:
        return True
    if markers >= 1 and len(t) > 45:
        return True
    if re.search(r"\b(saas|software)\s+development\b", t, re.I):
        return True
    if re.search(r"\b\w+\s+(development|services)\s*$", t, re.I) and len(t.split()) <= 3:
        return True
    return False


def normalize_company_name(raw: str) -> str | None:
    """
    Limpia títulos tipo:
    «Logiciel Solutions Reviews (5), Pricing, Services & ...» → «Logiciel Solutions»
    """
    t = (raw or "").strip()
    if not t:
        return None

    t = _TRAILING_JUNK.sub("", t, count=1)
    t = re.sub(r"\s*[\|\-–—:].*$", "", t, count=1)
    t = re.sub(r"\s*\([^)]*\)\s*", " ", t)
    t = re.sub(
        r",?\s*\b(reviews?|pricing|services?|features?|alternatives?|free trial)\b.*$",
        "",
        t,
        flags=re.I,
    )
    t = _MULTI_SPACE.sub(" ", t).strip(" ,.-–—|")

    if not t or len(t) < 2:
        return None
    if is_seo_listing_title(t):
        return None
    if len(t) > 80:
        t = t[:80].rsplit(" ", 1)[0].strip()
    return t[:255] if t else None


def name_from_slug(slug: str) -> str | None:
    s = (slug or "").strip().strip("/")
    if not s or s in ("search", "jobs", "login", "signup", "products"):
        return None
    name = s.replace("-", " ").replace("_", " ").title()
    return normalize_company_name(name) or name
