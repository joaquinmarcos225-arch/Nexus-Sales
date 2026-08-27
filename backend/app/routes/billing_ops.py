"""API Ops · cobro mensual → tools → créditos Nexus."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.models.company import Company
from app.models.user import User
from app.services.billing_ops import (
    board_for_companies,
    company_ops_summary,
    get_or_create_cycle,
    grant_cycle_credits,
    mark_paid,
    mark_tool_top_up,
    serialize_cycle,
    set_company_plan,
    update_custom_credits,
)
from app.services.credit_ledger import current_plan_cycle_key
from app.services.credit_plans import list_contact_plans, plan_economics_dict
from app.services.credits import CreditError, assert_gerente_actor

router = APIRouter(tags=["billing-ops"])


class SetPlanBody(BaseModel):
    plan: str = Field(min_length=1, max_length=64)
    custom_credits: int | None = Field(default=None, ge=0, le=2_000_000)


class CustomCreditsBody(BaseModel):
    credits: int = Field(ge=1, le=2_000_000)


class MarkPaidBody(BaseModel):
    paid: bool = True
    cycle_key: str | None = Field(default=None, max_length=7)


class MarkToolBody(BaseModel):
    topped_up: bool = True
    cycle_key: str | None = Field(default=None, max_length=7)


class GrantBody(BaseModel):
    cycle_key: str | None = Field(default=None, max_length=7)


def _err(exc: CreditError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _ops_company(company_id: int, db: Session, user: User) -> Company:
    """Ops CostGuard: director/owner puede operar cualquier empresa del tablero."""
    try:
        assert_gerente_actor(user)
    except CreditError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return company


@router.get("/billing-ops/plans")
def list_plans_economics(
    _: User = Depends(get_current_user),
) -> dict:
    return {"plans": [plan_economics_dict(p) for p in list_contact_plans()]}


@router.get("/billing-ops/board")
def ops_board(
    cycle_key: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        assert_gerente_actor(user)
    except CreditError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    companies = list(db.scalars(select(Company).order_by(Company.id.asc())).all())
    data = board_for_companies(db, companies, cycle_key=cycle_key)
    db.commit()
    return data


@router.get("/companies/{company_id}/billing-ops")
def company_billing_ops(
    company_id: int,
    cycle_key: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    company = _ops_company(company_id, db, user)
    out = company_ops_summary(db, company, cycle_key=cycle_key)
    db.commit()
    return out


@router.patch("/companies/{company_id}/billing-ops/plan")
def patch_company_plan(
    company_id: int,
    body: SetPlanBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    company = _ops_company(company_id, db, user)
    try:
        set_company_plan(
            db,
            company.id,
            body.plan,
            custom_credits=body.custom_credits,
        )
        db.commit()
        return company_ops_summary(db, company)
    except CreditError as e:
        db.rollback()
        raise _err(e) from e


@router.patch("/companies/{company_id}/billing-ops/custom-credits")
def patch_custom_credits(
    company_id: int,
    body: CustomCreditsBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    company = _ops_company(company_id, db, user)
    try:
        cycle = update_custom_credits(db, company.id, body.credits)
        db.commit()
        return {"cycle": serialize_cycle(cycle, company)}
    except CreditError as e:
        db.rollback()
        raise _err(e) from e


@router.post("/companies/{company_id}/billing-ops/mark-paid")
def post_mark_paid(
    company_id: int,
    body: MarkPaidBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    company = _ops_company(company_id, db, user)
    try:
        cycle = mark_paid(
            db,
            company.id,
            actor_user_id=user.id,
            cycle_key=body.cycle_key,
            paid=body.paid,
        )
        db.commit()
        return {"cycle": serialize_cycle(cycle, company)}
    except CreditError as e:
        db.rollback()
        raise _err(e) from e


@router.post("/companies/{company_id}/billing-ops/tools/{tool}")
def post_mark_tool(
    company_id: int,
    tool: str,
    body: MarkToolBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    company = _ops_company(company_id, db, user)
    try:
        cycle = mark_tool_top_up(
            db,
            company.id,
            tool,
            actor_user_id=user.id,
            cycle_key=body.cycle_key,
            topped_up=body.topped_up,
        )
        db.commit()
        return {"cycle": serialize_cycle(cycle, company)}
    except CreditError as e:
        db.rollback()
        raise _err(e) from e


@router.post("/companies/{company_id}/billing-ops/grant-credits")
def post_grant_credits(
    company_id: int,
    body: GrantBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    company = _ops_company(company_id, db, user)
    try:
        cycle, amount = grant_cycle_credits(
            db,
            company.id,
            actor_user_id=user.id,
            cycle_key=body.cycle_key,
        )
        db.commit()
        return {
            "granted": amount,
            "cycle": serialize_cycle(cycle, company),
            "message": (
                f"Se acreditaron {amount} créditos al pool."
                if amount > 0
                else "Ciclo Ops confirmado (el wallet ya tenía el cupo del mes)."
            ),
        }
    except CreditError as e:
        db.rollback()
        raise _err(e) from e


@router.post("/companies/{company_id}/billing-ops/ensure-cycle")
def post_ensure_cycle(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    cycle_key: str | None = None,
) -> dict:
    company = _ops_company(company_id, db, user)
    cycle = get_or_create_cycle(db, company.id, cycle_key=cycle_key or current_plan_cycle_key())
    db.commit()
    return {"cycle": serialize_cycle(cycle, company)}
