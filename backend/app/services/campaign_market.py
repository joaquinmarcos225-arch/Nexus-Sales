"""Resolución de market_scope (producto) y outreach_mode (campaña)."""

from __future__ import annotations

from app.models.enums import MarketScope, OutreachMode
from app.models.product import Product

VALID_MARKET_SCOPES = {m.value for m in MarketScope}
VALID_OUTREACH_MODES = {m.value for m in OutreachMode}


def normalize_market_scope(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in VALID_MARKET_SCOPES:
        return raw
    return MarketScope.b2b.value


def normalize_outreach_mode(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in VALID_OUTREACH_MODES:
        return raw
    return OutreachMode.b2b.value


def product_market_scope(product: Product | None) -> str:
    if product is None:
        return MarketScope.b2b.value
    return normalize_market_scope(getattr(product, "market_scope", None))


def resolve_outreach_mode(
    *,
    product: Product | None,
    requested: str | None,
) -> str:
    """
    Deriva el modo de campaña desde el producto.
    - product b2b → siempre b2b
    - product b2c → siempre b2c
    - product both → usa `requested` (debe ser b2b|b2c; default b2b)
    """
    scope = product_market_scope(product)
    if scope == MarketScope.b2c.value:
        return OutreachMode.b2c.value
    if scope == MarketScope.b2b.value:
        return OutreachMode.b2b.value
    return normalize_outreach_mode(requested)


def campaign_is_b2c(campaign) -> bool:
    return normalize_outreach_mode(getattr(campaign, "outreach_mode", None)) == OutreachMode.b2c.value


def market_scope_label(scope: str | None) -> str:
    s = normalize_market_scope(scope)
    return {"b2b": "B2B", "b2c": "B2C", "both": "B2B y B2C"}.get(s, "B2B")


def outreach_mode_label(mode: str | None) -> str:
    return "B2C" if normalize_outreach_mode(mode) == OutreachMode.b2c.value else "B2B"
