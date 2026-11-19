"""Motor local espejo del modo SIMULATE de Go (tests y dev sin binario Go)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

from app.services.whatsapp_meow_contract import MeowEngineClient, MeowOutboundView, MeowSessionView


class LocalSimulateMeowEngine:
    """Thread-safe in-memory engine aligned with Go SimulateEngine."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, MeowSessionView] = {}
        self._sent: dict[tuple[str, str], MeowOutboundView] = {}

    def create_session(self, *, company_id: int, user_id: int, session_id: str) -> MeowSessionView:
        with self._lock:
            sid = session_id or str(uuid.uuid4())
            if sid in self._sessions:
                raise ValueError("session already exists")
            now = datetime.now(UTC)
            view = MeowSessionView(
                session_id=sid,
                company_id=company_id,
                user_id=user_id,
                status="qr_ready",
                phone=None,
                display_name=None,
                qr_code=f"SIM-{sid[:8]}",
                qr_expires_at=now + timedelta(minutes=2),
                last_error=None,
            )
            self._sessions[sid] = view
            return view

    def get_session(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise LookupError("session not found")
            if s.company_id != company_id or s.user_id != user_id:
                raise PermissionError("session tenant mismatch")
            return s

    def get_qr(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise LookupError("session not found")
            if s.company_id != company_id or s.user_id != user_id:
                raise PermissionError("session tenant mismatch")
            if s.status == "cancelled":
                raise ValueError("session cancelled")
            now = datetime.now(UTC)
            if s.qr_expires_at and now > s.qr_expires_at and s.status != "connected":
                s = MeowSessionView(
                    session_id=s.session_id,
                    company_id=s.company_id,
                    user_id=s.user_id,
                    status="qr_ready",
                    phone=s.phone,
                    display_name=s.display_name,
                    qr_code=f"SIM-{uuid.uuid4().hex[:8]}",
                    qr_expires_at=now + timedelta(minutes=2),
                    last_error=s.last_error,
                )
                self._sessions[session_id] = s
            return s

    def cancel_pairing(self, session_id: str, *, company_id: int, user_id: int) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise LookupError("session not found")
            if s.company_id != company_id or s.user_id != user_id:
                raise PermissionError("session tenant mismatch")
            self._sessions[session_id] = MeowSessionView(
                session_id=s.session_id,
                company_id=s.company_id,
                user_id=s.user_id,
                status="cancelled",
                phone=s.phone,
                display_name=s.display_name,
                qr_code=None,
                qr_expires_at=None,
                last_error=s.last_error,
            )

    def disconnect(self, session_id: str, *, company_id: int, user_id: int) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise LookupError("session not found")
            if s.company_id != company_id or s.user_id != user_id:
                raise PermissionError("session tenant mismatch")
            self._sessions[session_id] = MeowSessionView(
                session_id=s.session_id,
                company_id=s.company_id,
                user_id=s.user_id,
                status="disconnected",
                phone=None,
                display_name=s.display_name,
                qr_code=None,
                qr_expires_at=None,
                last_error=s.last_error,
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
        del session_id, company_id, user_id, to, state

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
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise LookupError("session not found")
            if s.company_id != company_id or s.user_id != user_id:
                raise PermissionError("session tenant mismatch")
            if s.status != "connected":
                raise ValueError("session not connected")
            if idempotency_key:
                prev = self._sent.get((session_id, idempotency_key))
                if prev:
                    return prev
            out = MeowOutboundView(
                whatsapp_msg_id=f"sim-out-{uuid.uuid4()}",
                status="sent",
            )
            if idempotency_key:
                self._sent[(session_id, idempotency_key)] = out
            return out

    def simulate_pair(self, session_id: str, *, company_id: int, user_id: int) -> MeowSessionView:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                raise LookupError("session not found")
            if s.company_id != company_id or s.user_id != user_id:
                raise PermissionError("session tenant mismatch")
            if s.status == "cancelled":
                raise ValueError("session cancelled")
            s = MeowSessionView(
                session_id=s.session_id,
                company_id=s.company_id,
                user_id=s.user_id,
                status="connected",
                phone="+5491100000000",
                display_name="Simulated Line",
                qr_code=None,
                qr_expires_at=None,
                last_error=None,
            )
            self._sessions[session_id] = s
            return s

    def list_tenant(self, company_id: int, user_id: int) -> list[MeowSessionView]:
        with self._lock:
            return [
                s
                for s in self._sessions.values()
                if s.company_id == company_id and s.user_id == user_id
            ]

    def ensure_session(self, *, company_id: int, user_id: int, session_id: str) -> MeowSessionView:
        return self.get_session(session_id, company_id=company_id, user_id=user_id)

    def register_session(self, view: MeowSessionView) -> None:
        """Restaura sesión en memoria (p. ej. tras reinicio del backend)."""
        with self._lock:
            self._sessions[view.session_id] = view


_local_singleton: LocalSimulateMeowEngine | None = None
_singleton_lock = threading.Lock()


def get_local_simulate_engine() -> LocalSimulateMeowEngine:
    global _local_singleton
    with _singleton_lock:
        if _local_singleton is None:
            _local_singleton = LocalSimulateMeowEngine()
        return _local_singleton


def reset_local_simulate_engine_for_tests() -> None:
    global _local_singleton
    with _singleton_lock:
        _local_singleton = LocalSimulateMeowEngine()
