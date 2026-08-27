"""Recuperar contraseña: solo usuarios existentes, código por email."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import JWT_SECRET, hash_password
from app.models.password_reset import PasswordResetCode
from app.models.user import User
from app.services import outreach_metrics as om
from app.services.system_email import send_password_reset_code, smtp_configured

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 15
RESEND_COOLDOWN_SEC = 60
MAX_ATTEMPTS = 8
MIN_PASSWORD_LEN = 8


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_code(code: str) -> str:
    return hmac.new(JWT_SECRET.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()


def _verify_code(code: str, hashed: str) -> bool:
    try:
        return hmac.compare_digest(_hash_code((code or "").strip()), hashed or "")
    except Exception:
        return False


def _dev_echo_allowed() -> bool:
    if smtp_configured():
        return False
    if (os.getenv("NEXUS_PASSWORD_RESET_DEV_ECHO") or "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return not om.is_real_mode()


def _find_active_user(db: Session, email: str) -> User | None:
    email_n = (email or "").strip().lower()
    if not email_n or "@" not in email_n:
        return None
    user = db.scalars(select(User).where(func.lower(User.email) == email_n)).first()
    if user is None or not user.is_active:
        return None
    return user


def request_password_reset(db: Session, email: str) -> dict:
    user = _find_active_user(db, email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay una cuenta activa con ese email.",
        )

    now = _now()
    recent = db.scalars(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
            PasswordResetCode.created_at >= now - timedelta(seconds=RESEND_COOLDOWN_SEC),
        )
        .order_by(PasswordResetCode.created_at.desc())
    ).first()
    if recent is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Esperá un minuto antes de pedir otro código.",
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    sent = send_password_reset_code(
        to=str(user.email),
        code=code,
        name=(user.first_name or user.name or "").strip() or None,
    )
    echo = _dev_echo_allowed()
    if not sent and not echo:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No pudimos enviar el código. Falta configurar el correo de Nexus "
                "(NEXUS_SMTP_HOST / USER / PASSWORD)."
            ),
        )

    for old in db.scalars(
        select(PasswordResetCode).where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
        )
    ).all():
        old.used_at = now

    db.add(
        PasswordResetCode(
            user_id=user.id,
            email=str(user.email).strip().lower(),
            code_hash=_hash_code(code),
            attempts=0,
            expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
        )
    )
    db.commit()

    if not sent and echo:
        logger.warning("[password-reset] SMTP off — código de dev para %s", user.email)

    out: dict = {"ok": True, "email": str(user.email)}
    if not sent and echo:
        out["dev_code"] = code
    return out


def _load_valid_code(db: Session, email: str, code: str) -> tuple[PasswordResetCode, User]:
    user = _find_active_user(db, email)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No hay una cuenta activa con ese email.",
        )
    now = _now()
    row = db.scalars(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
            PasswordResetCode.expires_at >= now,
        )
        .order_by(PasswordResetCode.created_at.desc())
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El código venció o no es válido. Pedí uno nuevo.",
        )
    if int(row.attempts or 0) >= MAX_ATTEMPTS:
        row.used_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Demasiados intentos. Pedí un código nuevo.",
        )
    if not _verify_code(code, row.code_hash):
        row.attempts = int(row.attempts or 0) + 1
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El código no es correcto.",
        )
    return row, user


def verify_password_reset_code(db: Session, email: str, code: str) -> dict:
    _load_valid_code(db, email, code)
    return {"ok": True}


def confirm_password_reset(
    db: Session,
    *,
    email: str,
    code: str,
    password: str,
    password_confirm: str,
) -> dict:
    if (password or "") != (password_confirm or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Las contraseñas no coinciden.",
        )
    if len(password or "") < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres.",
        )
    row, user = _load_valid_code(db, email, code)
    user.password_hash = hash_password(password)
    row.used_at = _now()
    db.commit()
    return {"ok": True}
