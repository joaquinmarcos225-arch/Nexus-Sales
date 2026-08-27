"""Wallet pool — sin saldo sin asignar negativo."""

from unittest.mock import MagicMock

from app.services.credits import get_wallet_totals, reconcile_wallet_pool


def test_reconcile_wallet_when_over_allocated():
    wallet = MagicMock()
    wallet.total_balance = 500
    row1 = MagicMock()
    row1.allocated_balance = 400
    session = MagicMock()
    session.scalars.return_value.one_or_none.return_value = wallet
    session.scalar.return_value = 600

    assert reconcile_wallet_pool(session, 1) is True
    assert wallet.total_balance == 600


def test_get_wallet_totals_never_negative_unassigned():
    wallet = MagicMock()
    wallet.total_balance = 600
    session = MagicMock()
    session.get.return_value = MagicMock(plan="starter")
    session.scalars.return_value.one_or_none.return_value = wallet
    session.scalar.return_value = 600

    _, total, assigned, unassigned = get_wallet_totals(session, 1)

    assert total == 600
    assert assigned == 600
    assert unassigned == 0
