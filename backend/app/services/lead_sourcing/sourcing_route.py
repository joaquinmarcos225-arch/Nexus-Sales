"""Enrutado de prospección: empresa→rol vs rol-first (sin industria)."""

from __future__ import annotations

from app.services import campaign_icp as icp


def campaign_has_industry_icp(campaign) -> bool:
    return not icp.is_icp_token_empty(getattr(campaign, "target_industry", None))


def campaign_has_role_icp(campaign) -> bool:
    return not icp.is_icp_token_empty(getattr(campaign, "target_role", None))


def campaign_uses_role_first_sourcing(campaign) -> bool:
    """
    B2B sin industria + con rol → buscar personas por rol (Prospeo person-first).
    Con industria → empresa primero (Brave) y luego rol en la empresa.
    B2C sigue su propio camino (no usar este flag).
    """
    from app.services.campaign_market import campaign_is_b2c

    if campaign_is_b2c(campaign):
        return False
    if campaign_has_industry_icp(campaign):
        return False
    return campaign_has_role_icp(campaign)
