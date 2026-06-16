from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.company import Company
    from app.models.prospect import Prospect


class OutreachTask(Base):
    """
    Tareas operativas (follow-ups futuros con cron/timers cuando existan conectores reales).

    task_kind típicos:
    - scheduled_followup: re-contactar prospecto tras N días
    - review_inbound: revisar última respuesta
    - hot_lead: prospecto muy interesado
    - awaiting_reply: esperando réplica tras nuestro envío
    """

    __tablename__ = "outreach_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), nullable=True)

    task_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship("Company", back_populates="outreach_tasks")
    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="outreach_tasks")
    prospect: Mapped[Prospect | None] = relationship("Prospect", back_populates="outreach_tasks")
