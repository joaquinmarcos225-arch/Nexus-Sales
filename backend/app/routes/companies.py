from datetime import UTC, datetime
import logging
import os
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
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credit_plans import credits_for_plan, normalize_plan_key, plan_definition

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
    allow = (os.getenv("NEXUS_ALLOW_WORKSPACE_SIGNUP") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not allow:
        raise HTTPException(
            status_code=403,
            detail="Alta de empresa deshabilitada. Usá POST /onboarding/workspace o activá NEXUS_ALLOW_WORKSPACE_SIGNUP.",
        )
    plan = normalize_plan_key(payload.plan)
    company = Company(name=payload.name, employee_count=payload.employee_count, plan=plan)
    db.add(company)
    db.flush()
    initial_credits = credits_for_plan(plan)
    cycle = current_plan_cycle_key()
    wallet = CreditWallet(
        company_id=company.id,
        total_balance=initial_credits,
        plan_cycle_key=cycle,
        plan_last_credited_at=datetime.now(UTC),
    )
    db.add(wallet)
    db.flush()
    plan_def = plan_definition(plan)
    record_credit_ledger(
        db,
        company_id=company.id,
        kind="plan_seed",
        amount=initial_credits,
        note=f"Alta {plan_def.label}: +{initial_credits} créditos al pool ({cycle})",
    )
    db.commit()
    db.refresh(company)
    return company


@router.get("/{company_id}", response_model=CompanyRead)
def retrieve_company(
    company: Company = Depends(get_company),
) -> Company:
    return company
