"""Mapeo ICP Región (UI) → frases de búsqueda + códigos ISO para Brave."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RegionSearchContext:
    """Contexto de búsqueda derivado del valor «Región» del ICP (target_country)."""

    label: str
    query_phrase: str
    brave_country_codes: tuple[str, ...]
    country_names: tuple[str, ...]
    # Países cortos para armar queries (uno por query; no meter la bolsa entera).
    query_country_labels: tuple[str, ...] = ()


def _norm_key(value: str) -> str:
    return re.sub(r"[\s_\-+]+", "", (value or "").strip().lower())


_REGION_DEFS: dict[str, RegionSearchContext] = {
    "na": RegionSearchContext(
        label="NA",
        query_phrase="United States Canada North America",
        brave_country_codes=("US", "CA"),
        country_names=(
            "united states",
            "usa",
            "us",
            "u.s.",
            "canada",
            "canadian",
            "north america",
        ),
        query_country_labels=("United States", "Canada"),
    ),
    "latambrasil": RegionSearchContext(
        label="LATAM (sin Brasil)",
        query_phrase="Latin America Mexico Colombia Argentina Chile Peru Uruguay",
        brave_country_codes=("MX", "CO", "AR", "CL", "PE", "UY"),
        country_names=(
            "mexico",
            "méxico",
            "colombia",
            "argentina",
            "chile",
            "peru",
            "perú",
            "uruguay",
            "ecuador",
            "costa rica",
            "panama",
            "panamá",
            "latin america",
            "latam",
            "latinoamérica",
            "latinoamerica",
        ),
        query_country_labels=(
            "Mexico",
            "Colombia",
            "Argentina",
            "Chile",
            "Peru",
            "Uruguay",
        ),
    ),
    "latambrasilplus": RegionSearchContext(
        label="LATAM + Brasil",
        query_phrase="Latin America Brazil Mexico Colombia Argentina Chile",
        brave_country_codes=("BR", "MX", "AR", "CL"),
        country_names=(
            "brazil",
            "brasil",
            "mexico",
            "méxico",
            "colombia",
            "argentina",
            "chile",
            "peru",
            "perú",
            "uruguay",
            "latin america",
            "latam",
            "latinoamérica",
            "latinoamerica",
        ),
        query_country_labels=(
            "Brazil",
            "Mexico",
            "Colombia",
            "Argentina",
            "Chile",
            "Peru",
        ),
    ),
    "emea": RegionSearchContext(
        label="EMEA",
        query_phrase="Europe UK Germany France Spain Netherlands Ireland",
        brave_country_codes=("GB", "DE", "FR", "ES", "NL", "IE", "IT", "PT", "SE"),
        country_names=(
            "united kingdom",
            "uk",
            "great britain",
            "england",
            "germany",
            "france",
            "spain",
            "netherlands",
            "europe",
            "emea",
            "ireland",
            "italy",
            "portugal",
            "sweden",
        ),
        query_country_labels=(
            "United Kingdom",
            "Germany",
            "France",
            "Spain",
            "Netherlands",
            "Ireland",
        ),
    ),
    "apac": RegionSearchContext(
        label="APAC",
        query_phrase="Asia Pacific Australia Singapore India Japan South Korea",
        brave_country_codes=("AU", "SG", "IN", "JP", "NZ", "KR"),
        country_names=(
            "australia",
            "singapore",
            "india",
            "japan",
            "new zealand",
            "south korea",
            "korea",
            "asia pacific",
            "apac",
        ),
        query_country_labels=(
            "Australia",
            "Singapore",
            "India",
            "Japan",
            "South Korea",
        ),
    ),
}


def _is_empty_region(value: str | None) -> bool:
    if not value or not value.strip():
        return True
    return value.strip().lower() in {"no importante", "no_importante", "any", "global", "worldwide"}


def _latam_variant(raw: str) -> str | None:
    low = raw.lower()
    if not any(x in low for x in ("latam", "latin america", "latinoam")):
        return None
    if "+" in raw or " más " in f" {low} " or " mas " in f" {low} ":
        return "plus"
    if "-" in raw or "sin " in low or "except" in low or "excl" in low:
        return "minus"
    if "brasil" in low or "brazil" in low:
        return "plus"
    return "plus"


def resolve_region_search_context(region: str | None) -> RegionSearchContext | None:
    """Convierte el valor ICP «Región» en contexto usable para Brave y queries."""
    if _is_empty_region(region):
        return None
    raw = (region or "").strip()
    key = _norm_key(raw)

    latam = _latam_variant(raw)
    if latam == "minus":
        return _REGION_DEFS["latambrasil"]
    if latam == "plus":
        return _REGION_DEFS["latambrasilplus"]

    for ctx in _REGION_DEFS.values():
        if key == _norm_key(ctx.label):
            return ctx

    if key in _REGION_DEFS:
        return _REGION_DEFS[key]

    if len(raw) == 2 and raw.isalpha():
        code = raw.upper()
        return RegionSearchContext(
            label=code,
            query_phrase=code,
            brave_country_codes=(code,),
            country_names=(),
        )

    low = raw.lower()
    return RegionSearchContext(
        label=raw,
        query_phrase=raw,
        brave_country_codes=(),
        country_names=(low,),
    )


def brave_country_for_query(ctx: RegionSearchContext | None, query_index: int) -> str | None:
    if not ctx or not ctx.brave_country_codes:
        return None
    return ctx.brave_country_codes[query_index % len(ctx.brave_country_codes)]


_ISO_COUNTRY_LABELS: dict[str, str] = {
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "AR": "Argentina",
    "CL": "Chile",
    "BR": "Brazil",
    "CO": "Colombia",
    "PE": "Peru",
    "UY": "Uruguay",
    "GB": "United Kingdom",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "NL": "Netherlands",
    "IE": "Ireland",
    "IT": "Italy",
    "PT": "Portugal",
    "SE": "Sweden",
}


def country_hint_for_query(ctx: RegionSearchContext | None, query_index: int) -> str | None:
    """País/región de la query (para stampiar empresas hasta que Prospeo aporte HQ real)."""
    if not ctx:
        return None
    code = brave_country_for_query(ctx, query_index)
    if code and code in _ISO_COUNTRY_LABELS:
        return _ISO_COUNTRY_LABELS[code]
    labels = ctx.query_country_labels or ()
    if labels:
        return labels[query_index % len(labels)]
    return ctx.label or None


def _norm_country(value: str | None) -> str:
    return (value or "").strip().lower()


# Alias → etiqueta canónica para inferir país desde snippet/título/URL.
_COUNTRY_INFERENCE_ALIASES: tuple[tuple[str, str], ...] = (
    ("argentina", "Argentina"),
    ("buenos aires", "Argentina"),
    ("mexico", "Mexico"),
    ("méxico", "Mexico"),
    ("cdmx", "Mexico"),
    ("colombia", "Colombia"),
    ("bogotá", "Colombia"),
    ("bogota", "Colombia"),
    ("chile", "Chile"),
    ("santiago", "Chile"),
    ("peru", "Peru"),
    ("perú", "Peru"),
    ("lima", "Peru"),
    ("uruguay", "Uruguay"),
    ("montevideo", "Uruguay"),
    ("ecuador", "Ecuador"),
    ("costa rica", "Costa Rica"),
    ("panama", "Panama"),
    ("panamá", "Panama"),
    ("brazil", "Brazil"),
    ("brasil", "Brazil"),
    ("são paulo", "Brazil"),
    ("sao paulo", "Brazil"),
    ("united states", "United States"),
    ("usa", "United States"),
    ("u.s.", "United States"),
    ("canada", "Canada"),
    ("toronto", "Canada"),
    ("united kingdom", "United Kingdom"),
    ("london", "United Kingdom"),
    ("germany", "Germany"),
    ("berlin", "Germany"),
    ("france", "France"),
    ("paris", "France"),
    ("spain", "Spain"),
    ("madrid", "Spain"),
    ("netherlands", "Netherlands"),
    ("amsterdam", "Netherlands"),
    ("ireland", "Ireland"),
    ("dublin", "Ireland"),
    ("australia", "Australia"),
    ("sydney", "Australia"),
    ("singapore", "Singapore"),
    ("india", "India"),
    ("mumbai", "India"),
    ("japan", "Japan"),
    ("tokyo", "Japan"),
)


def infer_country_from_text(text: str | None) -> str | None:
    """Inferir país desde snippet, título o URL (sin usar hint rotativo de query)."""
    blob = (text or "").strip().lower()
    if not blob:
        return None
    best: tuple[int, str] | None = None
    for alias, label in _COUNTRY_INFERENCE_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", blob):
            rank = len(alias)
            if best is None or rank > best[0]:
                best = (rank, label)
    return best[1] if best else None


def countries_mentioned_in_text(text: str | None) -> list[str]:
    """Países explícitos mencionados en texto (puede haber más de uno)."""
    blob = (text or "").strip().lower()
    if not blob:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for alias, label in _COUNTRY_INFERENCE_ALIASES:
        if label in seen:
            continue
        if re.search(rf"\b{re.escape(alias)}\b", blob):
            found.append(label)
            seen.add(label)
    return found


def text_has_conflicting_country(text: str | None, campaign_region: str | None) -> bool:
    """True si el texto menciona un país claramente fuera de la región ICP."""
    if _is_empty_region(campaign_region):
        return False
    for country in countries_mentioned_in_text(text):
        score, _ = score_region_alignment(campaign_region, country)
        if score == 0:
            return True
    return False


def score_region_alignment(
    campaign_region: str | None,
    prospect_country: str | None,
) -> tuple[int, str]:
    """Puntúa alineación región ICP vs país del prospecto (0–100)."""
    if _is_empty_region(campaign_region):
        return 0, "ICP región no configurada"
    ctx = resolve_region_search_context(campaign_region)
    pc = _norm_country(prospect_country)
    if not pc:
        return 0, "País del prospecto desconocido (región ICP activa)"
    if not ctx:
        cc = _norm_country(campaign_region)
        if pc == cc:
            return 100, "Región/país coincide con ICP"
        if cc in pc or pc in cc:
            return 70, "Región parcialmente alineada"
        return 0, "Región no alineada con ICP"

    # LATAM (sin Brasil): "brasil" NUNCA puede matchear por substring del label.
    latam = _latam_variant(campaign_region or "")
    if latam == "minus" and pc in {"brasil", "brazil", "br"}:
        return 0, "Brasil está excluido de la región ICP (LATAM sin Brasil)"

    if pc in ctx.country_names:
        return 100, f"País del prospecto en región ICP ({ctx.label})"

    for name in ctx.country_names:
        if len(name) >= 4 and (name in pc or pc in name):
            return 85, f"País alineado con región ICP ({ctx.label})"

    cc = _norm_country(ctx.label)
    if pc == cc:
        return 100, "Región coincide con ICP"
    # Contención solo si el label completo está dentro del país del prospecto.
    # Nunca al revés: evita que "brasil" matchee "latam - brasil".
    if cc and len(cc) >= 4 and cc in pc:
        return 100, "Región coincide con ICP"

    if ctx.label.upper() == pc.upper() and len(pc) == 2:
        return 100, "Código país coincide con ICP"

    return 0, f"País fuera de región ICP ({ctx.label})"
