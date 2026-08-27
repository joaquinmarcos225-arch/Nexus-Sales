"""Países LatAm (sin Brasil) para cobro vía dLocal."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatamCountry:
    code: str  # ISO 3166-1 alpha-2
    label: str
    currency: str  # moneda local del pagador


# Objetivo comercial actual: LatAm hispana (sin BR).
LATAM_COUNTRIES: dict[str, LatamCountry] = {
    "AR": LatamCountry("AR", "Argentina", "ARS"),
    "BO": LatamCountry("BO", "Bolivia", "BOB"),
    "CL": LatamCountry("CL", "Chile", "CLP"),
    "CO": LatamCountry("CO", "Colombia", "COP"),
    "CR": LatamCountry("CR", "Costa Rica", "CRC"),
    "EC": LatamCountry("EC", "Ecuador", "USD"),
    "GT": LatamCountry("GT", "Guatemala", "GTQ"),
    "HN": LatamCountry("HN", "Honduras", "HNL"),
    "MX": LatamCountry("MX", "México", "MXN"),
    "NI": LatamCountry("NI", "Nicaragua", "NIO"),
    "PA": LatamCountry("PA", "Panamá", "USD"),
    "PY": LatamCountry("PY", "Paraguay", "PYG"),
    "PE": LatamCountry("PE", "Perú", "PEN"),
    "DO": LatamCountry("DO", "República Dominicana", "DOP"),
    "SV": LatamCountry("SV", "El Salvador", "USD"),
    "UY": LatamCountry("UY", "Uruguay", "UYU"),
}


def normalize_country(code: str | None) -> str | None:
    raw = (code or "").strip().upper()
    if not raw:
        return None
    if raw in LATAM_COUNTRIES:
        return raw
    return None


def is_latam_ex_br(code: str | None) -> bool:
    return normalize_country(code) is not None


def list_latam_countries() -> list[dict[str, str]]:
    return [
        {"code": c.code, "label": c.label, "currency": c.currency}
        for c in sorted(LATAM_COUNTRIES.values(), key=lambda x: x.label)
    ]


def country_currency(code: str | None) -> str:
    c = LATAM_COUNTRIES.get(normalize_country(code) or "")
    return c.currency if c else "USD"
