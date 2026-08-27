"""Créditos comprometidos al crear campaña — 1 prospección = 1 crédito."""

from unittest.mock import MagicMock

from app.services.credits import (
    CreditError,
    adjust_campaign_prospection_credits,
    release_user_credits,
    reserve_campaign_prospection_credits,
)


def _allocation(allocated: int, used: int) -> MagicMock:
    row = MagicMock()
    row.allocated_balance = allocated
    row.used_balance = used
    return row


def test_reserve_campaign_prospection_credits():
    row = _allocation(100, 10)
    session = MagicMock()
    session.scalars.return_value.first.return_value = row

    reserve_campaign_prospection_credits(session, 1, 2, 25, campaign_name="LATAM Q1")

    assert row.used_balance == 35


def test_adjust_campaign_increase_consumes_delta():
    row = _allocation(100, 50)
    session = MagicMock()
    session.scalars.return_value.first.return_value = row

    adjust_campaign_prospection_credits(session, 1, 2, 50, 70, campaign_name="LATAM Q1")

    assert row.used_balance == 70


def test_adjust_campaign_decrease_releases_delta():
    row = _allocation(100, 50)
    session = MagicMock()
    session.scalars.return_value.first.return_value = row

    adjust_campaign_prospection_credits(session, 1, 2, 50, 30, campaign_name="LATAM Q1")

    assert row.used_balance == 30


def test_release_user_credits_never_below_zero():
    row = _allocation(100, 5)
    session = MagicMock()
    session.scalars.return_value.first.return_value = row

    release_user_credits(session, 1, 2, 20, reason="test")

    assert row.used_balance == 0


def test_reserve_raises_when_insufficient():
    row = _allocation(10, 9)
    session = MagicMock()
    session.scalars.return_value.first.return_value = row

    try:
        reserve_campaign_prospection_credits(session, 1, 2, 5, campaign_name="X")
        assert False, "expected CreditError"
    except CreditError as e:
        assert "insuficientes" in str(e).lower()
