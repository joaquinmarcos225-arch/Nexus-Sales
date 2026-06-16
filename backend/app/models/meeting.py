from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.company import Company
    from app.models.prospect import Prospect


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meeting_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    timezone: Mapped[str] = mapped_column(String(128), nullable=False, default="America/Argentina/Buenos_Aires")
    suggested_slots: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    google_calendar_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    google_calendar_html_link: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    creation_method: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship("Company", back_populates="meetings")
    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="meetings")
    prospect: Mapped[Prospect] = relationship("Prospect", back_populates="meetings")
