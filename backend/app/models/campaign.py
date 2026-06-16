from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.meeting import Meeting
    from app.models.outreach import OutreachMessage, OutreachSequence
    from app.models.outreach_task import OutreachTask
    from app.models.product import Product
    from app.models.prospect import Prospect
    from app.models.user import User


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    seller_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    autopilot_status: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    autopilot_last_cycle_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    autopilot_last_cycle_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    target_company_size: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_language: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_role: Mapped[str | None] = mapped_column(String(255), nullable=True)

    prospect_count: Mapped[int] = mapped_column(Integer, nullable=False)

    calendar_link: Mapped[str] = mapped_column(String(2048), nullable=False)
    timezone: Mapped[str] = mapped_column(String(128), nullable=False)
    available_hours: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(String(255), nullable=False)

    # Canales permitidos para outreach simulado (y futuros conectores reales). Orden prioritario implícito: linkedin → email → whatsapp.
    allowed_channels: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: ["linkedin", "email", "whatsapp"],
    )

    estimated_meetings_min: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_meetings_max: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_min: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_cost_max: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_avg_cost_per_meeting: Mapped[float] = mapped_column(
        Float(asdecimal=False), nullable=False, default=0.0
    )

    icp_ai_last_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    outreach_activity_log: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    followup_delay_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_auto_followups: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Modo real Gmail: draft_only (default) o auto_send (requiere NEXUS_AUTO_SEND_ENABLED=1).
    outreach_email_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="draft_only")
    # Respuesta automática a inbound: borrador vs envío (delay en minutos).
    inbound_reply_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="draft_only")
    inbound_reply_delay_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    # Pausa instantánea de automatización (scheduler / follow-ups / ticks).
    automation_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # manual | semi_auto | full_auto — control operativo unificado
    automation_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="semi_auto")

    company: Mapped[Company] = relationship("Company", back_populates="campaigns")
    seller: Mapped[User] = relationship("User", back_populates="campaigns_assigned")
    product: Mapped[Product] = relationship("Product", back_populates="campaigns")
    prospects: Mapped[list[Prospect]] = relationship(
        "Prospect", back_populates="campaign", cascade="all, delete-orphan"
    )
    outreach_messages: Mapped[list[OutreachMessage]] = relationship(
        "OutreachMessage", back_populates="campaign", cascade="all, delete-orphan"
    )
    outreach_sequence: Mapped[OutreachSequence | None] = relationship(
        "OutreachSequence", back_populates="campaign", uselist=False, cascade="all, delete-orphan"
    )
    outreach_tasks: Mapped[list[OutreachTask]] = relationship(
        "OutreachTask", back_populates="campaign", cascade="all, delete-orphan"
    )
    meetings: Mapped[list[Meeting]] = relationship(
        "Meeting", back_populates="campaign", cascade="all, delete-orphan"
    )
