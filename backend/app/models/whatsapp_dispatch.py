"""Envíos WhatsApp por Gupshup/API: ids, estados y facturación."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.company import Company
    from app.models.outreach import OutreachMessage
    from app.models.prospect import Prospect


class WhatsAppDispatch(Base):
    __tablename__ = "whatsapp_dispatches"
    __table_args__ = (
        UniqueConstraint("gupshup_message_id", name="uq_wa_dispatch_gupshup_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), nullable=False, index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    outreach_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("outreach_messages.id"), nullable=True
    )
    destination_digits: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    dest_country: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="marketing")
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="gupshup")
    gupshup_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    submit_status: Mapped[str] = mapped_column(String(16), nullable=False, default="submitted")
    last_event: Mapped[str | None] = mapped_column(String(24), nullable=True)
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    billable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    billed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship("Company")
    prospect: Mapped[Prospect] = relationship("Prospect")
    campaign: Mapped[Campaign] = relationship("Campaign")
    outreach_message: Mapped[OutreachMessage | None] = relationship("OutreachMessage")


class WhatsAppWebhookEvent(Base):
    __tablename__ = "whatsapp_webhook_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_wa_webhook_event_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    dispatch_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatsapp_dispatches.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
