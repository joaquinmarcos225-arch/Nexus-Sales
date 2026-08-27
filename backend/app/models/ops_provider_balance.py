"""Saldo ops de proveedores (OpenAI/Brave manual; cache Prospeo)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OpsProviderBalance(Base):
    __tablename__ = "ops_provider_balances"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    balance_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    balance_credits: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
