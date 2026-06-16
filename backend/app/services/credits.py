from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User


class CreditError(Exception):
    pass


def get_wallet_totals(session: Session, company_id: int) -> tuple[CreditWallet, int, int, int]:
    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    wallet = session.scalars(select(CreditWallet).where(CreditWallet.company_id == company_id)).one_or_none()
    if wallet is None:
        raise CreditError("Wallet no encontrada para esta empresa")
    assigned = session.scalar(
        select(func.coalesce(func.sum(SellerCreditAllocation.allocated_balance), 0)).where(
            SellerCreditAllocation.company_id == company_id
        )
    )
    assigned = int(assigned or 0)
    total = int(wallet.total_balance)
    unassigned = total - assigned
    return wallet, total, assigned, unassigned


def ensure_wallet(session: Session, company: Company) -> CreditWallet:
    if company.wallet is None:
        w = CreditWallet(company_id=company.id, total_balance=0)
        session.add(w)
        session.flush()
        return w
    return company.wallet


def top_up_company(session: Session, company_id: int, amount: int) -> CreditWallet:
    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    wallet = ensure_wallet(session, company)
    wallet.total_balance = int(wallet.total_balance) + int(amount)
    session.flush()
    return wallet


def allocate_to_seller(session: Session, company_id: int, seller_id: int, amount: int) -> SellerCreditAllocation:
    seller = session.get(User, seller_id)
    if seller is None or seller.company_id != company_id:
        raise CreditError("Vendedor no encontrado en esta empresa")
    if seller.role != UserRole.sdr.value:
        raise CreditError("Solo se puede asignar saldo a usuarios con rol seller")

    company = session.get(Company, company_id)
    if company is None:
        raise CreditError("Empresa no encontrada")
    ensure_wallet(session, company)
    session.flush()

    _, _total, _assigned, unassigned = get_wallet_totals(session, company_id)
    if amount > unassigned:
        raise CreditError("Saldo no asignado insuficiente")

    row = session.scalars(
        select(SellerCreditAllocation).where(
            SellerCreditAllocation.company_id == company_id,
            SellerCreditAllocation.seller_id == seller_id,
        )
    ).first()
    if row is None:
        row = SellerCreditAllocation(company_id=company_id, seller_id=seller_id, allocated_balance=0, used_balance=0)
        session.add(row)
        session.flush()

    row.allocated_balance = int(row.allocated_balance) + int(amount)
    session.flush()
    return row


def company_dashboard_counts(session: Session, company_id: int) -> dict[str, int]:
    active_products = session.scalar(
        select(func.count()).select_from(Product).where(
            Product.company_id == company_id, Product.is_active.is_(True)
        )
    )
    sellers = session.scalar(
        select(func.count()).select_from(User).where(
            User.company_id == company_id, User.role == UserRole.sdr.value
        )
    )
    return {
        "active_products": int(active_products or 0),
        "active_sellers": int(sellers or 0),
    }
