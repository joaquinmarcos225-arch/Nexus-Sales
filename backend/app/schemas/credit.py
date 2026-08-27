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
    plan: str = "starter"
    plan_label: str = "Starter"
    plan_contact_credits: int = 4_000
    plan_description: str = ""
    plan_cycle_key: str | None = None
    plan_last_credited_at: datetime | None = None


class CreditLedgerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    user_id: int | None = None
    from_user_id: int | None = None
    actor_user_id: int | None = None
    kind: str
    kind_label: str = ""
    amount: int
    note: str
    created_at: datetime
    user_name: str | None = None
    from_user_name: str | None = None
    actor_name: str | None = None


class CreditPeerTransferRead(BaseModel):
    """Mensaje de transferencia en el chat peer-to-peer."""

    id: int
    amount: int
    note: str
    created_at: datetime
    from_user_id: int
    to_user_id: int
    direction: str  # "out" | "in" relativo al usuario autenticado
    from_user_name: str | None = None
    to_user_name: str | None = None


class SellerAllocationCreate(BaseModel):
    seller_id: int
    amount: int = Field(gt=0, description="Créditos a asignar al vendedor")


class CreditTransferCreate(BaseModel):
    from_user_id: int = Field(ge=1)
    to_user_id: int = Field(ge=1)
    amount: int = Field(gt=0, description="Créditos a transferir desde el pool del origen")


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
