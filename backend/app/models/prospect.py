from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.campaign import Campaign
    from app.models.company import Company
    from app.models.meeting import Meeting
    from app.models.outreach import OutreachMessage
    from app.models.outreach_task import OutreachTask
    from app.models.user import User


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(255), nullable=True)

    linkedin_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Hilo Gmail (threads.get) — se setea al crear borrador Nexus o al resolver búsqueda en sync.
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_website: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    source_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)

    compatibility_score: Mapped[int] = mapped_column(Integer, nullable=False)
    interest_probability: Mapped[int] = mapped_column(Integer, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Motor de sequía / IA conversacional (ver followup_engine, conversation_intelligence)
    outreach_touch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    objection_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    objection_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    interest_level: Mapped[str] = mapped_column(String(16), nullable=False, default="low")
    meeting_nudge_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    followup_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_best_action: Mapped[str | None] = mapped_column(String(255), nullable=True)

    pipeline_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="nuevo")
    meeting_suggestion_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    preferred_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    channel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_assisted_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_assist_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    linkedin_assist_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linkedin_last_assisted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    linkedin_sdr_marked_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Secuencia multicanal 21d (UX operativa; ProspectStatus sigue para analytics)
    sequence_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sequence_group: Mapped[str] = mapped_column(String(32), nullable=False, default="contactado")
    sequence_state: Mapped[str] = mapped_column(String(32), nullable=False, default="sin_respuesta")
    sequence_fired_milestones: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sequence_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reactivation_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Postergación explícita (timing del prospecto); la secuencia se pausa hasta `defer_resume_at`.
    defer_resume_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Propiedad / ownership entre SDRs de la empresa
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ownership_status: Mapped[str] = mapped_column(String(32), nullable=False, default="libre")
    commercial_state: Mapped[str] = mapped_column(String(32), nullable=False, default="prospeccion")
    commercial_state_is_testing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    conversation_state: Mapped[str] = mapped_column(String(32), nullable=False, default="sin_conversacion")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sequence_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sequence_playbook_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_touch_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    playbook_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ownership_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    previous_owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    company: Mapped[Company] = relationship("Company", back_populates="prospects")
    campaign: Mapped[Campaign] = relationship("Campaign", back_populates="prospects")
    owner: Mapped["User | None"] = relationship(
        "User",
        back_populates="owned_prospects",
        foreign_keys=[owner_user_id],
    )
    outreach_messages: Mapped[list[OutreachMessage]] = relationship(
        "OutreachMessage", back_populates="prospect", cascade="all, delete-orphan"
    )
    outreach_tasks: Mapped[list[OutreachTask]] = relationship(
        "OutreachTask", back_populates="prospect", cascade="all, delete-orphan"
    )
    meetings: Mapped[list[Meeting]] = relationship(
        "Meeting", back_populates="prospect", cascade="all, delete-orphan"
    )
