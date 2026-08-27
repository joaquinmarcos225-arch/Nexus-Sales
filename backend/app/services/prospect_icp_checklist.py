"""Checklist ICP visible por prospecto (solo lectura, no afecta sourcing/import)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.services import campaign_icp as icp
from app.services.lead_sourcing.icp_import_gate import (
    MIN_ROLE_MATCH_FOR_IMPORT,
    geo_hard_score,
    industry_hard_score,
    size_hard_score,
)
from app.services.lead_sourcing.icp_region import resolve_region_search_context
from app.services.lead_sourcing.role_alignment import best_icp_role_match


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _clean(value))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _active(value: str | None) -> bool:
    """Solo dims ICP reales. «No importante» / vacío / nulo → no aparecen en checklist."""
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    return not icp.is_icp_token_empty(text)


_PHONE_COUNTRIES = (
    ("598", "Uruguay"),
    ("351", "Portugal"),
    ("593", "Ecuador"),
    ("506", "Costa Rica"),
    ("507", "Panamá"),
    ("54", "Argentina"),
    ("55", "Brasil"),
    ("56", "Chile"),
    ("57", "Colombia"),
    ("51", "Perú"),
    ("52", "México"),
    ("34", "España"),
    ("44", "United Kingdom"),
)

_TLD_COUNTRIES = {
    "ar": "Argentina",
    "br": "Brasil",
    "cl": "Chile",
    "co": "Colombia",
    "mx": "México",
    "pe": "Perú",
    "uy": "Uruguay",
    "ec": "Ecuador",
    "cr": "Costa Rica",
    "pa": "Panamá",
    "es": "España",
    "pt": "Portugal",
    "uk": "United Kingdom",
}

_COUNTRY_LANGUAGE = {
    "argentina": "Español",
    "chile": "Español",
    "colombia": "Español",
    "mexico": "Español",
    "peru": "Español",
    "uruguay": "Español",
    "ecuador": "Español",
    "costa rica": "Español",
    "panama": "Español",
    "espana": "Español",
    "brasil": "Portugués",
    "brazil": "Portugués",
    "portugal": "Portugués",
    "united kingdom": "Inglés",
    "united states": "Inglés",
    "canada": "Inglés",
}


def _observed_country(prospect: Prospect, campaign: Campaign) -> tuple[str | None, str]:
    """País observado; nunca trata el label macro de la campaña como evidencia personal."""
    raw = _clean(getattr(prospect, "country", None))
    ctx = resolve_region_search_context(getattr(campaign, "target_country", None))
    macro_labels = {
        _norm(getattr(campaign, "target_country", None)),
        _norm(ctx.label if ctx else None),
        "latam",
        "latin america",
        "emea",
        "apac",
        "na",
    }
    if raw and _norm(raw) not in macro_labels:
        return raw, "dato del prospecto"

    phone = re.sub(r"\D", "", _clean(getattr(prospect, "phone", None) or getattr(prospect, "whatsapp", None)))
    for prefix, country in _PHONE_COUNTRIES:
        if phone.startswith(prefix):
            return country, "inferido por prefijo telefónico"

    email = _clean(getattr(prospect, "email", None)).lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if tld in _TLD_COUNTRIES:
        return _TLD_COUNTRIES[tld], "inferido por dominio del email"
    return None, "país no confirmado"


def _item(
    key: str,
    label: str,
    target: str,
    state: str,
    *,
    actual: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    # Sin evidencia en contra → se muestra como coincide (UI amigable).
    if state == "unknown":
        state = "match"
        reason = reason or "sin dato en contra"
    return {
        "key": key,
        "label": label,
        "target": target,
        "actual": actual,
        "state": state,
        "matched": state == "match",
        "reason": reason,
    }


def _token_overlap(target: str, actual: str) -> bool:
    wanted = {x for x in re.findall(r"[a-záéíóúñü0-9]+", _norm(target)) if len(x) > 2}
    found = {x for x in re.findall(r"[a-záéíóúñü0-9]+", _norm(actual)) if len(x) > 2}
    return bool(wanted and wanted.intersection(found))


def build_prospect_icp_checklist(prospect: Prospect, campaign: Campaign | None) -> list[dict[str, Any]]:
    """Desactivado: la UI ya no muestra chips coincide / no coincide."""
    del prospect, campaign
    return []
