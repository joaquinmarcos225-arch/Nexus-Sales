"""Cliente HTTP al servicio Go nexus-whatsapp."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.services.whatsapp_meow_contract import (
    MeowOutboundView,
    MeowSessionView,
    parse_session_payload,
)


class HttpMeowEngineClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _headers(self, *, company_id: int | None = None, user_id: int | None = None) -> dict[str, str]:
        headers = {"X-Nexus-Internal-Token": self._token, "Content-Type": "application/json"}
        if company_id is not None:
            headers["X-Nexus-Company-Id"] = str(int(company_id))
        if user_id is not None:
            headers["X-Nexus-User-Id"] = str(int(user_id))
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        company_id: int | None = None,
        user_id: int | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        url = f"{self._base}{path}"
        with httpx.Client(timeout=self._timeout if timeout is None else timeout) as client:
            resp = client.request(
                method,
                url,
                headers=self._headers(company_id=company_id, user_id=user_id),
                **kwargs,
            )
        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise RuntimeError(f"whatsapp meow {resp.status_code}: {detail}")
        if resp.content:
            return resp.json()
        return None

    def create_session(self, *, company_id: int, user_id: int, session_id: str) -> MeowSessionView:
        data = self._request(
            "POST",
            "/v1/sessions",
            json={"company_id": company_id, "user_id": user_id, "session_id": session_id},
        )
        return parse_session_payload(data)

    def get_session(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView:
        data = self._request(
            "GET",
            f"/v1/sessions/{session_id}",
            company_id=company_id,
            user_id=user_id,
        )
        return parse_session_payload(data)

    def get_qr(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView:
        data = self._request(
            "GET",
            f"/v1/sessions/{session_id}/qr",
            company_id=company_id,
            user_id=user_id,
        )
        return parse_session_payload(data)

    def cancel_pairing(self, session_id: str, *, company_id: int, user_id: int) -> None:
        self._request(
            "POST",
            f"/v1/sessions/{session_id}/cancel",
            company_id=company_id,
            user_id=user_id,
        )

    def disconnect(self, session_id: str, *, company_id: int, user_id: int) -> None:
        self._request(
            "POST",
            f"/v1/sessions/{session_id}/disconnect",
            company_id=company_id,
            user_id=user_id,
        )

    def ensure_session(self, *, company_id: int, user_id: int, session_id: str) -> MeowSessionView:
        data = self._request(
            "POST",
            "/v1/sessions/ensure",
            json={"company_id": company_id, "user_id": user_id, "session_id": session_id},
        )
        return parse_session_payload(data)

    def send_text(
        self,
        session_id: str,
        *,
        company_id: int,
        user_id: int,
        to: str,
        body: str,
        idempotency_key: str | None,
    ) -> MeowOutboundView:
        data = self._request(
            "POST",
            f"/v1/sessions/{session_id}/messages",
            company_id=company_id,
            user_id=user_id,
            json={"to": to, "body": body, "idempotency_key": idempotency_key or ""},
        )
        return MeowOutboundView(
            whatsapp_msg_id=str(data.get("WhatsAppMsgID") or data.get("whatsapp_msg_id") or ""),
            status=str(data.get("Status") or data.get("status") or "sent"),
            error=data.get("Error") or data.get("error"),
        )

    def set_chat_presence(
        self,
        session_id: str,
        *,
        company_id: int,
        user_id: int,
        to: str,
        state: str,
    ) -> None:
        try:
            self._request(
                "POST",
                f"/v1/sessions/{session_id}/presence",
                company_id=company_id,
                user_id=user_id,
                timeout=2.5,
                json={"to": to, "state": state or "composing"},
            )
        except RuntimeError as exc:
            if " 404" in str(exc) or "404:" in str(exc):
                return
            raise

    def simulate_pair(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView:
        data = self._request(
            "POST",
            f"/v1/sessions/{session_id}/simulate-pair",
            company_id=company_id,
            user_id=user_id,
        )
        return parse_session_payload(data)

    def list_tenant(self, company_id: int, user_id: int) -> list[MeowSessionView]:
        data = self._request(
            "GET",
            f"/v1/tenants/{company_id}/{user_id}/sessions",
            company_id=company_id,
            user_id=user_id,
        )
        sessions = data.get("sessions") if isinstance(data, dict) else []
        return [parse_session_payload(s) for s in sessions or []]


def get_meow_engine_client() -> HttpMeowEngineClient | None:
    url = (os.getenv("NEXUS_WHATSAPP_MEOW_URL") or "").strip().rstrip("/")
    token = (os.getenv("NEXUS_WHATSAPP_INTERNAL_TOKEN") or "").strip()
    if not url:
        return None
    if not token:
        raise RuntimeError("NEXUS_WHATSAPP_INTERNAL_TOKEN required when MEOW_URL is set")
    return HttpMeowEngineClient(url, token)


def resolve_meow_engine():
    """HTTP a Go si hay URL; simulate local solo en tests/dev explícito sin URL."""
    http = get_meow_engine_client()
    if http is not None:
        return http
    meow_url = (os.getenv("NEXUS_WHATSAPP_MEOW_URL") or "").strip()
    if meow_url:
        raise RuntimeError(
            "NEXUS_WHATSAPP_MEOW_URL está configurado pero el servicio Go no responde. "
            "Levantá nexus-whatsapp (binario real, sin NEXUS_WHATSAPP_SIMULATE)."
        )
    from app.services.whatsapp_meow_simulate import get_local_simulate_engine

    simulate_ok = (os.getenv("NEXUS_WHATSAPP_SIMULATE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not simulate_ok and (os.getenv("NEXUS_WHATSAPP_REQUIRE_REAL") or "1").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        raise RuntimeError(
            "WhatsApp real requerido: configurá NEXUS_WHATSAPP_MEOW_URL y el servicio Go, "
            "o solo en tests activá NEXUS_WHATSAPP_SIMULATE=1."
        )
    return get_local_simulate_engine()
