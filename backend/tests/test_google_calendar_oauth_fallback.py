"""Calendar OAuth cuando Gmail quedó en error pero Calendar sigue conectado."""

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus, UserRole
from app.models.user import User
from app.services.gmail_drafts import get_valid_google_calendar_connection


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _bundle(db):
    company = Company(name="Co", employee_count=5, plan="starter")
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        email="sdr@test.com",
        first_name="SDR",
        last_name="T",
        name="SDR T",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add(user)
    db.flush()
    db.add(
        ConnectedAccount(
            company_id=company.id,
            user_id=user.id,
            provider=IntegrationProvider.gmail.value,
            status=IntegrationStatus.error.value,
            external_email="seller@gmail.com",
            access_token_encrypted="enc-gmail",
            refresh_token_encrypted="enc-refresh",
        )
    )
    db.add(
        ConnectedAccount(
            company_id=company.id,
            user_id=user.id,
            provider=IntegrationProvider.google_calendar.value,
            status=IntegrationStatus.connected.value,
            external_email="seller@gmail.com",
            access_token_encrypted="enc-cal",
            refresh_token_encrypted="enc-refresh",
        )
    )
    db.commit()
    return company, user


@patch("app.services.gmail_drafts._probe_google_token", return_value=True)
@patch("app.services.gmail_drafts.decrypt_secret", return_value="token-ok")
def test_calendar_oauth_uses_calendar_row_when_gmail_error(_mock_decrypt, _mock_probe):
    db = _session()
    company, user = _bundle(db)
    access, row = get_valid_google_calendar_connection(
        db,
        company_id=company.id,
        user_id=user.id,
    )
    assert access == "token-ok"
    assert row.provider == IntegrationProvider.google_calendar.value
