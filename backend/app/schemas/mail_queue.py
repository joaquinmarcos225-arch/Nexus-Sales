"""Cola Mail — notificación de mails enviados (no acciones de envío)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MailQueueItemRead(BaseModel):
    outreach_message_id: int
    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    email: str | None = None
    subject: str = ""
    body: str = ""
    sent_at: datetime
    gmail_message_id: str
    gmail_web_link: str | None = None


class MailPendingItemRead(BaseModel):
    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    email: str | None = None
    subject: str = ""
    body: str = ""
    sequence_day: int | None = None


class MailQueueDayBucket(BaseModel):
    day_offset: int = 0
    label: str = "Hoy"
    actionable: bool = True
    limit: int = 0
    scheduled: int = 0
    items: list[MailPendingItemRead] = Field(default_factory=list)


class MailQueueRead(BaseModel):
    campaign_id: int
    items: list[MailQueueItemRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    remaining_today: int = 0
    pending_total: int = 0
    days: list[MailQueueDayBucket] = Field(default_factory=list)
