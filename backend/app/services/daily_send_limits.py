"""Límites diarios de envío por canal, por SDR (anti-bloqueo de cuentas).

Objetivo: proteger las cuentas del SDR (Gmail, LinkedIn, WhatsApp) para que la
automatización nunca supere volúmenes que disparen bloqueos.

Topes por defecto (por SDR, por día, UTC), configurables por env:

    email            → 300  (NEXUS_DAILY_LIMIT_EMAIL)
    linkedin_invite  → 40   (NEXUS_DAILY_LIMIT_LINKEDIN_INVITE)
    linkedin_dm      → 30   (NEXUS_DAILY_LIMIT_LINKEDIN_DM)
    whatsapp         → 20   (NEXUS_DAILY_LIMIT_WHATSAPP)

Los conteos son por `seller_id` de la campaña (las cuentas son del SDR), sumando
todas sus campañas del día.

Regla adicional WhatsApp: solo a prospectos "calificados" — que ya tuvieron
contacto previo por email o LinkedIn (nunca WhatsApp en frío).

Bonus WhatsApp: cada respuesta inbound hoy suma +1 al cupo de envíos del día
(p. ej. base 20 + 3 respuestas = 23 envíos posibles hoy).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.outreach import OutreachMessage
from app.models.prospect import Prospect

# Tipos de tope soportados.
KIND_EMAIL = "email"
KIND_LINKEDIN_INVITE = "linkedin_invite"
KIND_LINKEDIN_DM = "linkedin_dm"
KIND_WHATSAPP = "whatsapp"

DEFAULT_LIMITS: dict[str, int] = {
    # Día 1 de campaña debe poder contactar el volumen pedido el mismo día.
    KIND_EMAIL: 300,
    KIND_LINKEDIN_INVITE: 40,
    KIND_LINKEDIN_DM: 30,
    KIND_WHATSAPP: 20,
}

_ENV_KEYS: dict[str, str] = {
    KIND_EMAIL: "NEXUS_DAILY_LIMIT_EMAIL",
    KIND_LINKEDIN_INVITE: "NEXUS_DAILY_LIMIT_LINKEDIN_INVITE",
    KIND_LINKEDIN_DM: "NEXUS_DAILY_LIMIT_LINKEDIN_DM",
    KIND_WHATSAPP: "NEXUS_DAILY_LIMIT_WHATSAPP",
}


def limit_for(kind: str) -> int:
    """Tope diario para un tipo de canal (env override → default)."""
    raw = (os.getenv(_ENV_KEYS.get(kind, ""), "") or "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_LIMITS.get(kind, 0)


def _utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _seller_campaign_ids(db: Session, seller_id: int) -> list[int]:
    if not seller_id:
        return []
    return list(
        db.scalars(select(Campaign.id).where(Campaign.seller_id == seller_id)).all()
    )


def _outbound_count(
    db: Session,
    *,
    seller_id: int,
    channel: str,
    now: datetime | None = None,
) -> int:
    """Mensajes outbound de un canal enviados hoy por las campañas del SDR."""
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return 0
    start, end = _utc_day_bounds(now)
    n = db.scalar(
        select(func.count())
        .select_from(OutreachMessage)
        .where(
            OutreachMessage.campaign_id.in_(campaign_ids),
            OutreachMessage.channel == channel,
            OutreachMessage.direction == "outbound",
            OutreachMessage.created_at >= start,
            OutreachMessage.created_at < end,
        )
    )
    return int(n or 0)


def _inbound_count(
    db: Session,
    *,
    seller_id: int,
    channel: str,
    now: datetime | None = None,
) -> int:
    """Mensajes inbound de un canal recibidos hoy por las campañas del SDR."""
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return 0
    start, end = _utc_day_bounds(now)
    n = db.scalar(
        select(func.count())
        .select_from(OutreachMessage)
        .where(
            OutreachMessage.campaign_id.in_(campaign_ids),
            OutreachMessage.channel == channel,
            OutreachMessage.direction == "inbound",
            OutreachMessage.created_at >= start,
            OutreachMessage.created_at < end,
        )
    )
    return int(n or 0)


def whatsapp_inbounds_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Respuestas WhatsApp recibidas hoy — cada una habilita +1 envío extra."""
    return _inbound_count(db, seller_id=seller_id, channel="whatsapp", now=now)


def whatsapp_effective_limit_today(
    db: Session, seller_id: int, *, now: datetime | None = None
) -> int:
    """Cupo base + bonus por respuestas inbound hoy."""
    return limit_for(KIND_WHATSAPP) + whatsapp_inbounds_today(db, seller_id, now=now)


def emails_sent_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    return _outbound_count(db, seller_id=seller_id, channel="email", now=now)


def whatsapps_sent_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    return _outbound_count(db, seller_id=seller_id, channel="whatsapp", now=now)


def linkedin_dms_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    return _outbound_count(db, seller_id=seller_id, channel="linkedin", now=now)


def linkedin_invites_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Invitaciones de conexión enviadas hoy (campo Prospect.linkedin_invite_sent_at)."""
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return 0
    if not hasattr(Prospect, "linkedin_invite_sent_at"):
        return 0
    start, end = _utc_day_bounds(now)
    n = db.scalar(
        select(func.count())
        .select_from(Prospect)
        .where(
            Prospect.campaign_id.in_(campaign_ids),
            Prospect.linkedin_invite_sent_at.isnot(None),
            Prospect.linkedin_invite_sent_at >= start,
            Prospect.linkedin_invite_sent_at < end,
        )
    )
    return int(n or 0)


_COUNTERS = {
    KIND_EMAIL: emails_sent_today,
    KIND_WHATSAPP: whatsapps_sent_today,
    KIND_LINKEDIN_DM: linkedin_dms_today,
    KIND_LINKEDIN_INVITE: linkedin_invites_today,
}


def used_today(db: Session, seller_id: int, kind: str, *, now: datetime | None = None) -> int:
    counter = _COUNTERS.get(kind)
    if counter is None:
        return 0
    return counter(db, seller_id, now=now)


def remaining(db: Session, seller_id: int, kind: str, *, now: datetime | None = None) -> int:
    """Cupo restante hoy para (SDR, tipo de canal). Nunca negativo."""
    used = used_today(db, seller_id, kind, now=now)
    if kind == KIND_WHATSAPP:
        lim = whatsapp_effective_limit_today(db, seller_id, now=now)
    else:
        lim = limit_for(kind)
    return max(0, lim - used)


def can_send(db: Session, seller_id: int, kind: str, *, now: datetime | None = None) -> bool:
    return remaining(db, seller_id, kind, now=now) > 0


# ——— Regla de "WhatsApp calificado" ———

def whatsapp_qualified(db: Session, prospect: Prospect) -> bool:
    """
    True si el prospecto ya tuvo contacto previo por email o LinkedIn.

    Evita WhatsApp en frío: solo se contacta por WhatsApp a quien ya recibió
    (o respondió) email/LinkedIn. En el playbook Nexus esto se cumple naturalmente
    (email día 1, LinkedIn día 4 anteceden al WhatsApp día 7), pero lo validamos
    de forma explícita para secuencias personalizadas.
    """
    n = db.scalar(
        select(func.count())
        .select_from(OutreachMessage)
        .where(
            OutreachMessage.prospect_id == prospect.id,
            OutreachMessage.channel.in_(("email", "linkedin")),
        )
    )
    return int(n or 0) > 0


def snapshot(db: Session, seller_id: int, *, now: datetime | None = None) -> dict[str, dict[str, int]]:
    """Estado de todos los topes para un SDR (para UI/diagnóstico)."""
    out: dict[str, dict[str, int]] = {}
    for kind in DEFAULT_LIMITS:
        used = used_today(db, seller_id, kind, now=now)
        lim = limit_for(kind)
        row: dict[str, int] = {
            "used": used,
            "limit": lim,
            "remaining": max(0, lim - used),
        }
        if kind == KIND_WHATSAPP:
            bonus = whatsapp_inbounds_today(db, seller_id, now=now)
            effective = lim + bonus
            row["bonus_from_replies"] = bonus
            row["effective_limit"] = effective
            row["remaining"] = max(0, effective - used)
        out[kind] = row
    return out
