"""Bloqueo de secuencia por integración/extensión vs dato de contacto faltante."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.schemas.campaign_channels import coerce_allowed_channels
from app.core.sequence_playbook import is_touch_calendar_due

BLOCK_KIND = "integration_block"
BLOCK_RESOLVED_KIND = "integration_block_resolved"

_CHANNEL_LABELS = {
    "linkedin": "LinkedIn",
    "whatsapp": "WhatsApp",
    "email": "email",
}


def channel_label(channel: str | None) -> str:
    ch = (channel or "").strip().lower()
    return _CHANNEL_LABELS.get(ch, ch or "canal")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _seller_account(
    db: Session, *, company_id: int, user_id: int, provider: str
) -> ConnectedAccount | None:
    return db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.company_id == company_id,
            ConnectedAccount.user_id == user_id,
            ConnectedAccount.provider == provider,
        )
    ).first()


def seller_channel_block(
    db: Session,
    campaign: Campaign,
    channel: str | None,
) -> dict[str, Any] | None:
    """
    Si el canal del plan requiere una integración/extensión que no está lista,
    devolver bloqueo exacto. None = se puede intentar el toque.
    No confundir con «sin dato de contacto» (eso omite el día).
    """
    ch = (channel or "").strip().lower()
    if ch not in ("linkedin", "whatsapp", "email"):
        return None
    if not campaign.seller_id:
        return {
            "channel": ch,
            "code": "no_seller",
            "error": "La campaña no tiene vendedor asignado para ejecutar la secuencia.",
            "action": "assign_seller",
        }

    company_id = int(campaign.company_id)
    seller_id = int(campaign.seller_id)

    if ch == "linkedin":
        row = _seller_account(
            db,
            company_id=company_id,
            user_id=seller_id,
            provider=IntegrationProvider.linkedin.value,
        )
        status = (row.status if row else "") or ""
        status = status.strip().lower()
        # Sin fila / not_connected: no bloqueamos de antemano (la extensión puede
        # funcionar igual). Solo hold si Integraciones marca error o sin extensión.
        if not status or status in (
            IntegrationStatus.extension_connected.value,
            IntegrationStatus.not_connected.value,
            IntegrationStatus.connected.value,
        ):
            return None
        if status == IntegrationStatus.extension_not_installed.value:
            error = (
                "Extensión Nexus de LinkedIn no instalada o no conectada. "
                "Instalá/reactivá la extensión y reconectá LinkedIn en Integraciones."
            )
        else:
            error = (
                "LinkedIn (extensión Nexus) necesita reconexión. "
                f"Estado: {status}. Reconectá la extensión en Integraciones."
            )
        return {
            "channel": "linkedin",
            "code": "extension_disconnected",
            "error": error,
            "action": "reconnect_extension",
            "status": status,
        }

    if ch == "whatsapp":
        row = _seller_account(
            db,
            company_id=company_id,
            user_id=seller_id,
            provider=IntegrationProvider.whatsapp.value,
        )
        status = (row.status if row else "") or ""
        status = status.strip().lower()
        if not status or status in (
            IntegrationStatus.connected.value,
            IntegrationStatus.extension_connected.value,
            IntegrationStatus.not_connected.value,
        ):
            return None
        if status == IntegrationStatus.extension_not_installed.value:
            error = (
                "Extensión Nexus de WhatsApp no instalada o no conectada. "
                "Instalá/reactivá la extensión y reconectá WhatsApp en Integraciones."
            )
        else:
            error = (
                "WhatsApp (extensión Nexus) necesita reconexión. "
                f"Estado: {status}. Reconectá WhatsApp en Integraciones."
            )
        return {
            "channel": "whatsapp",
            "code": "extension_disconnected",
            "error": error,
            "action": "reconnect_extension",
            "status": status,
        }

    # email
    try:
        from app.services.gmail_drafts import get_valid_gmail_connection

        get_valid_gmail_connection(db, company_id=company_id, user_id=seller_id)
        return None
    except Exception as exc:  # noqa: BLE001
        detail = str(getattr(exc, "detail", None) or exc).strip() or str(exc)
        return {
            "channel": "email",
            "code": "gmail_disconnected",
            "error": (
                detail
                if "Gmail" in detail or "Google" in detail or "reconect" in detail.lower()
                else (
                    "Gmail del vendedor no está conectado o el token venció. "
                    "Reconectá Google en Integraciones."
                )
            ),
            "action": "reconnect_gmail",
            "status": IntegrationStatus.error.value,
        }


def read_campaign_integration_block(campaign: Campaign) -> dict[str, Any] | None:
    log = getattr(campaign, "outreach_activity_log", None)
    if not isinstance(log, list):
        return None
    for entry in reversed(log):
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if kind == BLOCK_RESOLVED_KIND:
            return None
        if kind == BLOCK_KIND:
            channel = str(entry.get("channel") or "").strip().lower()
            error = str(entry.get("error") or entry.get("message") or "").strip()
            if not channel or not error:
                continue
            return {
                "channel": channel,
                "code": str(entry.get("code") or "extension_disconnected"),
                "error": error,
                "action": str(entry.get("action") or "reconnect_extension"),
                "at": entry.get("at"),
                "blocked_prospects": int(entry.get("blocked_prospects") or 0),
            }
    return None


def set_campaign_integration_block(
    campaign: Campaign,
    block: dict[str, Any],
    *,
    blocked_prospects: int = 0,
) -> None:
    from app.services import multichannel_sequence as mseq

    channel = str(block.get("channel") or "").strip().lower()
    error = str(block.get("error") or "").strip()
    if not channel or not error:
        return
    existing = read_campaign_integration_block(campaign)
    if (
        existing
        and existing.get("channel") == channel
        and existing.get("error") == error
        and int(existing.get("blocked_prospects") or 0) == int(blocked_prospects or 0)
    ):
        return
    label = channel_label(channel)
    mseq._append_log(
        campaign,
        f"Secuencia en espera · {label}: {error}",
        kind=BLOCK_KIND,
    )
    log = list(getattr(campaign, "outreach_activity_log", None) or [])
    if log and isinstance(log[-1], dict) and log[-1].get("kind") == BLOCK_KIND:
        log[-1] = {
            **log[-1],
            "channel": channel,
            "code": block.get("code") or "extension_disconnected",
            "error": error,
            "action": block.get("action") or "reconnect_extension",
            "blocked_prospects": int(blocked_prospects or 0),
            "at": log[-1].get("at") or _now_iso(),
        }
        campaign.outreach_activity_log = log


def clear_campaign_integration_block(
    campaign: Campaign,
    *,
    channel: str | None = None,
    note: str | None = None,
) -> None:
    from app.services import multichannel_sequence as mseq

    ch = (channel or "").strip().lower() or None
    label = channel_label(ch) if ch else "canal"
    msg = note or f"Bloqueo de {label} resuelto."
    mseq._append_log(campaign, msg, kind=BLOCK_RESOLVED_KIND)
    log = list(getattr(campaign, "outreach_activity_log", None) or [])
    if log and isinstance(log[-1], dict) and log[-1].get("kind") == BLOCK_RESOLVED_KIND:
        log[-1] = {**log[-1], "channel": ch}
        campaign.outreach_activity_log = log


def detect_linkedin_verify_stall(
    db: Session,
    campaign: Campaign,
    *,
    min_stuck_seconds: int = 180,
) -> dict[str, Any] | None:
    """
    Si hay prospectos en checking/check_queued hace rato y ninguno avanzó a
    connected/invite, la extensión no está reportando → bloquear con error exacto.

    LI-SAFE: no hay verify de grado → nunca stall de extensión.
    """
    from app.services.linkedin_assisted_service import LI_SAFE_NO_PROFILE_PROBE

    if LI_SAFE_NO_PROFILE_PROBE:
        return None

    from datetime import UTC, datetime, timedelta

    from app.models.enums import ProspectStatus
    from app.models.prospect import Prospect

    terminal = {
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
        ProspectStatus.failed.value,
    }
    rows = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign.id,
            Prospect.status.notin_(list(terminal)),
        )
    ).all()
    if not rows:
        return None

    stuck_statuses = {"checking", "check_queued", "check_failed"}
    stuck = [
        p
        for p in rows
        if (p.linkedin_connection_status or "").strip().lower() in stuck_statuses
        and bool((p.linkedin_assisted_draft or "").strip() or (p.linkedin_url or "").strip())
    ]
    if not stuck:
        return None

    # Si alguno ya resolvió a connected/invite, no es stall global.
    progressed = [
        p
        for p in rows
        if (p.linkedin_connection_status or "").strip().lower()
        in {"connected", "invite_pending", "invite_sent"}
    ]
    if progressed:
        return None

    now = datetime.now(UTC)
    oldest = None
    for p in stuck:
        ts = getattr(p, "linkedin_last_assisted_at", None) or getattr(p, "sequence_started_at", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if oldest is None or ts < oldest:
            oldest = ts
    if oldest is None:
        # Sin timestamp: si hay ≥1 en checking y cola, igual avisar.
        if not any((p.linkedin_connection_status or "") == "checking" for p in stuck):
            return None
    elif now < oldest + timedelta(seconds=max(60, int(min_stuck_seconds))):
        return None

    n = len(stuck)
    mins = max(1, int((now - oldest).total_seconds() // 60)) if oldest else 3
    # Cap display so timestamps viejos no digan "1200 min".
    mins = min(mins, 30)
    return {
        "channel": "linkedin",
        "code": "extension_not_responding",
        "error": (
            f"LinkedIn lleva varios minutos verificando {n} contacto(s) "
            f"(~{mins} min) y todavía no llegó el reporte de 1º/2º/3º grado. "
            "Dejá LinkedIn abierto y logueado en el mismo Chrome de la extensión Nexus; "
            "Nexus reintenta sola. No hace falta saltar LinkedIn."
        ),
        "action": "reconnect_extension",
        "blocked_prospects": n,
    }


def continue_sequence_without_channel(
    db: Session,
    campaign: Campaign,
    *,
    channel: str,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Quita el canal bloqueado de allowed_channels y omite toques pendientes
    de ese canal para que la secuencia siga con el resto del plan.
    """
    from app.models.enums import ProspectStatus
    from app.models.prospect import Prospect
    from app.models.user import User
    from app.services import prospect_sequence as seq
    from app.services.campaign_sequence_channels import effective_channel_for_day

    ch = (channel or "").strip().lower()
    if ch not in ("linkedin", "whatsapp", "email"):
        return {"ok": False, "detail": "Canal inválido."}

    allowed = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    if ch in allowed:
        allowed = [c for c in allowed if c != ch]
        if not allowed:
            return {
                "ok": False,
                "detail": (
                    f"No podés quitar {channel_label(ch)}: quedaría la secuencia sin canales. "
                    "Reconectá la integración o agregá otro canal al plan."
                ),
            }
        campaign.allowed_channels = allowed

    clear_campaign_integration_block(
        campaign,
        channel=ch,
        note=(
            f"Seguís la secuencia sin {channel_label(ch)}. "
            f"Los toques de ese canal se omiten; el resto del plan continúa."
        ),
    )

    actor = db.get(User, int(actor_user_id)) if actor_user_id else None
    if actor is None and campaign.seller_id:
        actor = db.get(User, int(campaign.seller_id))

    terminal = {
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
        ProspectStatus.failed.value,
    }
    rows = db.scalars(
        select(Prospect).where(
            Prospect.campaign_id == campaign.id,
            Prospect.status.notin_(list(terminal)),
        )
    ).all()

    omitted = 0
    advanced = 0
    allowed_now = coerce_allowed_channels(getattr(campaign, "allowed_channels", None))
    for prospect in rows:
        if prospect.sequence_started_at is None:
            continue
        if getattr(prospect, "sequence_paused", False):
            continue
        # Limpiar cola LI/WA del canal omitido.
        if ch == "linkedin":
            prospect.linkedin_assisted_draft = None
            if (prospect.linkedin_connection_status or "") in (
                "checking",
                "check_queued",
                "check_failed",
            ):
                prospect.linkedin_connection_status = "none"
        if ch == "whatsapp":
            prospect.whatsapp_assisted_draft = None

        for _ in range(8):
            nxt = seq.next_executable_day(prospect, campaign)
            if nxt is None:
                break
            step_ch = (effective_channel_for_day(campaign, nxt) or "").strip().lower()
            if step_ch == ch or (step_ch and step_ch not in allowed_now):
                seq._auto_omit_sequence_touch(
                    db,
                    prospect=prospect,
                    day=int(nxt),
                    reason=f"{ch}_omitido_por_usuario",
                )
                omitted += 1
                continue
            # Foco D: al reanudar, no adelantar toques futuros del playbook.
            if not is_touch_calendar_due(prospect.sequence_started_at, int(nxt)):
                next_at, _ = seq.compute_next_touch(prospect, campaign)
                prospect.next_touch_at = next_at
                break
            if actor is not None:
                try:
                    result = seq.execute_sequence_touch(
                        db,
                        user=actor,
                        prospect=prospect,
                        day=int(nxt),
                        scheduled=False,
                    )
                    if result.get("omitted") or result.get("skipped"):
                        omitted += 1
                        continue
                    advanced += 1
                except Exception:  # noqa: BLE001
                    pass
            break

    db.flush()
    return {
        "ok": True,
        "channel": ch,
        "allowed_channels": allowed_now,
        "omitted_touches": omitted,
        "advanced_prospects": advanced,
        "message": (
            f"Secuencia sin {channel_label(ch)}. "
            f"Omitidos {omitted} toque(s); {advanced} prospecto(s) reanudados."
        ),
    }
