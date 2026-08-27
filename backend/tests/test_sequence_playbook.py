"""Contrato de secuencia unificada (playbook = ejecución = UI)."""

from app.core.sequence_playbook import (
    ALL_MILESTONE_DAYS,
    LEGACY_MILESTONE_DAY_MAP,
    PLAYBOOK_DAYS,
    PLAYBOOK_EMAIL_DAYS,
    PLAYBOOK_LAST_TOUCH_DAY,
    PLAYBOOK_LINKEDIN_DAYS,
    PLAYBOOK_WHATSAPP_DAYS,
    REACTIVATION_DAY,
    normalize_fired_milestones,
    normalize_milestone_day,
    playbook_step_for_day,
    resolve_touch_channel,
    sequence_playbook_public,
)
from app.services.lead_sourcing.mvp_outreach_playbook import DEFAULT_MVP_PLAYBOOK


def test_playbook_days_match_mvp_definition():
    assert PLAYBOOK_DAYS == (1, 4, 7, 10, 13, 16, 19)
    assert len(DEFAULT_MVP_PLAYBOOK) == 7


def test_channel_days_derived_from_playbook():
    assert PLAYBOOK_EMAIL_DAYS == frozenset({1, 10, 19})
    assert PLAYBOOK_LINKEDIN_DAYS == frozenset({4, 13})
    assert PLAYBOOK_WHATSAPP_DAYS == frozenset({7, 16})


def test_all_milestones_include_reactivation():
    assert ALL_MILESTONE_DAYS == (*PLAYBOOK_DAYS, REACTIVATION_DAY)
    assert PLAYBOOK_LAST_TOUCH_DAY == 19


def test_legacy_milestone_normalization():
    assert normalize_milestone_day(14) == 13
    assert normalize_milestone_day(18) == 16
    assert normalize_milestone_day(21) == 19
    assert normalize_milestone_day(7) == 7
    assert normalize_fired_milestones([1, 4, 14, 21]) == [1, 4, 13, 19]


def test_resolve_touch_channel_playbook_primary():
    ch = resolve_touch_channel(
        4,
        email="a@acme.com",
        linkedin_url="https://linkedin.com/in/jane",
        phone=None,
        whatsapp_number=None,
    )
    assert ch == "linkedin"


def test_resolve_touch_channel_fallback_email_when_no_linkedin():
    ch = resolve_touch_channel(
        4,
        email="a@acme.com",
        linkedin_url=None,
        phone=None,
        whatsapp_number=None,
    )
    assert ch == "email"


def test_public_payload_matches_frontend_contract():
    pub = sequence_playbook_public()
    assert pub["touch_days"] == [1, 4, 7, 10, 13, 16, 19]
    assert pub["reactivation_day"] == 42
    assert pub["legacy_day_map"] == LEGACY_MILESTONE_DAY_MAP
    assert len(pub["steps"]) == 7
    for step in pub["steps"]:
        assert playbook_step_for_day(step["day"]) is not None
        assert step["channel"] == playbook_step_for_day(step["day"]).channel
