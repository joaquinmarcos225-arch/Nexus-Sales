from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, user_session_payload
from app.core.permissions import normalize_role, permission_codes_for_role
from app.core.security import create_access_token, verify_password
from app.database.session import get_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AuthUserRead(BaseModel):
    user_id: int
    company_id: int
    first_name: str
    last_name: str
    name: str
    email: str
    role: str
    is_active: bool
    permissions: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserRead


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    email = str(payload.email).strip().lower()
    user = db.scalars(select(User).where(User.email == email)).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email o contraseña incorrectos")
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email o contraseña incorrectos")
    role = normalize_role(user.role)
    token = create_access_token(user_id=user.id, company_id=user.company_id, role=role.value)
    session = user_session_payload(user)
    return LoginResponse(
        access_token=token,
        user=AuthUserRead.model_validate(session),
    )


@router.get("/me", response_model=AuthUserRead)
def me(user: User = Depends(get_current_user)) -> AuthUserRead:
    return AuthUserRead.model_validate(user_session_payload(user))


@router.get("/permissions")
def list_permissions(user: User = Depends(get_current_user)) -> dict:
    role = normalize_role(user.role)
    return {
        "role": role.value,
        "permissions": permission_codes_for_role(role),
    }
