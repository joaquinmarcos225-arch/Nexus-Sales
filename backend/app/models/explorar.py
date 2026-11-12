"""Explorar: búsqueda estilo LinkedIn sobre Prospeo (uso acumulado, revelados, cache)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ExplorarUsage(Base):
    __tablename__ = "explorar_usage"

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    pages_unbilled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    pages_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExplorarReveal(Base):
    __tablename__ = "explorar_reveals"
    __table_args__ = (UniqueConstraint("company_id", "person_id", name="uq_explorar_reveal_person"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revealed_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    charged: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prospect_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ExplorarFavorite(Base):
    """Persona guardada con estrella por un vendedor (no se le vuelve a mostrar en búsquedas)."""

    __tablename__ = "explorar_favorites"
    __table_args__ = (UniqueConstraint("user_id", "person_id", name="uq_explorar_favorite_user_person"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_size: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    has_email: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_mobile: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    email_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mobile_verified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyMobileUsage(Base):
    """Celulares pagados a Prospeo por empresa y mes (uso razonable de WhatsApp)."""

    __tablename__ = "company_mobile_usage"

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    paid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ExplorarSearchCache(Base):
    __tablename__ = "explorar_search_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
