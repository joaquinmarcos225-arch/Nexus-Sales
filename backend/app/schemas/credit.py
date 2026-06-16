from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WalletTopUp(BaseModel):
    amount: int = Field(gt=0, description="Créditos a añadir (simulado)")


class WalletRead(BaseModel):
    company_id: int
    total_balance: int
    assigned_to_sellers: int
    unassigned_balance: int
    wallet_id: int
    updated_at: datetime


class SellerAllocationCreate(BaseModel):
    seller_id: int
    amount: int = Field(gt=0, description="Créditos a asignar al vendedor")


class CreditAllocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    seller_id: int
    allocated_balance: int
    used_balance: int
    created_at: datetime
    updated_at: datetime


class CreditAllocationReadWithSeller(CreditAllocationRead):
    seller_name: str
    seller_email: str
