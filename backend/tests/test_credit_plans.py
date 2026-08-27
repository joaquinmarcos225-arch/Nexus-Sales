from app.services.credit_plans import (
    CONTACT_PLANS,
    credits_for_plan,
    custom_tool_costs_for_credits,
    normalize_plan_key,
    plan_economics_dict,
    plan_definition,
)


def test_plan_quotas_and_prices():
    assert credits_for_plan("starter") == 600
    assert credits_for_plan("growth") == 1_000
    assert credits_for_plan("scaler") == 1_400
    assert credits_for_plan("elite") == 1_800
    assert credits_for_plan("custom") == 0
    assert plan_definition("starter").price_usd == 300
    assert plan_definition("elite").price_usd == 900
    assert plan_definition("starter").tools_cogs_usd == 180.0
    assert plan_definition("starter").margin_usd == 120.0
    assert abs(plan_definition("starter").sale_per_credit_usd - 0.5) < 1e-9


def test_aliases_and_default():
    assert normalize_plan_key("pro") == "scaler"
    assert normalize_plan_key("enterprise") == "elite"
    assert credits_for_plan(None) == 600


def test_custom_economics():
    tools = custom_tool_costs_for_credits(1000)
    assert tools["openai_usd"] + tools["prospeo_usd"] + tools["brave_usd"] == 300.0
    eco = plan_economics_dict(CONTACT_PLANS["custom"], custom_credits=1000)
    assert eco["price_usd"] == 500.0
    assert eco["tools_cogs_usd"] == 300.0
    assert eco["margin_usd"] == 200.0


def test_labels():
    assert CONTACT_PLANS["elite"].label == "Elite"
    assert CONTACT_PLANS["scaler"].label == "Scaler"
    assert CONTACT_PLANS["custom"].label == "Customized"
