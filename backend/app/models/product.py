from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.company import Company


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value_proposition: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # b2b | b2c | both — define qué tipos de campaña admite el producto
    market_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="b2b")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    company: Mapped[Company] = relationship("Company", back_populates="products")
    campaigns: Mapped[list[Campaign]] = relationship("Campaign", back_populates="product")
