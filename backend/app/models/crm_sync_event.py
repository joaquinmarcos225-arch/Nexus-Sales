from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.prospect import Prospect


class CrmSyncEvent(Base):
    """Idempotencia de sync CRM — un evento Nexus = una actividad por CRM."""

    __tablename__ = "crm_sync_events"
    __table_args__ = (
        UniqueConstraint("prospect_id", "event_key", name="uq_crm_sync_event_prospect_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    hubspot_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    salesforce_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hubspot_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    salesforce_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    prospect: Mapped[Prospect] = relationship("Prospect")
