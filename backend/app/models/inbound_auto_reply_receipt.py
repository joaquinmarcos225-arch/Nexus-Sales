"""Registro idempotente: una respuesta automática por inbound Gmail (message id)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class InboundAutoReplyReceipt(Base):
    __tablename__ = "inbound_auto_reply_receipts"
    __table_args__ = (
        UniqueConstraint(
            "prospect_id",
            "inbound_gmail_message_id",
            name="uq_inbound_auto_reply_prospect_message",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), nullable=False)
    inbound_gmail_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
