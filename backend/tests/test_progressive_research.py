"""Investigación progresiva: deep research solo en primer compose."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.lead_sourcing import cogs_runtime_metrics as m
from app.services.outreach_prospect_research import (
    RESEARCH_END,
    RESEARCH_START,
    ensure_outreach_research,
    resolve_research_depth,
)


class _Prospect:
    name = "Ana"
    role = "CEO"
    company_name = "Acme"
    industry = "SaaS"
    country = "AR"
    company_website = "https://acme.com"
    linkedin_url = ""
    notes = ""
    compatibility_score = 80


class _Campaign:
    outreach_mode = "b2b"
    target_country = "Argentina"
    product_id = None


class _Product:
    name = "Nexus"


def test_resolve_research_depth_skips_followup():
    assert (
        resolve_research_depth(
            day=3,
            prior_touches=[],
            has_stored_brief=False,
        )
        == "skip"
    )
    assert (
        resolve_research_depth(
            day=1,
            prior_touches=[{"day": 1, "channel": "email"}],
            has_stored_brief=False,
        )
        == "skip"
    )


def test_resolve_research_depth_first_compose_is_light():
    assert (
        resolve_research_depth(day=1, prior_touches=[], has_stored_brief=False)
        == "light"
    )


def test_resolve_research_depth_reuses_stored_brief():
    notes = f"{RESEARCH_START}\nprevio\n{RESEARCH_END}"
    assert (
        resolve_research_depth(day=1, prior_touches=[], has_stored_brief=True)
        == "skip"
    )
    _ = notes


def test_ensure_outreach_research_light_skips_brave(monkeypatch):
    m.reset_for_tests()
    monkeypatch.setenv("NEXUS_RESEARCH_ESCALATE_BRAVE", "0")

    calls = {"brave": 0}

    def _fake_collect(*args, **kwargs):
        if kwargs.get("allow_brave"):
            calls["brave"] += 1
        return ["Acme — software — https://acme.com"]

    db = SimpleNamespace(flush=lambda: None)
    prospect = _Prospect()

    with patch(
        "app.services.outreach_prospect_research._collect_web_snippets",
        side_effect=_fake_collect,
    ):
        with patch(
            "app.services.outreach_prospect_research._synthesize_brief",
            return_value="brief light",
        ):
            brief = ensure_outreach_research(
                db,  # type: ignore[arg-type]
                prospect=prospect,  # type: ignore[arg-type]
                campaign=_Campaign(),  # type: ignore[arg-type]
                product=_Product(),  # type: ignore[arg-type]
                depth="light",
            )

    assert calls["brave"] == 0
    assert brief == "brief light"
    assert RESEARCH_START in (prospect.notes or "")


def test_ensure_outreach_research_skip_records_metric():
    m.reset_for_tests()
    db = SimpleNamespace(flush=lambda: None)
    prospect = _Prospect()
    prospect.notes = f"{RESEARCH_START}\nok\n{RESEARCH_END}"

    out = ensure_outreach_research(
        db,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        campaign=_Campaign(),  # type: ignore[arg-type]
        product=_Product(),  # type: ignore[arg-type]
        depth="skip",
    )
    assert out == "ok"
    assert m.snapshot()["research_skipped"] == 1
