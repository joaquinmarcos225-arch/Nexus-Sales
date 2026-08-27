"""Ciclo mensual de cobro Ops: pagó → top-up tools → acreditar créditos Nexus."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.user import User


class BillingOpsCycle(Base):
    __tablename__ = "billing_ops_cycles"
    __table_args__ = (
        UniqueConstraint("company_id", "cycle_key", name="uq_billing_ops_company_cycle"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    cycle_key: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    plan_key: Mapped[str] = mapped_column(String(64), nullable=False, default="starter")

    # Cupo a acreditar (custom puede diferir del plan fijo).
    credits_to_grant: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    openai_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    prospeo_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    brave_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    openai_topped_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    openai_topped_up_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    prospeo_topped_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prospeo_topped_up_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    brave_topped_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    brave_topped_up_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    credits_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    credits_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    credits_granted_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits_granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship("Company", back_populates="billing_ops_cycles")
