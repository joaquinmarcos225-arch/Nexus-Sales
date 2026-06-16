"""Herramientas temporales de desarrollo — reinicio de datos de testing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.core.permissions import normalize_role
from app.database.session import get_db
from app.deps import get_company
from app.models.company import Company
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.dev_testing import TestingResetAvailabilityRead, TestingResetRead
from app.services.testing_reset import is_testing_reset_enabled, reset_company_testing_data

router = APIRouter(prefix="/companies", tags=["dev-testing"])


def _require_gerente(user: User) -> None:
    if normalize_role(user.role) != UserRole.gerente:
        raise HTTPException(status_code=403, detail="Solo el rol Gerente puede reiniciar el entorno de pruebas.")


def _require_dev_mode() -> None:
    if not is_testing_reset_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Reinicio de testing deshabilitado. "
                "Definí APP_ENV=development o TEST_MODE=true (solo desarrollo)."
            ),
        )


@router.get("/{company_id}/dev/testing-reset-availability", response_model=TestingResetAvailabilityRead)
def testing_reset_availability(
    company_id: int,
    user: User = Depends(get_current_user),
    _company: Company = Depends(get_company),
) -> TestingResetAvailabilityRead:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="Sin acceso a esta empresa")
    if normalize_role(user.role) != UserRole.gerente:
        return TestingResetAvailabilityRead(
            enabled=False,
            reason="Solo visible para Gerente.",
        )
    if not is_testing_reset_enabled():
        return TestingResetAvailabilityRead(
            enabled=False,
            reason="Requiere APP_ENV=development o TEST_MODE=true.",
        )
    return TestingResetAvailabilityRead(enabled=True, reason="")


@router.post("/{company_id}/dev/reset-testing-data", response_model=TestingResetRead)
def reset_testing_data(
    company_id: int,
    user: User = Depends(get_current_user),
    _company: Company = Depends(get_company),
    db: Session = Depends(get_db),
) -> TestingResetRead:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="Sin acceso a esta empresa")
    _require_dev_mode()
    _require_gerente(user)
    result = reset_company_testing_data(db, company_id=company_id)
    return TestingResetRead.model_validate(
        {
            **result,
            "detail": (
                f"Reiniciados {result['prospects_reset']} prospectos. "
                f"Eliminados {result['messages_deleted']} mensajes y "
                f"{result['meetings_deleted']} reuniones de prueba."
            ),
        }
    )
