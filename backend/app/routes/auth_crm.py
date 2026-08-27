"""OAuth CRM por empresa: HubSpot y Salesforce."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import require_permission
from app.core.permissions import Permission, has_permission, normalize_role
from app.database.session import get_db
from app.deps import get_company
from app.models.user import User
from app.services.crm import company_credentials as cc
from app.services.crm import hubspot_oauth, oauth_state, salesforce_oauth

router = APIRouter(tags=["auth-crm"])


class CrmOAuthStartRead(BaseModel):
    authorization_url: str


def _require_company_user(db: Session, company_id: int, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or int(user.company_id) != int(company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado en esta empresa")
    return user


def _require_crm_admin(user: User) -> None:
    if not has_permission(normalize_role(user.role), Permission.COMPANY_CONFIG):
        raise HTTPException(
            status_code=403,
            detail="Solo gerente puede conectar integraciones CRM de la empresa",
        )


@router.get("/auth/hubspot/start-url", response_model=CrmOAuthStartRead)
def hubspot_oauth_start_url(
    company_id: int = Query(..., ge=1),
    user_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> CrmOAuthStartRead:
    if int(current_user.id) != int(user_id):
        raise HTTPException(status_code=403, detail="Solo podés conectar CRM para tu sesión")
    _require_company_user(db, company_id, user_id)
    err = hubspot_oauth.oauth_configuration_error()
    if err:
        raise HTTPException(
            status_code=503,
            detail=f"HubSpot OAuth no configurado en el servidor. {err}",
        )
    state = oauth_state.encode_oauth_state(company_id, user_id, cc.PROVIDER_HUBSPOT)
    return CrmOAuthStartRead(authorization_url=hubspot_oauth.build_authorization_url(state=state))


@router.get("/auth/salesforce/start-url", response_model=CrmOAuthStartRead)
def salesforce_oauth_start_url(
    company_id: int = Query(..., ge=1),
    user_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.COMPANY_CONFIG)),
    _company=Depends(get_company),
) -> CrmOAuthStartRead:
    if int(current_user.id) != int(user_id):
        raise HTTPException(status_code=403, detail="Solo podés conectar CRM para tu sesión")
    _require_company_user(db, company_id, user_id)
    err = salesforce_oauth.oauth_configuration_error()
    if err:
        raise HTTPException(
            status_code=503,
            detail=f"Salesforce OAuth no configurado en el servidor. {err}",
        )
    state = oauth_state.encode_oauth_state(company_id, user_id, cc.PROVIDER_SALESFORCE)
    return CrmOAuthStartRead(authorization_url=salesforce_oauth.build_authorization_url(state=state))


@router.get("/auth/hubspot/callback")
def hubspot_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    provider = cc.PROVIDER_HUBSPOT
    if error:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "denied", error),
            status_code=302,
        )
    if not code or not state:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "missing_params", "Falta code o state"),
            status_code=302,
        )
    try:
        company_id, user_id, decoded_provider = oauth_state.decode_oauth_state(state)
        if decoded_provider != provider:
            raise ValueError("proveedor incorrecto")
    except ValueError as e:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "invalid_state", str(e)),
            status_code=302,
        )

    _require_company_user(db, company_id, user_id)
    actor = db.get(User, user_id)
    if actor is None:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "invalid_user", "Usuario inválido"),
            status_code=302,
        )
    try:
        _require_crm_admin(actor)
    except HTTPException as e:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "forbidden", str(e.detail)),
            status_code=302,
        )

    try:
        payload = hubspot_oauth.exchange_code_for_tokens(code)
        access = str(payload.get("access_token") or "")
        if not access:
            raise RuntimeError("Sin access_token")
        refresh = payload.get("refresh_token")
        refresh_s = str(refresh) if refresh else None
        portal_name = None
        portal_id = None
        try:
            details = hubspot_oauth.fetch_portal_details(access)
            portal_name = details.get("companyName") or details.get("portalName")
            portal_id = str(details.get("portalId") or "") or None
        except Exception:
            pass
        cc.upsert_company_integration(
            db,
            company_id=company_id,
            provider=provider,
            connected_by_user_id=user_id,
            access_token=access,
            refresh_token=refresh_s,
            expires_in=int(payload.get("expires_in") or 0) or None,
            external_label=portal_name,
            external_id=portal_id,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "token_exchange", str(e)),
            status_code=302,
        )

    from app.services.crm import exclusions as crm_exclusions

    crm_exclusions.sync_exclusions_best_effort(db, company_id, provider=provider)

    return RedirectResponse(oauth_state.frontend_redirect_success(provider), status_code=302)


@router.get("/auth/salesforce/callback")
def salesforce_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    provider = cc.PROVIDER_SALESFORCE
    if error:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "denied", error),
            status_code=302,
        )
    if not code or not state:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "missing_params", "Falta code o state"),
            status_code=302,
        )
    try:
        company_id, user_id, decoded_provider = oauth_state.decode_oauth_state(state)
        if decoded_provider != provider:
            raise ValueError("proveedor incorrecto")
    except ValueError as e:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "invalid_state", str(e)),
            status_code=302,
        )

    _require_company_user(db, company_id, user_id)
    actor = db.get(User, user_id)
    if actor is None:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "invalid_user", "Usuario inválido"),
            status_code=302,
        )
    try:
        _require_crm_admin(actor)
    except HTTPException as e:
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "forbidden", str(e.detail)),
            status_code=302,
        )

    try:
        payload = salesforce_oauth.exchange_code_for_tokens(code)
        access = str(payload.get("access_token") or "")
        instance_url = str(payload.get("instance_url") or "").strip().rstrip("/")
        if not access or not instance_url:
            raise RuntimeError("Respuesta Salesforce incompleta")
        refresh = payload.get("refresh_token")
        refresh_s = str(refresh) if refresh else None
        org_name = None
        cc.upsert_company_integration(
            db,
            company_id=company_id,
            provider=provider,
            connected_by_user_id=user_id,
            access_token=access,
            refresh_token=refresh_s,
            expires_in=int(payload.get("expires_in") or 0) or None,
            external_label=org_name,
            external_id=instance_url,
            metadata={"instance_url": instance_url},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            oauth_state.frontend_redirect_error(provider, "token_exchange", str(e)),
            status_code=302,
        )

    from app.services.crm import exclusions as crm_exclusions

    crm_exclusions.sync_exclusions_best_effort(db, company_id, provider=provider)

    return RedirectResponse(oauth_state.frontend_redirect_success(provider), status_code=302)
