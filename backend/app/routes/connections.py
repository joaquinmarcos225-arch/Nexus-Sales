"""Conexiones por usuario (Fase 1: mock, sin OAuth real)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.auth.deps import get_current_user
from app.deps import get_company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.models.user import User
from app.schemas.connection import ConnectionCardRead, GoogleIntegrationVerifyRead, WhatsAppIntegrationVerifyRead
from app.schemas.linkedin_connect import LinkedInMockConnectBody
from app.services import google_oauth
from app.services.google_integration_verify import verify_google_integrations
from app.services.oauth_tokens import decrypt_secret
from app.services.whatsapp_cloud_service import verify_whatsapp_api

_GOOGLE_PROVIDERS = (
    IntegrationProvider.gmail,
    IntegrationProvider.google_calendar,
)

router = APIRouter(tags=["connections"])

_PROVIDER_ORDER: tuple[IntegrationProvider, ...] = (
    IntegrationProvider.gmail,
    IntegrationProvider.google_calendar,
    IntegrationProvider.whatsapp,
    IntegrationProvider.linkedin,
)


def _user_in_company(db: Session, company_id: int, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or int(user.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")
    return user


def _ensure_own_integrations(current_user: User, user_id: int) -> None:
    if int(current_user.id) != int(user_id):
        raise HTTPException(
            status_code=403,
            detail="Solo podés gestionar tus propias integraciones",
        )


def _parse_provider(raw: str) -> IntegrationProvider:
    try:
        return IntegrationProvider(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Proveedor inválido") from None


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


def _merge_cards(rows: list[ConnectedAccount]) -> list[ConnectionCardRead]:
    by_p = {r.provider: r for r in rows}
    out: list[ConnectionCardRead] = []
    for p in _PROVIDER_ORDER:
        row = by_p.get(p.value)
        if row is None:
            out.append(
                ConnectionCardRead(
                    provider=p,
                    status=IntegrationStatus.not_connected,
                    external_email=None,
                    connected_at=None,
                    updated_at=None,
                )
            )
        else:
            try:
                st = IntegrationStatus(row.status)
            except ValueError:
                st = IntegrationStatus.error
            # Si hay tokens (aunque status=error), la card no debe verse como caída:
            # el refresh rehabilita la conexión sin intervención del usuario.
            if st == IntegrationStatus.error:
                has_tokens = bool(row.access_token_encrypted) or bool(
                    row.refresh_token_encrypted
                )
                if has_tokens:
                    st = IntegrationStatus.connected
            out.append(
                ConnectionCardRead(
                    provider=p,
                    status=st,
                    external_email=row.external_email,
                    connected_at=row.connected_at,
                    updated_at=row.updated_at,
                )
            )
    return out


def _fetch_merged_cards(db: Session, company_id: int, user_id: int) -> list[ConnectionCardRead]:
    _user_in_company(db, company_id, user_id)
    rows = db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
        )
    ).all()
    return _merge_cards(list(rows))


@router.get(
    "/companies/{company_id}/users/{user_id}/connections",
    response_model=list[ConnectionCardRead],
)
def list_user_connections(
    company_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> list[ConnectionCardRead]:
    _ensure_own_integrations(current_user, user_id)
    return _fetch_merged_cards(db, company_id, user_id)


@router.get(
    "/users/{user_id}/integrations/google/verify",
    response_model=GoogleIntegrationVerifyRead,
)
def verify_google_integrations_by_query(
    user_id: int,
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    deep: bool = Query(True, description="Verificar freebusy y creación de evento de prueba"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> GoogleIntegrationVerifyRead:
    """Comprueba token vivo y permisos Gmail / Calendar para la UI de integraciones."""
    _ensure_own_integrations(current_user, user_id)
    _user_in_company(db, company_id, user_id)
    data = verify_google_integrations(db, company_id=company_id, user_id=user_id, deep=deep)
    return GoogleIntegrationVerifyRead.model_validate(data)


@router.get(
    "/users/{user_id}/integrations/whatsapp/verify",
    response_model=WhatsAppIntegrationVerifyRead,
)
def verify_whatsapp_integrations_by_query(
    user_id: int,
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    deep: bool = Query(True, description="Verificar token contra Graph API"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> WhatsAppIntegrationVerifyRead:
    _ensure_own_integrations(current_user, user_id)
    _user_in_company(db, company_id, user_id)
    data = verify_whatsapp_api(deep=deep)
    return WhatsAppIntegrationVerifyRead.model_validate(data)


@router.get("/users/{user_id}/connections", response_model=list[ConnectionCardRead])
def list_user_connections_by_query(
    user_id: int,
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> list[ConnectionCardRead]:
    """GET /users/{user_id}/connections?company_id=…"""
    _ensure_own_integrations(current_user, user_id)
    return _fetch_merged_cards(db, company_id, user_id)


@router.post(
    "/companies/{company_id}/users/{user_id}/connections/{provider}/mock-connect",
    response_model=list[ConnectionCardRead],
)
def mock_connect_provider(
    company_id: int,
    user_id: int,
    provider: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
    body: LinkedInMockConnectBody | None = Body(default=None),
) -> list[ConnectionCardRead]:
    _ensure_own_integrations(current_user, user_id)
    user = _user_in_company(db, company_id, user_id)
    prov = _parse_provider(provider)
    if prov in (IntegrationProvider.gmail, IntegrationProvider.google_calendar):
        raise HTTPException(
            status_code=400,
            detail="Gmail y Google Calendar se conectan con OAuth: usá «Conectar Google» en la app.",
        )
    now = datetime.now(UTC)

    row = _get_row(db, company_id, user_id, prov)
    if row is None:
        row = ConnectedAccount(
            company_id=company_id,
            user_id=user_id,
            provider=prov.value,
            status=IntegrationStatus.not_connected.value,
        )
        db.add(row)

    if prov == IntegrationProvider.linkedin:
        profile = None
        if body is not None and (body.linkedin_profile_url or "").strip():
            profile = body.linkedin_profile_url.strip()[:512]
        display = None
        if body is not None and (body.display_name or "").strip():
            display = body.display_name.strip()[:120]
        row.status = IntegrationStatus.extension_connected.value
        row.connected_at = now
        if display and profile:
            row.external_email = f"{display} · {profile}"
        elif profile:
            row.external_email = profile
        elif display:
            row.external_email = display
        else:
            row.external_email = user.email
    else:
        row.status = IntegrationStatus.connected.value
        row.connected_at = now
        row.external_email = user.email
        row.access_token_encrypted = None
        row.refresh_token_encrypted = None

    db.commit()
    return _fetch_merged_cards(db, company_id, user_id)


@router.post(
    "/users/{user_id}/connections/{provider}/mock-connect",
    response_model=list[ConnectionCardRead],
)
def mock_connect_provider_by_query(
    user_id: int,
    provider: str,
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
    body: LinkedInMockConnectBody | None = Body(default=None),
) -> list[ConnectionCardRead]:
    """POST /users/{user_id}/connections/{provider}/mock-connect?company_id=…"""
    return mock_connect_provider(
        company_id, user_id, provider, db, current_user, _company, body
    )


def _disconnect_google_pair(db: Session, *, company_id: int, user_id: int) -> None:
    rows = [
        row
        for row in (
            _get_row(db, company_id, user_id, IntegrationProvider.gmail),
            _get_row(db, company_id, user_id, IntegrationProvider.google_calendar),
        )
        if row is not None
    ]
    token = None
    for row in rows:
        try:
            token = decrypt_secret(row.refresh_token_encrypted) or decrypt_secret(
                row.access_token_encrypted
            )
        except Exception:
            token = None
        if token:
            break
    for row in rows:
        db.delete(row)
    db.commit()
    google_oauth.revoke_google_token(token)


@router.post(
    "/companies/{company_id}/users/{user_id}/connections/{provider}/disconnect",
    response_model=list[ConnectionCardRead],
)
def disconnect_provider(
    company_id: int,
    user_id: int,
    provider: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> list[ConnectionCardRead]:
    _ensure_own_integrations(current_user, user_id)
    _user_in_company(db, company_id, user_id)
    prov = _parse_provider(provider)
    if prov in _GOOGLE_PROVIDERS:
        _disconnect_google_pair(db, company_id=company_id, user_id=user_id)
        return _fetch_merged_cards(db, company_id, user_id)
    row = _get_row(db, company_id, user_id, prov)
    if row is not None:
        db.delete(row)
        db.commit()
    return _fetch_merged_cards(db, company_id, user_id)


@router.post(
    "/users/{user_id}/connections/{provider}/disconnect",
    response_model=list[ConnectionCardRead],
)
def disconnect_provider_by_query(
    user_id: int,
    provider: str,
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> list[ConnectionCardRead]:
    """POST /users/{user_id}/connections/{provider}/disconnect?company_id=…"""
    return disconnect_provider(company_id, user_id, provider, db, current_user, _company)


def _mock_error_impl(
    company_id: int,
    user_id: int,
    provider: str,
    db: Session,
    _company,
    current_user: User,
) -> list[ConnectionCardRead]:
    """Fase 1: simula fallo de OAuth/conector sin API keys."""
    _ensure_own_integrations(current_user, user_id)
    _user_in_company(db, company_id, user_id)
    prov = _parse_provider(provider)
    row = _get_row(db, company_id, user_id, prov)
    if row is None:
        row = ConnectedAccount(
            company_id=company_id,
            user_id=user_id,
            provider=prov.value,
            status=IntegrationStatus.error.value,
        )
        db.add(row)
    else:
        row.status = IntegrationStatus.error.value
        row.connected_at = None
        row.external_email = None
        row.access_token_encrypted = None
        row.refresh_token_encrypted = None
    db.commit()
    return _fetch_merged_cards(db, company_id, user_id)


@router.post(
    "/companies/{company_id}/users/{user_id}/connections/{provider}/mock-error",
    response_model=list[ConnectionCardRead],
)
def mock_error_provider(
    company_id: int,
    user_id: int,
    provider: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> list[ConnectionCardRead]:
    return _mock_error_impl(company_id, user_id, provider, db, _company, current_user)


@router.post(
    "/users/{user_id}/connections/{provider}/mock-error",
    response_model=list[ConnectionCardRead],
)
def mock_error_provider_by_query(
    user_id: int,
    provider: str,
    company_id: int = Query(..., ge=1, description="ID de empresa (multi-tenant)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> list[ConnectionCardRead]:
    """POST /users/{user_id}/connections/{provider}/mock-error?company_id=…"""
    return _mock_error_impl(company_id, user_id, provider, db, _company, current_user)
