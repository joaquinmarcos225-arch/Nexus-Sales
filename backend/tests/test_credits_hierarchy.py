"""Créditos jerárquicos — consumo, transferencias y visibilidad."""

from app.core.permissions import normalize_role
from app.models.enums import UserRole
from app.models.user import User
from app.services.credits import (
    CreditError,
    assert_allocate_target_is_manager,
    assert_transfer_allowed,
    consume_user_credits,
    visible_allocation_user_ids,
)


def _user(uid: int, role: str, team_id: int | None = None) -> User:
    u = User(
        id=uid,
        company_id=1,
        team_id=team_id,
        first_name="T",
        last_name="U",
        name="Test",
        email=f"u{uid}@test.com",
        role=role,
        password_hash="x",
        is_active=True,
    )
    return u


def test_allocate_target_must_be_sdr_or_manager():
    director = _user(9, UserRole.gerente.value)
    try:
        assert_allocate_target_is_manager(director)
        assert False, "expected CreditError"
    except CreditError as e:
        assert "SDR" in str(e) or "Manager" in str(e)

    sdr = _user(2, UserRole.sdr.value)
    assert_allocate_target_is_manager(sdr)
    manager = _user(1, UserRole.manager.value)
    assert_allocate_target_is_manager(manager)


def test_anyone_can_transfer_to_any_eligible_peer():
    manager = _user(1, UserRole.manager.value, team_id=10)
    other_team_sdr = _user(3, UserRole.sdr.value, team_id=99)
    assert_transfer_allowed(actor=manager, from_user=manager, to_user=other_team_sdr)


def test_sdr_can_transfer_to_manager():
    sdr = _user(2, UserRole.sdr.value, team_id=10)
    manager = _user(1, UserRole.manager.value, team_id=10)
    assert_transfer_allowed(actor=sdr, from_user=sdr, to_user=manager)


def test_cannot_transfer_from_someone_else():
    manager = _user(1, UserRole.manager.value, team_id=10)
    sdr = _user(2, UserRole.sdr.value, team_id=10)
    try:
        assert_transfer_allowed(actor=manager, from_user=sdr, to_user=manager)
        assert False, "expected CreditError"
    except CreditError as e:
        assert "propio" in str(e).lower()


def test_cannot_transfer_to_self():
    manager = _user(1, UserRole.manager.value, team_id=10)
    try:
        assert_transfer_allowed(actor=manager, from_user=manager, to_user=manager)
        assert False, "expected CreditError"
    except CreditError as e:
        assert "distintos" in str(e).lower()


def test_manager_can_transfer_to_team_sdr():
    manager = _user(1, UserRole.manager.value, team_id=10)
    sdr = _user(2, UserRole.sdr.value, team_id=10)
    assert_transfer_allowed(actor=manager, from_user=manager, to_user=sdr)


def test_visible_allocations_manager_sees_all_eligible():
    manager = _user(1, UserRole.manager.value, team_id=10)
    users = [
        manager,
        _user(2, UserRole.sdr.value, team_id=10),
        _user(3, UserRole.sdr.value, team_id=99),
        _user(4, UserRole.manager.value, team_id=20),
        _user(5, UserRole.gerente.value),
    ]
    ids = visible_allocation_user_ids(manager, users)
    assert ids == {1, 2, 3, 4}


def test_consume_raises_when_insufficient():
    from unittest.mock import MagicMock

    row = MagicMock()
    row.allocated_balance = 5
    row.used_balance = 4
    session = MagicMock()
    session.scalars.return_value.first.return_value = row
    try:
        consume_user_credits(session, 1, 2, 2, reason="test")
        assert False, "expected CreditError"
    except CreditError as e:
        assert "insuficientes" in str(e).lower()
