from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_company
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User
from app.schemas.credit import (
    CreditAllocationReadWithSeller,
    SellerAllocationCreate,
    WalletRead,
    WalletTopUp,
)
from app.services.credits import CreditError, allocate_to_seller, get_wallet_totals, top_up_company

router = APIRouter(tags=["credits"])


@router.get("/companies/{company_id}/wallet", response_model=WalletRead)
def get_company_wallet(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> WalletRead:
    try:
        wallet, total, assigned, unassigned = get_wallet_totals(db, company_id)
    except CreditError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return WalletRead(
        company_id=company_id,
        total_balance=total,
        assigned_to_sellers=assigned,
        unassigned_balance=unassigned,
        wallet_id=wallet.id,
        updated_at=_ensure_aware(wallet.updated_at),
    )


@router.post("/companies/{company_id}/wallet/top-up", response_model=WalletRead)
def top_up_wallet(
    company_id: int,
    payload: WalletTopUp,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> WalletRead:
    try:
        wallet = top_up_company(db, company_id, payload.amount)
        db.commit()
        db.refresh(wallet)
        _, total, assigned, unassigned = get_wallet_totals(db, company_id)
    except CreditError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return WalletRead(
        company_id=company_id,
        total_balance=total,
        assigned_to_sellers=assigned,
        unassigned_balance=unassigned,
        wallet_id=wallet.id,
        updated_at=_ensure_aware(wallet.updated_at),
    )


@router.get("/companies/{company_id}/credit-allocations", response_model=list[CreditAllocationReadWithSeller])
def list_credit_allocations(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> list[CreditAllocationReadWithSeller]:
    rows = db.scalars(
        select(SellerCreditAllocation).where(SellerCreditAllocation.company_id == company_id)
    ).all()
    out: list[CreditAllocationReadWithSeller] = []
    for row in rows:
        seller = db.get(User, row.seller_id)
        if seller is None:
            continue
        out.append(
            CreditAllocationReadWithSeller(
                id=row.id,
                company_id=row.company_id,
                seller_id=row.seller_id,
                allocated_balance=int(row.allocated_balance),
                used_balance=int(row.used_balance),
                created_at=row.created_at,
                updated_at=row.updated_at,
                seller_name=seller.name,
                seller_email=seller.email,
            )
        )
    return out


@router.post("/companies/{company_id}/credit-allocations", response_model=CreditAllocationReadWithSeller)
def create_credit_allocation(
    company_id: int,
    payload: SellerAllocationCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> CreditAllocationReadWithSeller:
    try:
        row = allocate_to_seller(db, company_id, payload.seller_id, payload.amount)
        db.commit()
        db.refresh(row)
    except CreditError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e

    seller = db.get(User, row.seller_id)
    if seller is None:
        raise HTTPException(status_code=500, detail="Vendedor inconsistente")
    return CreditAllocationReadWithSeller(
        id=row.id,
        company_id=row.company_id,
        seller_id=row.seller_id,
        allocated_balance=int(row.allocated_balance),
        used_balance=int(row.used_balance),
        created_at=row.created_at,
        updated_at=row.updated_at,
        seller_name=seller.name,
        seller_email=seller.email,
    )


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
