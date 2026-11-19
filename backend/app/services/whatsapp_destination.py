"""País y costo estimado de un destino WhatsApp (E.164).

Las tarifas son de referencia para el tope económico — no cotizan en vivo.
Si el país no está en la lista aprobada, el auto-envío se corta.
"""

from __future__ import annotations

import re

# Hispano LatAm + ES. Brasil queda afuera del default (regla de prospectos Nexus).
DEFAULT_APPROVED_DESTINATIONS: tuple[str, ...] = (
    "AR",
    "CL",
    "MX",
    "CO",
    "PE",
    "UY",
    "EC",
    "PY",
    "BO",
    "CR",
    "DO",
    "PA",
    "ES",
)

# Marketing PMP aproximado + fee Gupshup ~0.001. Conservador (sobreestima).
_MARKETING_USD: dict[str, float] = {
    "AR": 0.063,
    "CL": 0.090,
    "MX": 0.045,
    "CO": 0.014,
    "PE": 0.045,
    "UY": 0.080,
    "EC": 0.050,
    "PY": 0.050,
    "BO": 0.050,
    "CR": 0.055,
    "DO": 0.055,
    "PA": 0.055,
    "ES": 0.070,
    "US": 0.030,
    "BR": 0.070,
}
_UNKNOWN_USD = 0.12
_GUPSHUP_FEE_USD = 0.001

# Prefijos más largos primero (NANP DO vs US).
_PREFIXES: tuple[tuple[str, str], ...] = (
    ("1809", "DO"),
    ("1829", "DO"),
    ("1849", "DO"),
    ("598", "UY"),
    ("593", "EC"),
    ("595", "PY"),
    ("591", "BO"),
    ("506", "CR"),
    ("507", "PA"),
    ("54", "AR"),
    ("56", "CL"),
    ("52", "MX"),
    ("57", "CO"),
    ("51", "PE"),
    ("55", "BR"),
    ("34", "ES"),
    ("1", "US"),
)


def digits_only(raw: str | None) -> str:
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("00"):
        d = d[2:]
    return d


def country_from_digits(raw: str | None) -> str | None:
    digits = digits_only(raw)
    if len(digits) < 8:
        return None
    for prefix, iso in _PREFIXES:
        if digits.startswith(prefix):
            return iso
    return None


def estimated_marketing_usd(country: str | None) -> float:
    iso = (country or "").strip().upper()
    base = _MARKETING_USD.get(iso, _UNKNOWN_USD)
    return round(base + _GUPSHUP_FEE_USD, 4)


def normalize_destination_list(raw: list[str] | tuple[str, ...] | None) -> list[str]:
    if not raw:
        return list(DEFAULT_APPROVED_DESTINATIONS)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        iso = str(item or "").strip().upper()
        if len(iso) != 2 or iso in seen:
            continue
        seen.add(iso)
        out.append(iso)
    return out or list(DEFAULT_APPROVED_DESTINATIONS)
