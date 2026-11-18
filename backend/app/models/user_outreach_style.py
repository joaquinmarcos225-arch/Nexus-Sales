"""Estilo de outreach personal por SDR (solo afecta sus mensajes)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if False:  # TYPE_CHECKING
    from app.models.user import User


class UserOutreachStyle(Base):
    __tablename__ = "user_outreach_styles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    examples_linkedin: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    examples_email: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    examples_whatsapp: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    templates_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="outreach_style")
