from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.company import Company


class CreditWallet(Base):
    __tablename__ = "credit_wallets"
    __table_args__ = (UniqueConstraint("company_id", name="uq_wallet_company"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    total_balance: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # Total de créditos de la empresa (pool global)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    plan_cycle_key: Mapped[str | None] = mapped_column(String(7), nullable=True)
    plan_last_credited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    company: Mapped[Company] = relationship("Company", back_populates="wallet")
