"""Olvidé contraseña: solo usuarios existentes + código."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password, verify_password
from app.database.base import Base
from app.models.company import Company
from app.models.enums import UserRole
from app.models.password_reset import PasswordResetCode
from app.models.user import User
from app.services.password_reset import (
    confirm_password_reset,
    request_password_reset,
    verify_password_reset_code,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(db, email="sdr@acme.com"):
    company = Company(name="Acme", plan="starter", employee_count=2)
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        email=email,
        first_name="Ana",
        last_name="Sdr",
        name="Ana Sdr",
        role=UserRole.sdr.value,
        password_hash=hash_password("vieja1234"),
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def test_unknown_email_is_rejected(monkeypatch):
    monkeypatch.delenv("NEXUS_REAL_MODE", raising=False)
    db = _session()
    _user(db)
    try:
        request_password_reset(db, "nadie@acme.com")
        assert False, "expected 404"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404


def test_reset_code_then_new_password(monkeypatch):
    monkeypatch.delenv("NEXUS_REAL_MODE", raising=False)
    monkeypatch.setenv("NEXUS_PASSWORD_RESET_DEV_ECHO", "1")
    db = _session()
    user = _user(db)
    out = request_password_reset(db, "SDR@acme.com")
    assert out["ok"] is True
    code = out.get("dev_code")
    assert code and len(code) == 6

    verify_password_reset_code(db, user.email, code)
    confirm_password_reset(
        db,
        email=user.email,
        code=code,
        password="nueva1234",
        password_confirm="nueva1234",
    )
    db.refresh(user)
    assert verify_password("nueva1234", user.password_hash)
    assert not verify_password("vieja1234", user.password_hash)

    used = db.scalars(select(PasswordResetCode).where(PasswordResetCode.user_id == user.id)).all()
    assert used and used[0].used_at is not None


def test_wrong_code_rejected(monkeypatch):
    monkeypatch.delenv("NEXUS_REAL_MODE", raising=False)
    monkeypatch.setenv("NEXUS_PASSWORD_RESET_DEV_ECHO", "1")
    db = _session()
    _user(db)
    request_password_reset(db, "sdr@acme.com")
    try:
        verify_password_reset_code(db, "sdr@acme.com", "000000")
        assert False, "expected 400"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
