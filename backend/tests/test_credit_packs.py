"""Tests de paquetes y solicitud de compra de créditos."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.credit_packs import get_credit_pack, list_credit_packs


def test_credit_packs_pricing():
    packs = {p["key"]: p for p in list_credit_packs()}
    assert packs["s"]["credits"] == 100
    assert packs["s"]["price_usd"] == 50.0
    assert packs["m"]["credits"] == 500
    assert packs["l"]["price_usd"] == 500.0


def test_get_credit_pack_invalid():
    assert get_credit_pack("xx") is None


@pytest.mark.parametrize("role,ok", [("gerente", True), ("manager", True), ("sdr", False)])
def test_purchase_request_actor(role, ok):
    from app.models.enums import UserRole
    from app.services.credits import CreditError, assert_purchase_request_actor

    user = MagicMock()
    user.role = role
    if ok:
        assert_purchase_request_actor(user)
    else:
        with pytest.raises(CreditError):
            assert_purchase_request_actor(user)
