"""Filtros tempranos: solo chats 1:1 comercialmente autorizados entran al motor Nexus."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.whatsapp_meow import WhatsappMeowSession

if TYPE_CHECKING:
    from app.services.whatsapp_meow_authorization import MeowInboundAuthorization

logger = logging.getLogger(__name__)

_GROUP_SERVERS = frozenset({"g.us"})
_BROADCAST_SERVERS = frozenset({"broadcast"})
_NEWSLETTER_SERVERS = frozenset({"newsletter"})
_INDIVIDUAL_SERVERS = frozenset({"s.whatsapp.net", "whatsapp.net", "lid"})

# Go/whatsmeow a veces manda hora local (ART) etiquetada como UTC. Un inbound
# vivo a las 21:18 ART llega como 21:18Z y cae "antes" de connected_at/first_out.
# Margen amplio: husos ±12h + reloj desfasado + delay de outbox.
_CLOCK_SKEW_RESCUE = timedelta(hours=36)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _peer_server(peer: str) -> str:
    raw = (peer or "").strip().lower()
    if "@" not in raw:
        return ""
    return raw.rsplit("@", 1)[-1]


def peer_blocks_commercial(from_peer: str) -> str | None:
    """Razón de bloqueo por JID; None = no bloqueado por tipo de chat."""
    peer = (from_peer or "").strip()
    if not peer:
        return "invalid_sender"
    lower = peer.lower()
    server = _peer_server(peer)
    if server in _GROUP_SERVERS or lower.endswith("@g.us"):
        return "group_chat"
    if server in _BROADCAST_SERVERS or "status@broadcast" in lower:
        return "broadcast_chat"
    if server in _NEWSLETTER_SERVERS:
        return "newsletter_chat"
    if server and server not in _INDIVIDUAL_SERVERS:
        return "unsupported_peer"
    return None


def _parse_inbound_timestamp(raw: str | None) -> datetime | None:
    if not (raw or "").strip():
        return None
    text = raw.strip()
    # Epoch seconds / millis (string) from Go.
    if text.isdigit():
        try:
            n = int(text)
        except ValueError:
            return None
        if n > 10_000_000_000:  # millis
            n //= 1000
        try:
            return datetime.fromtimestamp(n, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _live_callback_skew_rescue(
    msg_ts: datetime,
    *,
    received_at: datetime | None = None,
) -> bool:
    """True si el stamp parece 'ahora' bajo sesgo local↔UTC (no dump histórico)."""
    recv = _as_utc(received_at or datetime.now(UTC))
    return msg_ts >= (recv - _CLOCK_SKEW_RESCUE)


def is_pre_connection_historical_sync(
    session_row: WhatsappMeowSession,
    *,
    inbound_timestamp: str | None,
    received_at: datetime | None = None,
) -> bool:
    """Mensajes anteriores a la conexión de la sesión (sync/historial whatsmeow).

    Importante: el stamp de WA/Go suele venir como hora local etiquetada UTC, así que
    un inbound VIVO parece 'histórico'. Solo descartamos si el stamp es claramente
    viejo vs el momento del webhook (días), no vs connected_at.
    """
    msg_ts = _parse_inbound_timestamp(inbound_timestamp)
    if msg_ts is None:
        return False
    recv = _as_utc(received_at or datetime.now(UTC))
    # Dump real de historial: stamp semanas/días atrás respecto del webhook.
    if msg_ts >= (recv - timedelta(days=2)):
        return False
    connected = session_row.connected_at
    if connected is None:
        return True
    connected = _as_utc(connected)
    return msg_ts < (connected - timedelta(minutes=5))


def first_nexus_whatsapp_outbound_at(db: Session, prospect_id: int) -> datetime | None:
    """Primer toque WhatsApp saliente de Nexus a este prospecto (si hubo)."""
    from sqlalchemy import select

    from app.models.outreach import OutreachMessage

    created = db.scalars(
        select(OutreachMessage.created_at)
        .where(
            OutreachMessage.prospect_id == int(prospect_id),
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "outbound",
        )
        .order_by(OutreachMessage.created_at.asc())
        .limit(1)
    ).first()
    if created is None:
        return None
    if isinstance(created, datetime) and created.tzinfo is None:
        return created.replace(tzinfo=UTC)
    return created


def is_pre_outbound_whatsapp_history(
    db: Session,
    *,
    prospect_id: int,
    inbound_timestamp: str | None,
    received_at: datetime | None = None,
) -> bool:
    """Mensaje del chat anterior al primer outbound WA de Nexus → no es respuesta."""
    msg_ts = _parse_inbound_timestamp(inbound_timestamp)
    if msg_ts is None:
        return False
    first_out = first_nexus_whatsapp_outbound_at(db, prospect_id)
    if first_out is None:
        return False
    first_out = _as_utc(first_out)
    if msg_ts >= (first_out - timedelta(seconds=10)):
        return False
    # Mismo sesgo local/UTC: stamp "antes" del 1º out pero cerca del webhook → vivo.
    if _live_callback_skew_rescue(msg_ts, received_at=received_at):
        return False
    return True


@dataclass(frozen=True)
class MeowInboundGate:
    accept: bool
    reason: str
    authorization: MeowInboundAuthorization | None = None


def gate_meow_inbound_commercial(
    db: Session,
    *,
    session_row: WhatsappMeowSession,
    from_peer: str,
    body: str,
    inbound_timestamp: str | None = None,
    received_at: datetime | None = None,
) -> MeowInboundGate:
    """Decide si persistir y procesar el inbound (solo contactos 1:1 autorizados)."""
    from app.services.whatsapp_meow_authorization import authorize_meow_inbound_commercial

    block = peer_blocks_commercial(from_peer)
    if block:
        return MeowInboundGate(False, block, None)

    # Nota: NO filtramos historical_sync / pre_outbound en el webhook live.
    # Los stamps de WA/Go son unreliable (local-as-UTC) y ya tiraron respuestas
    # reales. El filtro de grupos/broadcast y la autorización de contacto bastan;
    # el historial viejo en reconnect queda mitigado por authorize (secuencia).
    _ = (inbound_timestamp, received_at, session_row)  # API estable

    auth = authorize_meow_inbound_commercial(
        db,
        session_row=session_row,
        from_peer=from_peer,
        body=body,
    )
    if not auth.allowed:
        return MeowInboundGate(False, auth.reason, auth)
    return MeowInboundGate(True, "ok", auth)


def log_meow_inbound_excluded(
    *,
    reason: str,
    session_id: str,
    from_peer: str,
    whatsapp_msg_id: str | None,
) -> None:
    logger.info(
        "meow inbound excluded reason=%s session=%s peer=%s whatsapp_msg_id=%s",
        reason,
        session_id,
        from_peer,
        whatsapp_msg_id,
    )
