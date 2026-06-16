from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission, normalize_role, permission_codes_for_role
from app.core.security import safe_decode_access_token
from app.database.session import get_db
from app.models.company import Company
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)


def get_current_user_optional(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User | None:
    if credentials is None or not credentials.credentials:
        return None
    payload = safe_decode_access_token(credentials.credentials)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_same_company(company_id: int, user: User = Depends(get_current_user)) -> User:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    return user


def get_company_for_user(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Company:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return company


def require_permission(permission: Permission):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(normalize_role(user.role), permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permiso denegado: {permission.value}",
            )
        return user

    return _dep


RequireProductCreate = Annotated[User, Depends(require_permission(Permission.PRODUCT_CREATE))]
RequireProductEdit = Annotated[User, Depends(require_permission(Permission.PRODUCT_EDIT))]
RequireProductDelete = Annotated[User, Depends(require_permission(Permission.PRODUCT_DELETE))]
RequireUserCreate = Annotated[User, Depends(require_permission(Permission.USER_CREATE))]
RequireUserChangeRole = Annotated[User, Depends(require_permission(Permission.USER_CHANGE_ROLE))]
RequireProspectReassign = Annotated[User, Depends(require_permission(Permission.PROSPECT_REASSIGN))]


def user_session_payload(user: User) -> dict:
    role = normalize_role(user.role)
    return {
        "user_id": user.id,
        "company_id": user.company_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "name": user.name,
        "email": user.email,
        "role": role.value,
        "team_id": user.team_id,
        "is_active": user.is_active,
        "permissions": permission_codes_for_role(role),
    }
