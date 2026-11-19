"""Contratos HTTP con el servicio Go nexus-whatsapp."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class MeowSessionView:
    session_id: str
    company_id: int
    user_id: int
    status: str
    phone: str | None
    display_name: str | None
    qr_code: str | None
    qr_expires_at: datetime | None
    last_error: str | None


@dataclass(frozen=True)
class MeowOutboundView:
    whatsapp_msg_id: str
    status: str
    error: str | None = None


class MeowEngineClient(Protocol):
    def create_session(self, *, company_id: int, user_id: int, session_id: str) -> MeowSessionView: ...

    def get_session(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView: ...

    def get_qr(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView: ...

    def cancel_pairing(self, session_id: str, *, company_id: int, user_id: int) -> None: ...

    def disconnect(self, session_id: str, *, company_id: int, user_id: int) -> None: ...

    def ensure_session(self, *, company_id: int, user_id: int, session_id: str) -> MeowSessionView: ...

    def set_chat_presence(
        self,
        session_id: str,
        *,
        company_id: int,
        user_id: int,
        to: str,
        state: str,
    ) -> None: ...

    def send_text(
        self,
        session_id: str,
        *,
        company_id: int,
        user_id: int,
        to: str,
        body: str,
        idempotency_key: str | None,
    ) -> MeowOutboundView: ...

    def simulate_pair(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView: ...

    def list_tenant(self, company_id: int, user_id: int) -> list[MeowSessionView]: ...


def parse_session_payload(data: dict[str, Any]) -> MeowSessionView:
    qr_exp = data.get("qr_expires_at")
    expires: datetime | None = None
    if isinstance(qr_exp, str) and qr_exp:
        expires = datetime.fromisoformat(qr_exp.replace("Z", "+00:00"))
    return MeowSessionView(
        session_id=str(data["id"]),
        company_id=int(data["company_id"]),
        user_id=int(data["user_id"]),
        status=str(data.get("status") or "pending_qr"),
        phone=(data.get("phone") or None) or None,
        display_name=(data.get("display_name") or None) or None,
        qr_code=(data.get("qr_code") or None) or None,
        qr_expires_at=expires,
        last_error=(data.get("last_error") or None) or None,
    )
