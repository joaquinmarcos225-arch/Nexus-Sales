from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.ai_instruction import AIInstruction
    from app.models.campaign import Campaign
    from app.models.connected_account import ConnectedAccount
    from app.models.credit_wallet import CreditWallet
    from app.models.meeting import Meeting
    from app.models.outreach_task import OutreachTask
    from app.models.product import Product
    from app.models.prospect import Prospect
    from app.models.seller_allocation import SellerCreditAllocation
    from app.models.team import Team
    from app.models.user import User


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(64), nullable=False, default="starter")
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    global_automation_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    users: Mapped[list[User]] = relationship("User", back_populates="company")
    teams: Mapped[list[Team]] = relationship("Team", back_populates="company")
    products: Mapped[list[Product]] = relationship("Product", back_populates="company")
    wallet: Mapped[CreditWallet | None] = relationship(
        "CreditWallet", back_populates="company", uselist=False
    )
    seller_allocations: Mapped[list[SellerCreditAllocation]] = relationship(
        "SellerCreditAllocation", back_populates="company"
    )
    campaigns: Mapped[list[Campaign]] = relationship("Campaign", back_populates="company")
    prospects: Mapped[list[Prospect]] = relationship("Prospect", back_populates="company")
    ai_instructions: Mapped[list[AIInstruction]] = relationship(
        "AIInstruction", back_populates="company", cascade="all, delete-orphan"
    )
    outreach_tasks: Mapped[list[OutreachTask]] = relationship(
        "OutreachTask", back_populates="company", cascade="all, delete-orphan"
    )
    meetings: Mapped[list[Meeting]] = relationship(
        "Meeting", back_populates="company", cascade="all, delete-orphan"
    )
    connected_accounts: Mapped[list[ConnectedAccount]] = relationship(
        "ConnectedAccount", back_populates="company", cascade="all, delete-orphan"
    )
