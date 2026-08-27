"""Cola operativa de llamadas asistidas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CallAssistTaskRead(BaseModel):
    prospect_id: int
    prospect_name: str
    company_name: str | None = None
    phone_digits: str = ""
    phone_display: str = ""
    phone_kind: str = "unknown"
    brief: str = ""
    assist_status: str = "suggested"
    tel_href: str = ""
    sequence_group: str | None = None


class CallAssistQueueRead(BaseModel):
    campaign_id: int
    tasks: list[CallAssistTaskRead] = Field(default_factory=list)
    total_pending: int = 0


class CallAssistMarkDoneRead(BaseModel):
    ok: bool = True
    detail: str = ""
    assist_status: str = "done"
