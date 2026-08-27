from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, user_session_payload
from app.core.permissions import normalize_role, permission_codes_for_role
from app.core.security import create_access_token, verify_password
from app.database.session import get_db
from app.models.campaign import Campaign
from app.models.user import User
from app.services import user_avatar as avatar_svc
from app.services.password_reset import (
    confirm_password_reset,
    request_password_reset,
    verify_password_reset_code,
)
from app.services.support import is_nexus_support_ops

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    first_name: str = Field(min_length=1, max_length=128)


class SupportLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    password: str = Field(min_length=8, max_length=256)
    password_confirm: str = Field(min_length=8, max_length=256)


def _apply_login_display_name(db: Session, user: User, first_name: str) -> None:
    from app.services.outreach_display_names import is_placeholder_token

    first = first_name.strip()
    if not first or is_placeholder_token(first):
        return
    user.first_name = first
    last = (user.last_name or "").strip()
    # Seed deja last_name="Test" — no lo arrastramos a la firma.
    if is_placeholder_token(last):
        last = ""
        user.last_name = ""
    user.name = f"{first} {last}".strip() if last else first
    for campaign in db.scalars(select(Campaign).where(Campaign.seller_id == user.id)):
        campaign.sender_name = first


class AuthUserRead(BaseModel):
    user_id: int
    company_id: int
    company_name: str = ""
    first_name: str
    last_name: str = ""
    name: str
    email: str
    role: str
    is_active: bool
    permissions: list[str]
    is_support_ops: bool = False
    avatar_url: str | None = None
    access_token: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserRead


def _authenticate_user(db: Session, email: str, password: str) -> User:
    user = db.scalars(select(User).where(User.email == email)).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email o contraseña incorrectos")
    if not user.password_hash or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email o contraseña incorrectos")
    return user


def _login_response(user: User, db: Session | None = None) -> LoginResponse:
    role = normalize_role(user.role)
    token = create_access_token(user_id=user.id, company_id=user.company_id, role=role.value)
    return LoginResponse(
        access_token=token,
        user=AuthUserRead.model_validate(user_session_payload(user, db=db)),
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    email = str(payload.email).strip().lower()
    user = _authenticate_user(db, email, payload.password)
    _apply_login_display_name(db, user, payload.first_name)
    db.commit()
    db.refresh(user)
    return _login_response(user, db=db)


@router.post("/support-login", response_model=LoginResponse)
def support_login(payload: SupportLoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """Login de la app Nexus Support (no muta nombre/firma de Nexus Sales)."""
    email = str(payload.email).strip().lower()
    user = _authenticate_user(db, email, payload.password)
    if not is_nexus_support_ops(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta cuenta no pertenece al equipo de Nexus Support.",
        )
    return _login_response(user, db=db)


@router.post("/password-reset/request")
def password_reset_request(payload: PasswordResetRequest, db: Session = Depends(get_db)) -> dict:
    return request_password_reset(db, str(payload.email))


@router.post("/password-reset/verify")
def password_reset_verify(payload: PasswordResetVerify, db: Session = Depends(get_db)) -> dict:
    return verify_password_reset_code(db, str(payload.email), payload.code)


@router.post("/password-reset/confirm")
def password_reset_confirm(payload: PasswordResetConfirm, db: Session = Depends(get_db)) -> dict:
    return confirm_password_reset(
        db,
        email=str(payload.email),
        code=payload.code,
        password=payload.password,
        password_confirm=payload.password_confirm,
    )


@router.get("/me", response_model=AuthUserRead)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AuthUserRead:
    role = normalize_role(user.role)
    payload = user_session_payload(user, db=db)
    payload["access_token"] = create_access_token(
        user_id=user.id, company_id=user.company_id, role=role.value
    )
    return AuthUserRead.model_validate(payload)


@router.post("/me/avatar", response_model=AuthUserRead)
async def upload_my_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthUserRead:
    """Foto de perfil interna (equipo / UI). No afecta mensajes de outreach."""
    data = await file.read()
    try:
        key = avatar_svc.save_user_avatar(
            user_id=user.id,
            data=data,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user.avatar_key and user.avatar_key != key:
        avatar_svc.delete_user_avatar_files(user.avatar_key)
    user.avatar_key = key
    db.commit()
    db.refresh(user)
    return AuthUserRead.model_validate(user_session_payload(user, db=db))


@router.delete("/me/avatar", response_model=AuthUserRead)
def delete_my_avatar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuthUserRead:
    avatar_svc.delete_user_avatar_files(user.avatar_key)
    user.avatar_key = None
    db.commit()
    db.refresh(user)
    return AuthUserRead.model_validate(user_session_payload(user, db=db))


@router.get("/permissions")
def list_permissions(user: User = Depends(get_current_user)) -> dict:
    role = normalize_role(user.role)
    return {
        "role": role.value,
        "permissions": permission_codes_for_role(role),
    }
