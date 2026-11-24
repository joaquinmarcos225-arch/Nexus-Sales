"""Contrato público del Nexus Brain (orquestador + especialistas)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BrainAction = Literal[
    "skip",
    "skip_autoresponder",
    "booked",
    "alternatives",
    "booking_failed",
    "offer_hours",
    "calendar_link",
    "ask_email",
    "ask_slot",
    "canceled",
    "referral",
    "normal_reply",
    "escalate",
]


@dataclass
class BrainTurnResult:
    """Salida única del Brain para un turno inbound (todos los canales)."""

    action: BrainAction
    reply_body: str | None = None
    subject: str | None = None
    meeting_booking: dict[str, Any] | None = None
    reply_objective: str = "seguimiento"
    response_class: str = "otro"
    skip_reason: str | None = None
    used_agent: bool = False
    agent_trace: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    channel: str = "email"

    @property
    def should_skip(self) -> bool:
        return self.action in ("skip", "skip_autoresponder") or bool(self.skip_reason)


@dataclass
class BrainContext:
    """Contexto empaquetado para el orquestador y los especialistas."""

    company_id: int
    campaign_id: int
    prospect_id: int
    channel: str
    inbound_text: str
    prospect_name: str = ""
    prospect_company: str = ""
    prospect_role: str = ""
    product_name: str = ""
    product_pitch: str = ""
    conversation_summary: str = ""
    history_snippet: str = ""
    testing: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
