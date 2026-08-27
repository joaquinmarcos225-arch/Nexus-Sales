from __future__ import annotations

from pydantic import BaseModel, Field


class GmailDraftCreate(BaseModel):
    user_id: int = Field(ge=1, description="Usuario (SDR) con Gmail conectado vía OAuth")
    company_id: int = Field(ge=1)
    campaign_id: int = Field(ge=1)
    prospect_id: int = Field(ge=1)


class GmailDraftRead(BaseModel):
    draft_id: str
    message_id: str | None = None
    gmail_web_link: str | None = None
    subject: str | None = None


class GmailSendCreate(BaseModel):
    """Envío real (Gmail API). Requiere confirm_send=true para evitar envíos accidentales."""

    user_id: int = Field(ge=1, description="Usuario (SDR) con Gmail conectado vía OAuth")
    company_id: int = Field(ge=1)
    campaign_id: int = Field(ge=1)
    prospect_id: int = Field(ge=1)
    subject: str | None = Field(default=None, description="Si se omite, Nexus genera asunto/cuerpo con IA (como borrador).")
    body: str | None = Field(default=None, description="Si se omite, Nexus genera el cuerpo con IA.")
    confirm_send: bool = Field(default=False, description="Debe ser true para ejecutar users.messages.send.")


class GmailSendRead(BaseModel):
    gmail_message_id: str | None = None
    thread_id: str | None = None
    gmail_web_link: str | None = None
    subject: str
    outreach_message_id: int = Field(ge=1, description="ID en outreach_messages (timeline Nexus).")


class GmailInboundSyncCreate(BaseModel):
    user_id: int = Field(ge=1, description="Vendedor con Gmail conectado (debe ser el seller de la campaña)")
    company_id: int = Field(ge=1)
    campaign_id: int = Field(ge=1)


class GmailInboundSyncRead(BaseModel):
    imported: int
    skipped_no_thread: int
    threads_examined: int
    messages_fetched: int = 0
    replies_detected: int = 0
    prospects_matched: int = 0
    prospects_scanned: int = 0
    auto_drafts: int = 0
    auto_sent: int = 0
    gmail_draft_sents_detected: int = 0
    errors: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
