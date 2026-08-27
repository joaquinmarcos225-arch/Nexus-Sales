from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SequenceTemplate(Base):
    """Plantilla de secuencia guardada por una empresa (reusable en campañas)."""

    __tablename__ = "sequence_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # fixed | ia
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="fixed")
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    follow_up: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
