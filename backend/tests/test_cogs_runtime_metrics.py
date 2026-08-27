"""Runtime COGS counters por módulo."""

from app.services.lead_sourcing import cogs_runtime_metrics as m


def test_cogs_metrics_module_breakdown():
    m.reset_for_tests()
    m.record_prospeo_search(2)
    m.record_enrich(enrich_mobile=True)
    m.record_enrich(enrich_mobile=False)
    m.record_web_search(backend="brave", n=3)
    m.record_web_search(backend="ddg", n=1)
    m.record_openai(input_tokens=1000, output_tokens=500)
    m.record_import(2)
    m.record_wa_sent(1)

    snap = m.snapshot()
    assert snap["prospeo_search_calls"] == 2
    assert snap["enrich_mobile_calls"] == 1
    assert snap["enrich_email_only_calls"] == 1
    assert snap["prospeo_credits_est"] == 2 + 1 + 10  # search + email + mobile*10
    assert snap["brave_queries"] == 3
    assert snap["web_search_other_queries"] == 1
    assert snap["openai_calls"] == 1
    assert snap["openai_total_tokens"] == 1500
    assert snap["imports"] == 2
    assert snap["wa_sent"] == 1
    assert snap["est_prospeo_usd"] > 0
    assert snap["est_brave_usd"] == round(3 * 0.005, 4)
    assert snap["est_openai_usd"] > 0
    assert snap["est_total_usd"] == round(
        snap["est_prospeo_usd"] + snap["est_brave_usd"] + snap["est_openai_usd"],
        4,
    )
    assert snap["est_cogs_per_import_usd"] == round(snap["est_total_usd"] / 2, 3)


def test_cogs_metrics_legacy_keys_still_present():
    m.reset_for_tests()
    m.record_enrich(enrich_mobile=True)
    m.record_import(1)
    snap = m.snapshot()
    assert "enrich_mobile_calls" in snap
    assert "mobile_per_import" in snap
    assert "est_cogs_per_import_usd" in snap
