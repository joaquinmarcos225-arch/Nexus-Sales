import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.deps import get_company
from app.models import Company
from app.models.credit_wallet import CreditWallet
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyRead

router = APIRouter(prefix="/companies", tags=["companies"])
_logger = logging.getLogger("nexus.http")


@router.get("", response_model=list[CompanyRead])
def list_companies(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Company]:
    """Devuelve la empresa del usuario logueado (multi-tenant por sesión)."""
    t0 = time.perf_counter()
    company = db.get(Company, user.company_id)
    rows = [company] if company else []
    _logger.info(
        "[companies] list_companies user=%s elapsed_ms=%s",
        user.id,
        int((time.perf_counter() - t0) * 1000),
    )
    return rows


@router.post("", response_model=CompanyRead, status_code=201)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)) -> Company:
    company = Company(name=payload.name, employee_count=payload.employee_count)
    db.add(company)
    db.flush()
    wallet = CreditWallet(company_id=company.id, total_balance=0)
    db.add(wallet)
    db.commit()
    db.refresh(company)
    return company


@router.get("/{company_id}", response_model=CompanyRead)
def retrieve_company(
    company: Company = Depends(get_company),
) -> Company:
    return company
