"""Regla 11: no plantilla cold si ya hay toques en la secuencia."""

from app.services.cold_message_bank import resolve_bank_kind
from app.services.sequence_cold_bank_guard import (
    cold_bank_forbidden,
    is_cold_bank_template_id,
)


def test_detects_cold_template_id():
    assert is_cold_bank_template_id("b2b-linkedin-cold-08")
    assert not is_cold_bank_template_id("b2b-linkedin-fu-03")
    assert not is_cold_bank_template_id("seller_style_linkedin")


def test_forbidden_when_prior_and_cold_id():
    assert cold_bank_forbidden(
        prior_touches=[{"channel": "email", "body": "x"}],
        template_id="b2b-linkedin-cold-08",
    )
    assert not cold_bank_forbidden(
        prior_touches=[],
        template_id="b2b-linkedin-cold-01",
    )


def test_resolve_bank_kind_ignores_forced_cold_with_history():
    prior = [{"channel": "email", "body": "mail"}]
    assert resolve_bank_kind(prior_touches=prior, first_touch=True) == "fu"
    assert resolve_bank_kind(prior_touches=prior, first_touch=None) == "fu"
    assert resolve_bank_kind(prior_touches=[], first_touch=None) == "cold"
