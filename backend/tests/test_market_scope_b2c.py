"""Tests B2B/B2C market scope + ICP."""

from app.models.enums import MarketScope, OutreachMode
from app.services.campaign_icp import assert_icp_has_signal, ICP_MISSING_MESSAGE
from app.services.campaign_market import (
    normalize_market_scope,
    normalize_outreach_mode,
    resolve_outreach_mode,
)


class _Prod:
    def __init__(self, scope: str):
        self.market_scope = scope


def test_normalize_defaults():
    assert normalize_market_scope(None) == "b2b"
    assert normalize_market_scope("BOTH") == "both"
    assert normalize_outreach_mode("b2c") == "b2c"


def test_resolve_outreach_from_product():
    assert resolve_outreach_mode(product=_Prod("b2b"), requested="b2c") == "b2b"
    assert resolve_outreach_mode(product=_Prod("b2c"), requested="b2b") == "b2c"
    assert resolve_outreach_mode(product=_Prod("both"), requested="b2c") == "b2c"
    assert resolve_outreach_mode(product=_Prod("both"), requested=None) == "b2b"


def test_icp_b2c_requires_region_and_who_or_keywords():
    from app.services.campaign_icp import ICP_B2C_MISSING_MESSAGE

    try:
        assert_icp_has_signal(outreach_mode="b2c")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == ICP_B2C_MISSING_MESSAGE

    # Solo keywords o solo región → insuficiente
    try:
        assert_icp_has_signal(target_interests="running", outreach_mode="b2c")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == ICP_B2C_MISSING_MESSAGE
    try:
        assert_icp_has_signal(target_country="Argentina", outreach_mode="b2c")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == ICP_B2C_MISSING_MESSAGE

    # Idioma / situación solos no alcanzan
    try:
        assert_icp_has_signal(
            target_country="Argentina",
            target_language="Español",
            outreach_mode="b2c",
        )
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == ICP_B2C_MISSING_MESSAGE

    assert_icp_has_signal(
        target_country="Argentina",
        target_interests="propiedades",
        outreach_mode="b2c",
    )
    assert_icp_has_signal(
        target_country="LATAM - Brasil",
        target_role="Inversor particular",
        outreach_mode="b2c",
    )

def test_icp_b2b_still_requires_signal():
    try:
        assert_icp_has_signal(outreach_mode="b2b")
        assert False, "expected ValueError"
    except ValueError as e:
        assert str(e) == ICP_MISSING_MESSAGE

    assert_icp_has_signal(target_industry="SaaS", outreach_mode="b2b")


def test_enums():
    assert MarketScope.both.value == "both"
    assert OutreachMode.b2c.value == "b2c"
