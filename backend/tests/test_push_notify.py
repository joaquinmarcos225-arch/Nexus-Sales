"""Web Push subscriptions (sin enviar a la red)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.push_subscription import PushSubscription  # noqa: F401
from app.models.user import User
from app.services.push_notify import delete_subscription, upsert_subscription


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_upsert_and_delete_push_subscription():
    db = _session()
    company = Company(name="Co", plan="starter", employee_count=1)
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        first_name="Ana",
        last_name="",
        name="Ana",
        email="ana@cliente.com",
        role="sdr",
    )
    db.add(user)
    db.flush()

    row = upsert_subscription(
        db,
        user=user,
        app="sales",
        endpoint="https://push.example/abc",
        p256dh="p256",
        auth="authk",
    )
    assert row.id
    again = upsert_subscription(
        db,
        user=user,
        app="support",
        endpoint="https://push.example/abc",
        p256dh="p256b",
        auth="auth2",
    )
    assert again.id == row.id
    assert again.app == "support"
    assert delete_subscription(db, endpoint="https://push.example/abc") is True
    assert delete_subscription(db, endpoint="https://push.example/abc") is False
