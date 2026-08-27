from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.core.permissions import normalize_role
from app.database.session import get_db
from app.deps import get_company
from app.models import Company
from app.models.enums import UserRole
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User
from app.schemas.credit import (
    CreditAllocationReadWithSeller,
    CreditLedgerRead,
    CreditPeerTransferRead,
    CreditTransferCreate,
    SellerAllocationCreate,
    WalletRead,
    WalletTopUp,
)
from app.services.credit_ledger import (
    LEDGER_KIND_LABELS,
    list_credit_ledger,
    list_peer_transfer_ledger,
)
from app.services.credit_plans import credits_for_plan
from app.services.credits import (
    CreditError,
    allocate_to_seller,
    apply_plan_credits_to_company,
    assert_gerente_actor,
    assert_transfer_actor,
    get_user_available_credits,
    get_wallet_totals,
    plan_wallet_summary,
    reconcile_wallet_pool,
    top_up_company,
    transfer_credits_between_users,
    visible_allocation_user_ids,
)

router = APIRouter(tags=["credits"])


def _wallet_read(db: Session, company_id: int, wallet, total: int, assigned: int, unassigned: int) -> WalletRead:
    company = db.get(Company, company_id)
    plan_meta = plan_wallet_summary(company) if company else plan_wallet_summary(None)
    plan_credits = int(plan_meta.get("plan_contact_credits") or credits_for_plan(getattr(company, "plan", None)))
    return WalletRead(
        company_id=company_id,
        total_balance=total,
        assigned_to_sellers=assigned,
        unassigned_balance=max(0, unassigned),
        wallet_id=wallet.id,
        updated_at=_ensure_aware(wallet.updated_at),
        plan=str(plan_meta.get("plan") or "starter"),
        plan_label=str(plan_meta.get("plan_label") or "Starter"),
        plan_contact_credits=plan_credits,
        plan_description=str(plan_meta.get("plan_description") or ""),
        plan_cycle_key=getattr(wallet, "plan_cycle_key", None),
        plan_last_credited_at=_maybe_aware(getattr(wallet, "plan_last_credited_at", None)),
    )


def _maybe_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return _ensure_aware(dt)


@router.get("/users/me/credits")
def get_my_credits(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int | str]:
    role = normalize_role(user.role)
    if role in (UserRole.gerente, UserRole.owner):
        try:
            _, _total, assigned, unassigned = get_wallet_totals(db, user.company_id)
        except CreditError:
            assigned = 0
            unassigned = 0
        available = max(0, int(unassigned))
        return {
            "user_id": user.id,
            "company_id": user.company_id,
            "role_scope": "director_pool",
            "allocated_balance": int(assigned),
            "used_balance": 0,
            "available_balance": available,
        }

    available = get_user_available_credits(db, user.company_id, user.id)
    row = db.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == user.company_id,
            SellerCreditAllocation.seller_id == user.id,
        )
    ).first()
    allocated = int(row.allocated_balance) if row else 0
    used = int(row.used_balance) if row else 0
    return {
        "user_id": user.id,
        "company_id": user.company_id,
        "role_scope": "personal",
        "allocated_balance": allocated,
        "used_balance": used,
        "available_balance": available,
    }


@router.get("/companies/{company_id}/wallet", response_model=WalletRead)
def get_company_wallet(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> WalletRead:
    try:
        repaired = reconcile_wallet_pool(db, company_id)
        wallet, total, assigned, unassigned = get_wallet_totals(db, company_id)
        if repaired:
            db.commit()
            db.refresh(wallet)
    except CreditError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _wallet_read(db, company_id, wallet, total, assigned, unassigned)


@router.post("/companies/{company_id}/wallet/apply-plan", response_model=WalletRead)
def apply_plan_wallet_credits(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> WalletRead:
    """Acredita al pool los contactos del plan comercial (renovación / alta)."""
    try:
        assert_gerente_actor(user)
        wallet = apply_plan_credits_to_company(db, company_id, manual=True, actor_user_id=user.id)
        db.commit()
        db.refresh(wallet)
        _, total, assigned, unassigned = get_wallet_totals(db, company_id)
    except CreditError as e:
        db.rollback()
        msg = str(e)
        status = 409 if "ya fue acreditado" in msg.lower() else 403
        raise HTTPException(status_code=status, detail=msg) from e
    return _wallet_read(db, company_id, wallet, total, assigned, unassigned)


@router.post("/companies/{company_id}/wallet/top-up", response_model=WalletRead)
def top_up_wallet(
    company_id: int,
    payload: WalletTopUp,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> WalletRead:
    """Ajuste manual de créditos (ops). Preferir apply-plan según contrato."""
    try:
        assert_gerente_actor(user)
        wallet = top_up_company(db, company_id, payload.amount, actor_user_id=user.id)
        db.commit()
        db.refresh(wallet)
        _, total, assigned, unassigned = get_wallet_totals(db, company_id)
    except CreditError as e:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(e)) from e
    return _wallet_read(db, company_id, wallet, total, assigned, unassigned)


@router.get("/companies/{company_id}/credit-allocations", response_model=list[CreditAllocationReadWithSeller])
def list_credit_allocations(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> list[CreditAllocationReadWithSeller]:
    users = db.scalars(select(User).where(User.company_id == company_id)).all()
    visible_ids = visible_allocation_user_ids(user, list(users))
    rows = db.scalars(
        select(SellerCreditAllocation).where(SellerCreditAllocation.company_id == company_id)
    ).all()
    out: list[CreditAllocationReadWithSeller] = []
    for row in rows:
        if row.seller_id not in visible_ids:
            continue
        seller = db.get(User, row.seller_id)
        if seller is None:
            continue
        from app.database.seed import is_demo_test_email

        if is_demo_test_email(seller.email) or not seller.is_active:
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


@router.get("/companies/{company_id}/credit-ledger", response_model=list[CreditLedgerRead])
def list_company_credit_ledger(
    company_id: int,
    limit: int = 60,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> list[CreditLedgerRead]:
    try:
        assert_gerente_actor(user)
    except CreditError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    rows = list_credit_ledger(db, company_id, limit=limit)
    out: list[CreditLedgerRead] = []
    for row in rows:
        subject = db.get(User, row.user_id) if row.user_id else None
        from_user = db.get(User, row.from_user_id) if getattr(row, "from_user_id", None) else None
        actor = db.get(User, row.actor_user_id) if row.actor_user_id else None
        out.append(
            CreditLedgerRead(
                id=row.id,
                company_id=row.company_id,
                user_id=row.user_id,
                from_user_id=getattr(row, "from_user_id", None),
                actor_user_id=row.actor_user_id,
                kind=row.kind,
                kind_label=LEDGER_KIND_LABELS.get(row.kind, row.kind),
                amount=int(row.amount),
                note=row.note,
                created_at=_ensure_aware(row.created_at),
                user_name=subject.name if subject else None,
                from_user_name=from_user.name if from_user else None,
                actor_name=actor.name if actor else None,
            )
        )
    return out


@router.get(
    "/companies/{company_id}/credit-peer-transfers",
    response_model=list[CreditPeerTransferRead],
)
def list_credit_peer_transfers(
    company_id: int,
    peer_user_id: int,
    limit: int = 80,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> list[CreditPeerTransferRead]:
    try:
        assert_transfer_actor(user)
    except CreditError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    peer = db.get(User, peer_user_id)
    if peer is None or peer.company_id != company_id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if peer_user_id == user.id:
        raise HTTPException(status_code=400, detail="Elegí otro usuario del equipo")

    rows = list_peer_transfer_ledger(
        db, company_id, me_user_id=user.id, peer_user_id=peer_user_id, limit=limit
    )
    out: list[CreditPeerTransferRead] = []
    for row in rows:
        from_id = int(row.from_user_id or row.actor_user_id or 0)
        to_id = int(row.user_id or 0)
        if not from_id or not to_id:
            continue
        from_u = db.get(User, from_id)
        to_u = db.get(User, to_id)
        direction = "out" if from_id == user.id else "in"
        out.append(
            CreditPeerTransferRead(
                id=row.id,
                amount=int(row.amount),
                note=row.note or "",
                created_at=_ensure_aware(row.created_at),
                from_user_id=from_id,
                to_user_id=to_id,
                direction=direction,
                from_user_name=from_u.name if from_u else None,
                to_user_name=to_u.name if to_u else None,
            )
        )
    return out


@router.post("/companies/{company_id}/credit-allocations", response_model=CreditAllocationReadWithSeller)
def create_credit_allocation(
    company_id: int,
    payload: SellerAllocationCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> CreditAllocationReadWithSeller:
    try:
        assert_gerente_actor(user)
        row = allocate_to_seller(db, company_id, payload.seller_id, payload.amount, actor_user_id=user.id)
        db.commit()
        db.refresh(row)
    except CreditError as e:
        db.rollback()
        status = 403 if "gerente" in str(e).lower() else 400
        raise HTTPException(status_code=status, detail=str(e)) from e

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


@router.post(
    "/companies/{company_id}/credit-transfers",
    response_model=CreditAllocationReadWithSeller,
)
def transfer_credit_allocation(
    company_id: int,
    payload: CreditTransferCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    user: User = Depends(get_current_user),
) -> CreditAllocationReadWithSeller:
    try:
        assert_transfer_actor(user)
        # Siempre desde el saldo del usuario autenticado (no confiar en from_user_id del body).
        if int(payload.from_user_id) != int(user.id):
            raise CreditError("Solo podés transferir desde tu propio saldo")
        _from_row, to_row = transfer_credits_between_users(
            db,
            company_id,
            user.id,
            payload.to_user_id,
            payload.amount,
            actor=user,
        )
        db.commit()
        db.refresh(to_row)
    except CreditError as e:
        db.rollback()
        status = 403 if "permiso" in str(e).lower() else 400
        raise HTTPException(status_code=status, detail=str(e)) from e

    seller = db.get(User, to_row.seller_id)
    if seller is None:
        raise HTTPException(status_code=500, detail="Usuario destino inconsistente")
    return CreditAllocationReadWithSeller(
        id=to_row.id,
        company_id=to_row.company_id,
        seller_id=to_row.seller_id,
        allocated_balance=int(to_row.allocated_balance),
        used_balance=int(to_row.used_balance),
        created_at=to_row.created_at,
        updated_at=to_row.updated_at,
        seller_name=seller.name,
        seller_email=seller.email,
    )


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
