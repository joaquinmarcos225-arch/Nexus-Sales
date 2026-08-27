"""Gmail/Calendar OAuth no se desconectan si hay refresh_token recuperable."""

from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus, UserRole
from app.models.user import User
from app.services.gmail_drafts import (
    get_valid_gmail_connection,
    get_valid_google_calendar_connection,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user_with_google(db, *, gmail_status: str, cal_status: str):
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
            status=gmail_status,
            external_email="seller@gmail.com",
            access_token_encrypted="enc-access",
            refresh_token_encrypted="enc-refresh",
        )
    )
    db.add(
        ConnectedAccount(
            company_id=company.id,
            user_id=user.id,
            provider=IntegrationProvider.google_calendar.value,
            status=cal_status,
            external_email="seller@gmail.com",
            access_token_encrypted="enc-access",
            refresh_token_encrypted="enc-refresh",
        )
    )
    db.commit()
    return company, user


@patch("app.services.gmail_drafts._probe_google_token", return_value=True)
@patch("app.services.gmail_drafts.decrypt_secret", side_effect=lambda v: "token-ok" if v else None)
def test_gmail_usable_when_status_error_but_refresh_exists(_mock_decrypt, _mock_probe):
    db = _session()
    company, user = _user_with_google(
        db,
        gmail_status=IntegrationStatus.error.value,
        cal_status=IntegrationStatus.error.value,
    )
    access, row = get_valid_gmail_connection(db, company_id=company.id, user_id=user.id)
    assert access == "token-ok"
    assert row.provider == IntegrationProvider.gmail.value
    # heal a connected
    gmail = db.get(ConnectedAccount, row.id)
    assert gmail.status == IntegrationStatus.connected.value


@patch("app.services.gmail_drafts._probe_google_token", return_value=False)
@patch("app.services.gmail_drafts._refresh_access_token", return_value="token-refreshed")
@patch(
    "app.services.gmail_drafts.decrypt_secret",
    side_effect=lambda v: {
        "enc-access": "stale",
        "enc-refresh": "refresh-plain",
    }.get(v, v),
)
def test_gmail_refreshes_instead_of_staying_disconnected(
    _mock_decrypt, _mock_refresh, _mock_probe
):
    db = _session()
    company, user = _user_with_google(
        db,
        gmail_status=IntegrationStatus.error.value,
        cal_status=IntegrationStatus.error.value,
    )
    access, _row = get_valid_gmail_connection(db, company_id=company.id, user_id=user.id)
    assert access == "token-refreshed"
    _mock_refresh.assert_called_once()


@patch("app.services.gmail_drafts._probe_google_token", return_value=True)
@patch("app.services.gmail_drafts.decrypt_secret", return_value="token-ok")
def test_calendar_oauth_uses_calendar_row_when_gmail_error(_mock_decrypt, _mock_probe):
    db = _session()
    company, user = _user_with_google(
        db,
        gmail_status=IntegrationStatus.error.value,
        cal_status=IntegrationStatus.connected.value,
    )
    access, row = get_valid_google_calendar_connection(
        db,
        company_id=company.id,
        user_id=user.id,
    )
    assert access == "token-ok"
    assert row.provider == IntegrationProvider.google_calendar.value
