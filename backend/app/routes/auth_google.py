"""OAuth Google: Gmail + Calendar (mismo consentimiento, dos ConnectedAccount)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.auth.deps import get_current_user
from app.deps import get_company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.models.user import User
from app.services import google_oauth
from app.services.oauth_tokens import encrypt_secret

router = APIRouter(tags=["auth-google"])


class GoogleOAuthStartRead(BaseModel):
    authorization_url: str


def _build_google_authorization_url(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    current_user: User,
) -> str:
    if int(current_user.id) != int(user_id):
        raise HTTPException(status_code=403, detail="Solo podés conectar tu propia cuenta Google")
    _user_in_company(db, company_id, user_id)
    state = google_oauth.encode_oauth_state(company_id, user_id)
    return google_oauth.build_authorization_url(state=state)


def _user_in_company(db: Session, company_id: int, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or int(user.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")
    return user


def _get_row(
    db: Session, company_id: int, user_id: int, provider: IntegrationProvider
) -> ConnectedAccount | None:
    return db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == provider.value,
        )
    ).first()


def _upsert_connected(
    db: Session,
    *,
    company_id: int,
    user_id: int,
    provider: IntegrationProvider,
    email: str | None,
    access_token: str,
    refresh_token: str | None,
) -> None:
    now = datetime.now(UTC)
    enc_access = encrypt_secret(access_token)
    row = _get_row(db, company_id, user_id, provider)
    if row is None:
        row = ConnectedAccount(
            company_id=company_id,
            user_id=user_id,
            provider=provider.value,
            status=IntegrationStatus.connected.value,
        )
        db.add(row)
    row.status = IntegrationStatus.connected.value
    row.external_email = email
    row.access_token_encrypted = enc_access
    if refresh_token:
        row.refresh_token_encrypted = encrypt_secret(refresh_token)
    elif not row.refresh_token_encrypted:
        row.refresh_token_encrypted = None
    row.connected_at = now


@router.get("/auth/google/start-url", response_model=GoogleOAuthStartRead)
def google_oauth_start_url(
    user_id: int = Query(..., ge=1),
    company_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> GoogleOAuthStartRead:
    """Devuelve la URL de consentimiento Google (requiere JWT — usar desde la app)."""
    try:
        url = _build_google_authorization_url(
            db, company_id=company_id, user_id=user_id, current_user=current_user
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return GoogleOAuthStartRead(authorization_url=url)


@router.get("/auth/google/start")
def google_oauth_start(
    user_id: int = Query(..., ge=1),
    company_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> RedirectResponse:
    """Redirect directo — requiere Authorization Bearer (no usar con window.location)."""
    try:
        url = _build_google_authorization_url(
            db, company_id=company_id, user_id=user_id, current_user=current_user
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/google/callback")
def google_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Intercambia code, guarda Gmail + Google Calendar con tokens cifrados."""
    if error:
        return RedirectResponse(
            url=google_oauth.frontend_redirect_error("google_denied", error),
            status_code=302,
        )
    if not code or not state:
        return RedirectResponse(
            url=google_oauth.frontend_redirect_error("missing_params", "Falta code o state"),
            status_code=302,
        )
    try:
        company_id, user_id = google_oauth.decode_oauth_state(state)
    except ValueError as e:
        return RedirectResponse(
            url=google_oauth.frontend_redirect_error("invalid_state", str(e)),
            status_code=302,
        )

    _user_in_company(db, company_id, user_id)

    try:
        token_payload = google_oauth.exchange_code_for_tokens(code)
    except Exception as e:
        return RedirectResponse(
            url=google_oauth.frontend_redirect_error("token_exchange", str(e)),
            status_code=302,
        )

    access_token = token_payload.get("access_token")
    if not access_token or not isinstance(access_token, str):
        return RedirectResponse(
            url=google_oauth.frontend_redirect_error("no_access_token", ""),
            status_code=302,
        )
    refresh_token = token_payload.get("refresh_token")
    if refresh_token is not None and not isinstance(refresh_token, str):
        refresh_token = None

    try:
        email = google_oauth.fetch_google_user_email(access_token)
    except Exception:
        email = None

    try:
        _upsert_connected(
            db,
            company_id=company_id,
            user_id=user_id,
            provider=IntegrationProvider.gmail,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        _upsert_connected(
            db,
            company_id=company_id,
            user_id=user_id,
            provider=IntegrationProvider.google_calendar,
            email=email,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=google_oauth.frontend_redirect_error("persist", str(e)),
            status_code=302,
        )

    return RedirectResponse(url=google_oauth.frontend_redirect_success(), status_code=302)
