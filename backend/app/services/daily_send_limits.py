"""Límites de envío por canal, por SDR (anti-bloqueo de cuentas).

Objetivo: proteger las cuentas del SDR (Gmail, LinkedIn, WhatsApp) para que la
automatización nunca supere volúmenes que disparen bloqueos.

Topes diarios por defecto (por SDR, últimas 24 h como cuenta Google), configurables por env:

    email            → 400  (NEXUS_DAILY_LIMIT_EMAIL) — Gmail común corta en 500
    linkedin_invite  → 50   (NEXUS_DAILY_LIMIT_LINKEDIN_INVITE)
    linkedin_dm      → 50   (NEXUS_DAILY_LIMIT_LINKEDIN_DM) — mensajes a conectados
    whatsapp         → 45   (NEXUS_DAILY_LIMIT_WHATSAPP) — personas en frío/día
    whatsapp_fu      → 200  (NEXUS_DAILY_LIMIT_WHATSAPP_FOLLOWUP)
    inbound WA       → sin tope (quien ya respondió)

Topes de cada aplicación, por encima de los diarios:

    LinkedIn  → 100 invitaciones cada 7 días (NEXUS_WEEKLY_LIMIT_LINKEDIN_INVITE)
    WhatsApp  → 150 mensajes por mes calendario a quien todavía no respondió
                (NEXUS_MONTHLY_LIMIT_WHATSAPP_UNANSWERED). Es el tope de WhatsApp común
                desde 2026-10 (Business: 600); no sabemos qué app usa cada SDR.

Los conteos son por `seller_id` de la campaña (las cuentas son del SDR), sumando
todas sus campañas.

Regla adicional WhatsApp: solo a prospectos "calificados" — que ya tuvieron
contacto previo por email o LinkedIn (nunca WhatsApp en frío).

Bonus WhatsApp: cada respuesta inbound hoy suma +1 al cupo de envíos del día
(p. ej. base 20 + 3 respuestas = 23 envíos posibles hoy).

Email — cuidado de la casilla (además del tope diario):

    Arranque gradual → 20 el primer día de envíos Nexus, +20 por día hasta el tope
                       (NEXUS_EMAIL_RAMP_START / NEXUS_EMAIL_RAMP_STEP). Si la casilla ya
                       mandaba volumen antes de Nexus, arranca desde ese promedio diario.
    Freno            → nivel 0-3 en `mailbox_health` (lo sube/baja `mailbox_health`
                       por rebotes o caída de respuestas): multiplica el cupo.
    Ritmo            → envíos automáticos espaciados (cupo repartido en el horario
                       laboral, con variación al azar). Los envíos manuales no esperan.
"""

from __future__ import annotations

import math
import os
import random
from datetime import UTC, datetime, timedelta
from typing import Any

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
    KIND_EMAIL: 400,
    KIND_LINKEDIN_INVITE: 50,
    KIND_LINKEDIN_DM: 50,
    KIND_WHATSAPP: 45,
}

DEFAULT_WHATSAPP_FOLLOWUP_LIMIT = 200
_ENV_FOLLOWUP = "NEXUS_DAILY_LIMIT_WHATSAPP_FOLLOWUP"

_ENV_KEYS: dict[str, str] = {
    KIND_EMAIL: "NEXUS_DAILY_LIMIT_EMAIL",
    KIND_LINKEDIN_INVITE: "NEXUS_DAILY_LIMIT_LINKEDIN_INVITE",
    KIND_LINKEDIN_DM: "NEXUS_DAILY_LIMIT_LINKEDIN_DM",
    KIND_WHATSAPP: "NEXUS_DAILY_LIMIT_WHATSAPP",
}

DEFAULT_WEEKLY_LINKEDIN_INVITES = 100
DEFAULT_MONTHLY_WHATSAPP_UNANSWERED = 150


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    return int(raw) if raw.isdigit() else default


def limit_for(kind: str) -> int:
    """Tope diario para un tipo de canal (env override → default)."""
    return _env_int(_ENV_KEYS.get(kind, ""), DEFAULT_LIMITS.get(kind, 0))


def weekly_linkedin_invite_limit() -> int:
    return _env_int("NEXUS_WEEKLY_LIMIT_LINKEDIN_INVITE", DEFAULT_WEEKLY_LINKEDIN_INVITES)


def monthly_whatsapp_unanswered_limit() -> int:
    return _env_int("NEXUS_MONTHLY_LIMIT_WHATSAPP_UNANSWERED", DEFAULT_MONTHLY_WHATSAPP_UNANSWERED)


def _now_utc(now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now


def _utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Últimas 24 h: así cuentan Gmail y LinkedIn (no se resetea a medianoche)."""
    now = _now_utc(now)
    return now - timedelta(days=1), now + timedelta(seconds=1)


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
    conds = [
        OutreachMessage.campaign_id.in_(campaign_ids),
        OutreachMessage.channel == channel,
        OutreachMessage.direction == "outbound",
        OutreachMessage.created_at >= start,
        OutreachMessage.created_at < end,
    ]
    if channel == "email":
        # Borradores y notas no salieron: solo cuenta lo que Gmail envió.
        conds.append(OutreachMessage.gmail_message_id.isnot(None))
    n = db.scalar(select(func.count()).select_from(OutreachMessage).where(*conds))
    return int(n or 0)


def linkedin_invites_last_7_days(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids or not hasattr(Prospect, "linkedin_invite_sent_at"):
        return 0
    now = _now_utc(now)
    n = db.scalar(
        select(func.count())
        .select_from(Prospect)
        .where(
            Prospect.campaign_id.in_(campaign_ids),
            Prospect.linkedin_invite_sent_at.isnot(None),
            Prospect.linkedin_invite_sent_at >= now - timedelta(days=7),
        )
    )
    return int(n or 0)


def whatsapp_unanswered_this_month(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Mensajes WhatsApp de este mes a prospectos que nunca respondieron por WhatsApp."""
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return 0
    now = _now_utc(now)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    replied = (
        select(OutreachMessage.prospect_id)
        .where(
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "inbound",
        )
        .scalar_subquery()
    )
    n = db.scalar(
        select(func.count())
        .select_from(OutreachMessage)
        .where(
            OutreachMessage.campaign_id.in_(campaign_ids),
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "outbound",
            OutreachMessage.created_at >= month_start,
            OutreachMessage.prospect_id.not_in(replied),
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


def whatsapp_followup_limit() -> int:
    return max(0, _env_int(_ENV_FOLLOWUP, DEFAULT_WHATSAPP_FOLLOWUP_LIMIT))


def _whatsapp_first_outbound_subq(campaign_ids: list[int]):
    return (
        select(
            OutreachMessage.prospect_id.label("prospect_id"),
            func.min(OutreachMessage.created_at).label("first_at"),
        )
        .where(
            OutreachMessage.campaign_id.in_(campaign_ids),
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "outbound",
        )
        .group_by(OutreachMessage.prospect_id)
        .subquery()
    )


def whatsapp_cold_people_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Personas cuyo primer WhatsApp salió hoy (contacto en frío)."""
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return 0
    start, end = _utc_day_bounds(now)
    first_out = _whatsapp_first_outbound_subq(campaign_ids)
    n = db.scalar(
        select(func.count())
        .select_from(first_out)
        .where(first_out.c.first_at >= start, first_out.c.first_at < end)
    )
    return int(n or 0)


def whatsapp_followup_people_today(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Personas con follow-up WhatsApp hoy (ya tenían un envío previo)."""
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return 0
    start, end = _utc_day_bounds(now)
    first_out = _whatsapp_first_outbound_subq(campaign_ids)
    n = db.scalar(
        select(func.count(func.distinct(OutreachMessage.prospect_id)))
        .select_from(OutreachMessage)
        .join(first_out, first_out.c.prospect_id == OutreachMessage.prospect_id)
        .where(
            OutreachMessage.campaign_id.in_(campaign_ids),
            OutreachMessage.channel == "whatsapp",
            OutreachMessage.direction == "outbound",
            OutreachMessage.created_at >= start,
            OutreachMessage.created_at < end,
            first_out.c.first_at < start,
        )
    )
    return int(n or 0)


def remaining_whatsapp_followups(
    db: Session, seller_id: int, *, now: datetime | None = None
) -> int:
    return max(0, whatsapp_followup_limit() - whatsapp_followup_people_today(db, seller_id, now=now))


def whatsapp_effective_limit_today(
    db: Session, seller_id: int, *, now: datetime | None = None
) -> int:
    """Tope de personas en frío hoy (las respuestas inbound no suman ni restan)."""
    del db, seller_id, now
    return limit_for(KIND_WHATSAPP)


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


def remaining(
    db: Session, seller_id: int, kind: str, *, now: datetime | None = None, cold: bool = True
) -> int:
    """Cupo restante hoy para (SDR, tipo de canal). Nunca negativo.

    WhatsApp `cold=False` = ya respondieron: charla ilimitada.
    WhatsApp `cold=True` = personas nuevas hoy (tope 45) + tope mensual sin respuesta.
    """
    if kind == KIND_WHATSAPP and not cold:
        return 10**9
    used = (
        whatsapp_cold_people_today(db, seller_id, now=now)
        if kind == KIND_WHATSAPP
        else used_today(db, seller_id, kind, now=now)
    )
    if kind == KIND_WHATSAPP:
        lim = limit_for(KIND_WHATSAPP)
    elif kind == KIND_EMAIL:
        lim = int(email_cap_today(db, seller_id, now=now)["cap"])
    else:
        lim = limit_for(kind)
    left = max(0, lim - used)
    if kind == KIND_LINKEDIN_INVITE:
        left = min(left, max(0, weekly_linkedin_invite_limit() - linkedin_invites_last_7_days(db, seller_id, now=now)))
    if kind == KIND_WHATSAPP and cold:
        left = min(
            left,
            max(0, monthly_whatsapp_unanswered_limit() - whatsapp_unanswered_this_month(db, seller_id, now=now)),
        )
    return left


# ——— Email: arranque gradual, freno y ritmo ———

DEFAULT_EMAIL_RAMP_START = 20
DEFAULT_EMAIL_RAMP_STEP = 20
BRAKE_FACTORS: dict[int, float] = {0: 1.0, 1: 0.5, 2: 0.25, 3: 0.1}
MIN_BRAKED_EMAIL_CAP = 10
MIN_EMAIL_GAP_SECONDS = 45


def _flag_on(name: str) -> bool:
    return (os.getenv(name) or "1").strip().lower() not in ("0", "false", "no", "off")


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _seller_email_sent_conds(campaign_ids: list[int]) -> list[Any]:
    return [
        OutreachMessage.campaign_id.in_(campaign_ids),
        OutreachMessage.channel == "email",
        OutreachMessage.direction == "outbound",
        OutreachMessage.gmail_message_id.isnot(None),
    ]


def first_nexus_email_at(db: Session, seller_id: int) -> datetime | None:
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return None
    first = db.scalar(
        select(func.min(OutreachMessage.created_at)).where(*_seller_email_sent_conds(campaign_ids))
    )
    return _aware(first)


def _mailbox_health_row(db: Session, seller_id: int) -> Any:
    from app.models.mailbox_health import MailboxHealth

    return db.scalars(select(MailboxHealth).where(MailboxHealth.seller_id == seller_id)).first()


def email_cap_today(db: Session, seller_id: int, *, now: datetime | None = None) -> dict[str, Any]:
    """Cupo de email de hoy para la casilla del SDR, con el detalle de por qué."""
    now = _now_utc(now)
    base = limit_for(KIND_EMAIL)
    health = _mailbox_health_row(db, seller_id) if seller_id else None
    level = int(getattr(health, "brake_level", 0) or 0) if health is not None else 0
    level = max(0, min(3, level))

    ramp_cap = base
    ramp_day = None
    if _flag_on("NEXUS_EMAIL_RAMP_ENABLED") and seller_id:
        start = _env_int("NEXUS_EMAIL_RAMP_START", DEFAULT_EMAIL_RAMP_START)
        step = _env_int("NEXUS_EMAIL_RAMP_STEP", DEFAULT_EMAIL_RAMP_STEP)
        prior = int(getattr(health, "prior_daily_sent", 0) or 0) if health is not None else 0
        start = max(start, min(prior, base))
        first = first_nexus_email_at(db, seller_id)
        days = max(0, (now - first).days) if first is not None else 0
        ramp_day = days + 1
        ramp_cap = min(base, start + step * days)

    cap = ramp_cap
    if level:
        cap = max(min(MIN_BRAKED_EMAIL_CAP, ramp_cap), int(ramp_cap * BRAKE_FACTORS[level]))
    return {
        "cap": cap,
        "base": base,
        "ramp_cap": ramp_cap,
        "ramp_day": ramp_day,
        "ramping": ramp_cap < base,
        "brake_level": level,
        "brake_reason": (getattr(health, "brake_reason", None) or None) if level else None,
    }


def _last_email_sent(db: Session, seller_id: int) -> tuple[int, datetime] | None:
    campaign_ids = _seller_campaign_ids(db, seller_id)
    if not campaign_ids:
        return None
    row = db.execute(
        select(OutreachMessage.id, OutreachMessage.created_at)
        .where(*_seller_email_sent_conds(campaign_ids))
        .order_by(OutreachMessage.created_at.desc(), OutreachMessage.id.desc())
        .limit(1)
    ).first()
    if row is None or row[1] is None:
        return None
    return int(row[0]), _aware(row[1])


def email_gap_seconds(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Separación objetivo entre mails automáticos: cupo repartido en el horario laboral."""
    from app.services.send_window import window_bounds

    start_h, end_h = window_bounds()
    window_sec = max(1, end_h - start_h) * 3600
    cap = max(1, int(email_cap_today(db, seller_id, now=now)["cap"]))
    return max(MIN_EMAIL_GAP_SECONDS, int(window_sec / cap))


def email_pacing_wait_seconds(db: Session, seller_id: int, *, now: datetime | None = None) -> int:
    """Segundos a esperar antes del próximo mail automático (0 = puede salir ya)."""
    if not seller_id or not _flag_on("NEXUS_EMAIL_PACING_ENABLED"):
        return 0
    now = _now_utc(now)
    last = _last_email_sent(db, seller_id)
    if last is None:
        return 0
    last_id, last_at = last
    jitter = random.Random(f"{seller_id}:{last_id}").uniform(0.6, 1.4)
    gap = max(MIN_EMAIL_GAP_SECONDS, email_gap_seconds(db, seller_id, now=now) * jitter)
    wait = gap - (now - last_at).total_seconds()
    return max(0, math.ceil(wait))


def email_auto_send_ready(db: Session, seller_id: int, *, now: datetime | None = None) -> bool:
    """Envío automático de email: hay cupo hoy y ya pasó la separación desde el último."""
    return can_send(db, seller_id, KIND_EMAIL, now=now) and email_pacing_wait_seconds(
        db, seller_id, now=now
    ) == 0


def email_address_bounced(db: Session, email: str | None) -> bool:
    from app.models.mailbox_health import EmailBounce

    em = (email or "").strip().lower()
    if not em:
        return False
    n = db.scalar(
        select(func.count())
        .select_from(EmailBounce)
        .where(func.lower(EmailBounce.email) == em, EmailBounce.prospect_id.isnot(None))
    )
    return int(n or 0) > 0


def whatsapp_monthly_cap_reached(db: Session, seller_id: int, *, now: datetime | None = None) -> bool:
    return whatsapp_unanswered_this_month(db, seller_id, now=now) >= monthly_whatsapp_unanswered_limit()


def can_send(
    db: Session, seller_id: int, kind: str, *, now: datetime | None = None, cold: bool = True
) -> bool:
    return remaining(db, seller_id, kind, now=now, cold=cold) > 0


# ——— Regla de "WhatsApp calificado" ———

def whatsapp_qualified(db: Session, prospect: Prospect) -> bool:
    """
    True si el prospecto ya tuvo contacto previo por email o LinkedIn.

    Aplica al envío por **WhatsApp Business API** (Gupshup / Meta Cloud), no al
    número nativo Meow conectado del vendedor. En el playbook estándar el email
    y LinkedIn anteceden al WhatsApp; en secuencias custom con WA primero se usa Meow.
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
            row["used"] = whatsapp_cold_people_today(db, seller_id, now=now)
            row["limit"] = lim
            row["effective_limit"] = lim
            row["followups_used"] = whatsapp_followup_people_today(db, seller_id, now=now)
            row["followups_limit"] = whatsapp_followup_limit()
            row["month_unanswered_used"] = whatsapp_unanswered_this_month(db, seller_id, now=now)
            row["month_unanswered_limit"] = monthly_whatsapp_unanswered_limit()
        if kind == KIND_EMAIL:
            row["effective_limit"] = int(email_cap_today(db, seller_id, now=now)["cap"])
        if kind == KIND_LINKEDIN_INVITE:
            row["week_used"] = linkedin_invites_last_7_days(db, seller_id, now=now)
            row["week_limit"] = weekly_linkedin_invite_limit()
        row["remaining"] = remaining(db, seller_id, kind, now=now)
        out[kind] = row
    return out
