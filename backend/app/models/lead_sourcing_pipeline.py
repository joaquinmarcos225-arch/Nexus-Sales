"""Estado del pipeline de sourcing por campaña."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class LeadSourcingPipeline(Base):
    __tablename__ = "lead_sourcing_pipelines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), unique=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    companies_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    people_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=70)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
