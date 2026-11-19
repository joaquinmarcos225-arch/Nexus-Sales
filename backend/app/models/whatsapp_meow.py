"""Sesiones WhatsApp Web (whatsmeow) por usuario — multi-número, multi-tenant."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.user import User


class WhatsappMeowSession(Base):
    __tablename__ = "whatsapp_meow_sessions"
    __table_args__ = (
        UniqueConstraint("session_uuid", name="uq_wa_meow_session_uuid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_qr")
    phone_e164: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    qr_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    user: Mapped[User] = relationship("User")


class WhatsappMeowMessage(Base):
    __tablename__ = "whatsapp_meow_messages"
    __table_args__ = (
        UniqueConstraint("session_uuid", "idempotency_key", name="uq_wa_meow_out_idempotency"),
        UniqueConstraint("whatsapp_msg_id", name="uq_wa_meow_wa_msg_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in | out
    peer: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_msg_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    company: Mapped[Company] = relationship("Company")
    user: Mapped[User] = relationship("User")
