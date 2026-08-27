"""
Política de secuencia LinkedIn — Conectar si hace falta, DM cuando ya son contacto.

Reglas:
- Si YA son 1º grado → mensaje DM directo (nunca pedir Conectar).
- Si no → tarea «Conectar»; al marcar Contactar enviado, Nexus deja el DM listo en cola
  (sin sondear aceptación: el SDR lo manda cuando acepte).
- El toque LinkedIn de la secuencia se completa al ENVIAR el DM, no al mandar la solicitud.
- Si no se envía el DM en 3 días (desde Contactar enviado) → se limpia la cola LI,
  conexión expirada, toques LinkedIn futuros se omiten y sigue otro canal.
- El primer toque no-LinkedIn tras fallo de conexión menciona el intento por LinkedIn.
- Al enviar el DM LinkedIn → se reinicia el reloj de secuencia desde ese envío.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.sequence_playbook import (
    PLAYBOOK_DAYS,
    is_touch_calendar_due,
    playbook_channel_for_day,
    resolve_touch_channel,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.campaign import Campaign
    from app.models.prospect import Prospect

CONNECT_WAIT_DAYS = 3
POST_CONNECT_DRAFT_TTL_DAYS = 3
# Toque LI/WA en cola asistida sin marcar enviado → omitir y seguir secuencia.
ASSISTED_QUEUE_TTL_DAYS = 3
# Verificación LinkedIn 1º/2º/3º: máximo 120s. Sin evidencia → check_failed (NO Contactar).
CHECKING_FALLBACK_SECONDS = 120

CONN_EXPIRED = "expired"

LINKEDIN_MENTION_INSTRUCTION = (
    "IMPORTANTE: En este mensaje mencioná de forma natural que también intentaste "
    "contactar al prospecto por LinkedIn sin éxito (una frase breve, sin sonar "
    "reprochoso). Es el primer contacto después de que no aceptaron la solicitud de conexión."
)


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _invite_sent_at(prospect: Prospect) -> datetime | None:
    raw = getattr(prospect, "linkedin_invite_sent_at", None)
    return _utc(raw) if raw else None


def _post_connect_draft_at(prospect: Prospect) -> datetime | None:
    raw = getattr(prospect, "linkedin_post_connect_draft_at", None)
    return _utc(raw) if raw else None


def connect_wait_deadline(prospect: Prospect) -> datetime | None:
    sent = _invite_sent_at(prospect)
    if sent is None:
        return None
    return sent + timedelta(days=CONNECT_WAIT_DAYS)


def is_connect_wait_expired(prospect: Prospect, *, now: datetime | None = None) -> bool:
    from app.services.linkedin_assisted_service import CONN_INVITE_SENT, read_connection_status

    if read_connection_status(prospect) != CONN_INVITE_SENT:
        return False
    deadline = connect_wait_deadline(prospect)
    if deadline is None:
        return False
    now = _utc(now or datetime.now(UTC))
    return now >= deadline


def is_post_connect_draft(prospect: Prospect) -> bool:
    return _post_connect_draft_at(prospect) is not None and bool(
        (prospect.linkedin_assisted_draft or "").strip()
    )


def linkedin_connect_failed(prospect: Prospect, *, now: datetime | None = None) -> bool:
    from app.services.linkedin_assisted_service import (
        CONN_DECLINED,
        CONN_EXPIRED,
        CONN_INVITE_SENT,
        read_connection_status,
    )

    status = read_connection_status(prospect)
    if status in (CONN_EXPIRED, CONN_DECLINED):
        return True
    if status == CONN_INVITE_SENT and is_connect_wait_expired(prospect, now=now):
        return True
    return False


def is_linkedin_connected(prospect: Prospect) -> bool:
    from app.services.linkedin_assisted_service import CONN_CONNECTED, read_connection_status

    return read_connection_status(prospect) == CONN_CONNECTED


def _planned_channel_for_day(prospect: Prospect, campaign: Campaign | None, day: int) -> str | None:
    if campaign is not None:
        from app.services.campaign_sequence_channels import effective_channel_for_day

        return effective_channel_for_day(campaign, day)
    return resolve_touch_channel(
        day,
        email=prospect.email,
        linkedin_url=prospect.linkedin_url,
        phone=prospect.phone,
        whatsapp_number=prospect.whatsapp,
        allowed_channels=None,
        channel_plan=None,
    )


def is_planned_linkedin_touch_day(prospect: Prospect, campaign: Campaign | None, day: int) -> bool:
    ch = _planned_channel_for_day(prospect, campaign, day)
    if ch == "linkedin":
        return True
    return playbook_channel_for_day(day) == "linkedin"


def remaining_linkedin_touch_days(
    prospect: Prospect,
    campaign: Campaign | None,
    *,
    now: datetime | None = None,
) -> list[int]:
    """Días LinkedIn del plan que aún no se completaron y ya son calendario-cumplidos o futuros."""
    from app.services.prospect_sequence import _completed_days

    now = now or datetime.now(UTC)
    done = _completed_days(prospect)
    out: list[int] = []
    for day in PLAYBOOK_DAYS:
        if day in done:
            continue
        if not is_planned_linkedin_touch_day(prospect, campaign, day):
            continue
        out.append(day)
    return out


def should_auto_omit_linkedin_touch(
    prospect: Prospect,
    campaign: Campaign | None,
    day: int,
    *,
    now: datetime | None = None,
) -> bool:
    """Omitir un toque LinkedIn cuando la conexión falló y no hay vínculo."""
    if not is_planned_linkedin_touch_day(prospect, campaign, day):
        return False
    if is_linkedin_connected(prospect):
        return False
    if not linkedin_connect_failed(prospect, now=now):
        return False
    if not is_touch_calendar_due(prospect.sequence_started_at, day, now=now):
        return False
    return True


def clear_linkedin_queue_draft(prospect: Prospect) -> None:
    from app.services.linkedin_assisted_service import STATUS_NONE, _set_assist_status, read_assist_status

    prospect.linkedin_assisted_draft = None
    prospect.linkedin_post_connect_draft_at = None
    prospect.linkedin_assist_session_id = None
    prospect.linkedin_last_assisted_at = None
    if read_assist_status(prospect) not in ("sent",):
        _set_assist_status(prospect, STATUS_NONE)


def expire_connect_invite(prospect: Prospect, *, now: datetime | None = None) -> bool:
    from app.services.linkedin_assisted_service import CONN_INVITE_SENT, read_connection_status

    if read_connection_status(prospect) != CONN_INVITE_SENT:
        return False
    if not is_connect_wait_expired(prospect, now=now):
        return False
    prospect.linkedin_connection_status = CONN_EXPIRED
    prospect.linkedin_mention_next_touch = True
    clear_linkedin_queue_draft(prospect)
    return True


def expire_post_connect_draft(prospect: Prospect, *, now: datetime | None = None) -> bool:
    if not is_post_connect_draft(prospect):
        return False
    draft_at = _post_connect_draft_at(prospect)
    if draft_at is None:
        return False
    now = _utc(now or datetime.now(UTC))
    if now < draft_at + timedelta(days=POST_CONNECT_DRAFT_TTL_DAYS):
        return False
    clear_linkedin_queue_draft(prospect)
    prospect.linkedin_post_connect_draft_at = None
    return True


def refresh_linkedin_sequence_state(
    prospect: Prospect,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Expira invitaciones/borradores; LI-SAFE no reencola checking/probe."""
    from app.services.linkedin_assisted_service import LI_SAFE_NO_PROFILE_PROBE

    events: list[str] = []
    # Legacy: invite_sent con borrador pero sin TTL → anclar al momento del Contactar.
    if _backfill_post_connect_draft_clock(prospect):
        events.append("post_connect_draft_clock_backfilled")
    if expire_post_connect_draft(prospect, now=now):
        events.append("post_connect_draft_expired")
    if expire_connect_invite(prospect, now=now):
        events.append("connect_expired")
    if LI_SAFE_NO_PROFILE_PROBE:
        from app.services.linkedin_assisted_service import _li_safe_clear_probe_status

        if _li_safe_clear_probe_status(prospect):
            events.append("li_safe_cleared_probe")
        return events
    if heal_unverified_invite_pending(prospect, now=now):
        events.append("healed_unverified_invite")
    if heal_none_with_linkedin_draft(prospect, now=now):
        events.append("healed_none_to_check")
    if promote_stale_connection_check(prospect, now=now):
        events.append("checking_timeout_failed")
    if revive_check_failed_for_retry(prospect, now=now):
        events.append("check_failed_requeued")
    return events


def _backfill_post_connect_draft_clock(prospect: Prospect) -> bool:
    from app.services.linkedin_assisted_service import CONN_INVITE_SENT, read_connection_status

    if read_connection_status(prospect) != CONN_INVITE_SENT:
        return False
    if not (prospect.linkedin_assisted_draft or "").strip():
        return False
    if _post_connect_draft_at(prospect) is not None:
        return False
    mark_post_connect_draft_prepared(prospect, now=_invite_sent_at(prospect) or datetime.now(UTC))
    return True


def heal_unverified_invite_pending(prospect: Prospect, *, now: datetime | None = None) -> bool:
    """
    Contactar en cola sin haber pasado por verificación → reencolar verificación.
    NO satura: va a check_queued (normalize promueve de a uno).
    Si ya hay borrador de DM, se deja en Contactar (legítimo / legacy).
    """
    from app.services.linkedin_assisted_service import (
        CONN_CHECK_QUEUED,
        CONN_INVITE_PENDING,
        read_connection_status,
    )

    if read_connection_status(prospect) != CONN_INVITE_PENDING:
        return False
    if getattr(prospect, "linkedin_invite_sent_at", None):
        return False
    # Ya hubo verificación (reloj de checking) → Contactar es legítimo.
    if getattr(prospect, "linkedin_last_assisted_at", None):
        return False
    # Borrador ya compuesto: no reabrir verificación en masa (rompe la cola real).
    if (getattr(prospect, "linkedin_assisted_draft", None) or "").strip():
        return False
    del now
    prospect.linkedin_connection_status = CONN_CHECK_QUEUED
    prospect.linkedin_last_assisted_at = None
    return True


def heal_none_with_linkedin_draft(prospect: Prospect, *, now: datetime | None = None) -> bool:
    """
    Borrador LinkedIn con connection_status=none (sin check) → reencolar verificación.
    Evita mostrar «Enviar mensaje» antes de saber si es 1º grado.
    """
    from app.services.linkedin_assisted_service import (
        CONN_CHECK_QUEUED,
        CONN_NONE,
        is_real_linkedin_profile_url,
        read_connection_status,
    )

    if read_connection_status(prospect) != CONN_NONE:
        return False
    if not is_real_linkedin_profile_url(getattr(prospect, "linkedin_url", None)):
        return False
    has_draft = bool((getattr(prospect, "linkedin_assisted_draft", None) or "").strip())
    assist = (getattr(prospect, "linkedin_assist_status", None) or "").strip().lower()
    if not has_draft and assist not in ("suggested", "prepared", "opened"):
        return False
    del now
    prospect.linkedin_connection_status = CONN_CHECK_QUEUED
    prospect.linkedin_last_assisted_at = None
    return True


def revive_check_failed_for_retry(prospect: Prospect, *, now: datetime | None = None) -> bool:
    """
    check_failed no puede quedar muerto: reencola verificación.
    El sondeo volverá a intentar; sin inventar Contactar.
    """
    from app.services.linkedin_assisted_service import (
        CONN_CHECK_FAILED,
        CONN_CHECK_QUEUED,
        read_connection_status,
    )

    if read_connection_status(prospect) != CONN_CHECK_FAILED:
        return False
    del now
    prospect.linkedin_connection_status = CONN_CHECK_QUEUED
    prospect.linkedin_last_assisted_at = None
    return True


def promote_stale_connection_check(prospect: Prospect, *, now: datetime | None = None) -> bool:
    """
    Si en 120s la extensión no leyó 1/2/3 → check_failed.
    NO inventa Contactar ni Mensaje: nada en cola operativa hasta verificar.
    """
    from app.services.linkedin_assisted_service import (
        CONN_CHECK_FAILED,
        CONN_CHECKING,
        read_connection_status,
    )

    if read_connection_status(prospect) != CONN_CHECKING:
        return False
    now = _utc(now or datetime.now(UTC))
    started = getattr(prospect, "linkedin_last_assisted_at", None)
    if started is None:
        prospect.linkedin_last_assisted_at = now
        return False
    if now < _utc(started) + timedelta(seconds=CHECKING_FALLBACK_SECONDS):
        return False
    prospect.linkedin_connection_status = CONN_CHECK_FAILED
    return True


def mark_post_connect_draft_prepared(prospect: Prospect, *, now: datetime | None = None) -> None:
    prospect.linkedin_post_connect_draft_at = _utc(now or datetime.now(UTC))


def clear_post_connect_draft_meta(prospect: Prospect) -> None:
    prospect.linkedin_post_connect_draft_at = None


def reset_sequence_clock_after_post_connect_dm(prospect: Prospect, sent_at: datetime) -> None:
    """Reancla sequence_started_at para que el próximo toque pendiente caiga 3 días después del envío."""
    from app.services.prospect_sequence import compute_next_touch, next_executable_day

    next_day = next_executable_day(prospect)
    if next_day is None:
        return
    sent = _utc(sent_at)
    prospect.sequence_started_at = sent + timedelta(days=3) - timedelta(days=max(0, next_day - 1))
    next_at, _ = compute_next_touch(prospect)
    prospect.next_touch_at = next_at


def linkedin_mention_context(prospect: Prospect, *, channel: str) -> str | None:
    if channel == "linkedin":
        return None
    if not getattr(prospect, "linkedin_mention_next_touch", False):
        return None
    return LINKEDIN_MENTION_INSTRUCTION


def consume_linkedin_mention_flag(prospect: Prospect) -> None:
    prospect.linkedin_mention_next_touch = False


def linkedin_touch_decision(
    prospect: Prospect,
    *,
    now: datetime | None = None,
) -> str:
    """
    Decisión rápida sin side-effects: connect | message | skip.
    Usar queue_linkedin_sequence_touch para encolar con efectos.
    """
    from app.services.linkedin_assisted_service import (
        CONN_INVITE_PENDING,
        CONN_INVITE_SENT,
        CONN_NONE,
        read_connection_status,
    )

    now = now or datetime.now(UTC)
    refresh_linkedin_sequence_state(prospect, now=now)
    status = read_connection_status(prospect)

    if is_linkedin_connected(prospect):
        return "message"
    # Contactar ya enviado: DM listo en cola (sin hold / sin sondear aceptación).
    if status == CONN_INVITE_SENT and not is_connect_wait_expired(prospect, now=now):
        return "message"
    if linkedin_connect_failed(prospect, now=now):
        return "skip"
    if status in (CONN_NONE, CONN_INVITE_PENDING):
        return "connect"
    return "skip"
