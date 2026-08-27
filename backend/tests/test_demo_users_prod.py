from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.database.base import Base
from app.database.seed import deactivate_demo_test_users, is_demo_test_email
from app.models.company import Company
from app.models.enums import UserRole
from app.models.user import User


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_is_demo_test_email():
    assert is_demo_test_email("sdr@test.com")
    assert is_demo_test_email("Manager@test.com")
    assert not is_demo_test_email("joaquin@costguard.com.ar")


def test_deactivate_demo_test_users():
    db = _session()
    company = Company(name="CostGuard", employee_count=3, plan="custom")
    db.add(company)
    db.flush()
    db.add(
        User(
            company_id=company.id,
            first_name="SDR",
            last_name="Test",
            name="SDR Test",
            email="sdr@test.com",
            password_hash=hash_password("x"),
            role=UserRole.sdr.value,
            is_active=True,
        )
    )
    db.add(
        User(
            company_id=company.id,
            first_name="Real",
            last_name="Sdr",
            name="Real Sdr",
            email="sdr@costguard.com.ar",
            password_hash=hash_password("x"),
            role=UserRole.sdr.value,
            is_active=True,
        )
    )
    db.commit()

    n = deactivate_demo_test_users(db)
    db.commit()
    assert n == 1
    demo = db.scalars(select(User).where(User.email == "sdr@test.com")).one()
    real = db.scalars(select(User).where(User.email == "sdr@costguard.com.ar")).one()
    assert demo.is_active is False
    assert real.is_active is True
