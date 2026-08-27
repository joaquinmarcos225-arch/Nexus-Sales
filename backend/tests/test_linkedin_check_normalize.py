"""Un solo checking activo por empresa (cola FIFO)."""

from unittest.mock import MagicMock

from app.models.prospect import Prospect
from app.services import linkedin_assisted_service as las


def _p(**kw) -> Prospect:
    base = dict(
        id=1,
        company_id=9,
        campaign_id=1,
        name="A",
        linkedin_url="https://www.linkedin.com/in/a/",
        status="imported",
        linkedin_connection_status="checking",
    )
    base.update(kw)
    return Prospect(**base)


def test_normalize_demotes_extra_checking_to_queued():
    a = _p(id=48, name="Luis")
    b = _p(id=49, name="Marco")
    c = _p(id=50, name="Maria")
    db = MagicMock()
    # Simula order_by id desc → más nuevo primero.
    db.scalars.return_value.all.return_value = [c, b, a]
    # count checking: after demote only 1
    db.scalar.return_value = 1

    changed = las.normalize_company_connection_checks(db, 9)
    assert changed is True
    assert c.linkedin_connection_status == "checking"
    assert b.linkedin_connection_status == "check_queued"
    assert a.linkedin_connection_status == "check_queued"
