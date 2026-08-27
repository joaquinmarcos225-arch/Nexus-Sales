"""Cache global Nexus de empresas empleadoras + contactos (reuso antes de Prospeo)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class NexusCompanyCache(Base):
    """Empresa empleadora (no el tenant Nexus)."""

    __tablename__ = "nexus_company_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    contacts: Mapped[list[NexusContactCache]] = relationship(back_populates="employer")


class NexusContactCache(Base):
    """Persona/contacto reutilizable entre campañas/tenants (con delivery tracking)."""

    __tablename__ = "nexus_contact_cache"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_cache_id: Mapped[int | None] = mapped_column(
        ForeignKey("nexus_company_cache.id"), nullable=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    linkedin_slug: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    whatsapp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    employer: Mapped[NexusCompanyCache | None] = relationship(back_populates="contacts")
    deliveries: Mapped[list[NexusContactDelivery]] = relationship(back_populates="contact")


class NexusContactDelivery(Base):
    """Qué tenant Nexus ya recibió este contacto (anti-dupe cross-cliente)."""

    __tablename__ = "nexus_contact_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "contact_cache_id",
            "tenant_company_id",
            name="uq_nexus_contact_delivery_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contact_cache_id: Mapped[int] = mapped_column(
        ForeignKey("nexus_contact_cache.id"), nullable=False, index=True
    )
    tenant_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), nullable=False, index=True
    )
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prospect_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # delivered | suppressed | … — outcome comercial opcional (replied, meeting, …)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="delivered")
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)

    contact: Mapped[NexusContactCache] = relationship(back_populates="deliveries")
