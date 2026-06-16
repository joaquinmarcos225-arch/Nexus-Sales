"""Estado persistido de jobs del worker (locks, última ejecución, errores)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.database.base import Base
from app.database.config import DATABASE_URL

_JSONType = JSON().with_variant(SQLITE_JSON(), "sqlite") if "sqlite" in DATABASE_URL else JSON()


class AutomationJobState(Base):
    """
    Una fila por job lógico (ej. tick:gmail_inbound).
    `locked_until` evita corridas solapadas entre workers (un solo proceso recomendado).
    """

    __tablename__ = "automation_job_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_result_meta: Mapped[dict[str, Any] | None] = mapped_column(_JSONType, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
