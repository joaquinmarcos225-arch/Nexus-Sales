"""Salud de la casilla Gmail de cada SDR: freno por rebotes, historial previo y dominio."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class MailboxHealth(Base):
    __tablename__ = "mailbox_health"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    brake_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    brake_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    brake_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prior_daily_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prior_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    domain_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    domain_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounce_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_down_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EmailBounce(Base):
    """Rebote de Gmail (mailer-daemon) detectado en la casilla del SDR."""

    __tablename__ = "email_bounces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    gmail_message_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    bounced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
