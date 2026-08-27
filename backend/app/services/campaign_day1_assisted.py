"""Día 1 LinkedIn / WhatsApp / email al importar o iniciar campaña ICP."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import ProspectStatus
from app.models.product import Product
from app.models.prospect import Prospect
from app.models.user import User
from app.core.sequence_playbook import is_touch_calendar_due
from app.services import prospect_sequence as seq
from app.services.campaign_sequence_channels import effective_channel_for_day
from app.services.linkedin_assisted_service import (
    CONN_CHECK_FAILED,
    CONN_CHECK_QUEUED,
    CONN_CHECKING,
    CONN_CONNECTED,
    CONN_INVITE_PENDING,
    CONN_INVITE_SENT,
    read_connection_status,
)
from app.services.sequence_channel_gate import (
    channel_label,
    seller_channel_block,
    set_campaign_integration_block,
)

_logger = logging.getLogger(__name__)

_TERMINAL = frozenset(
    {
        ProspectStatus.not_interested.value,
        ProspectStatus.meeting_booked.value,
        ProspectStatus.failed.value,
    }
)


def _day_channel(campaign: Campaign, day: int) -> str | None:
    ch = effective_channel_for_day(campaign, day)
    return (ch or "").strip().lower() or None


def _linkedin_day1_already_queued(prospect: Prospect) -> bool:
    from app.services.linkedin_assisted_service import LI_SAFE_NO_PROFILE_PROBE

    conn = read_connection_status(prospect)
    # LI-SAFE: checking no cuenta como “ya en cola de verify”; se libera a mensaje.
    if LI_SAFE_NO_PROFILE_PROBE and conn in (
        CONN_CHECKING,
        CONN_CHECK_QUEUED,
        CONN_CHECK_FAILED,
    ):
        if (prospect.linkedin_assisted_draft or "").strip():
            return True
        return False
    if conn in (
        CONN_CHECKING,
        CONN_CHECK_QUEUED,
        CONN_CHECK_FAILED,
        CONN_INVITE_PENDING,
        CONN_INVITE_SENT,
        CONN_CONNECTED,
    ):
        return True
    if (prospect.linkedin_assisted_draft or "").strip():
        return True
    return False


def _whatsapp_day_already_queued(prospect: Prospect, day: int | None = None) -> bool:
    if (prospect.whatsapp_assisted_draft or "").strip():
        return True
    if day is not None:
        from app.services.prospect_sequence import TOUCH_ENVIADO, TOUCH_GENERADO, _touch_log

        entry = _touch_log(prospect).get(str(int(day)), {})
        status = entry.get("status")
        if status == TOUCH_ENVIADO and (
            entry.get("whatsapp_assisted_sent")
            or entry.get("sdr_marked_sent")
            or getattr(prospect, "whatsapp_sdr_marked_sent_at", None)
        ):
            return True
        if status == TOUCH_GENERADO and (
            (entry.get("message_body") or "").strip()
            or (entry.get("body") or "").strip()
            or getattr(prospect, "whatsapp_sdr_marked_sent_at", None)
        ):
            return True
    return False


def kickoff_assisted_day1_for_prospect(
    db: Session,
    campaign: Campaign,
    prospect: Prospect,
    *,
    actor: User | None,
    product: Product | None = None,
) -> dict[str, Any]:
    """
    Arranca la secuencia de UN prospecto.
    - Sin dato de contacto del canal → omite ese día y prueba el siguiente.
    - Extensión/Gmail desconectados → espera (hold), no omite.
    """
    out: dict[str, Any] = {
        "prospect_id": prospect.id,
        "started": False,
        "queued_linkedin": False,
        "queued_whatsapp": False,
        "omitted_days": [],
        "held": False,
        "block": None,
        "delivered_day": None,
        "channel": None,
        "skipped": False,
        "error": None,
    }
    if actor is None:
        out["error"] = "Sin vendedor asignado."
        return out
    if getattr(prospect, "sequence_paused", False):
        out["skipped"] = True
        return out
    if (prospect.status or "") in _TERMINAL:
        out["skipped"] = True
        return out

    if product is None and campaign.product_id:
        product = db.get(Product, int(campaign.product_id))

    try:
        if prospect.sequence_started_at is None:
            seq.bootstrap_sequence_scaffold_fast(
                db,
                prospect=prospect,
                campaign=campaign,
                product=product,
            )
            seq.start_prospect_sequence(db, user=actor, prospect=prospect)
            db.refresh(prospect)
    except Exception as exc:  # noqa: BLE001
        detail = getattr(exc, "detail", None) or str(exc)
        out["error"] = str(detail)[:400]
        return out

    planned = list(seq._planned_days(prospect, campaign)) or [1]
    done = seq._completed_days(prospect, campaign)

    for day in planned:
        day_i = int(day)
        if day_i in done:
            continue
        channel = _day_channel(campaign, day_i)
        out["channel"] = channel

        # Foco D: no adelantar días futuros del playbook en el kickoff.
        # Tras omitir día 1 (sin dato), el día 4/7 queda para el scheduler.
        if not is_touch_calendar_due(prospect.sequence_started_at, day_i):
            next_at, _ = seq.compute_next_touch(prospect, campaign)
            prospect.next_touch_at = next_at
            db.flush()
            out["waiting_calendar"] = True
            out["next_day"] = day_i
            if out["omitted_days"] and not out["started"]:
                out["skipped"] = True
            return out

        if channel == "linkedin" and day_i == 1 and _linkedin_day1_already_queued(prospect):
            out["skipped"] = True
            out["queued_linkedin"] = True
            return out
        if channel == "whatsapp" and _whatsapp_day_already_queued(prospect, day_i):
            out["skipped"] = True
            out["queued_whatsapp"] = True
            return out

        # Integración/extensión caída → ESPERAR (no omitir).
        block = seller_channel_block(db, campaign, channel)
        if block:
            out["held"] = True
            out["block"] = block
            return out

        try:
            result = seq.execute_sequence_touch(
                db,
                user=actor,
                prospect=prospect,
                day=day_i,
                scheduled=False,
            )
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None) or str(exc)
            detail_s = str(detail)
            # Si el fallo huele a reconexión, hold (no avanzar).
            low = detail_s.lower()
            if any(
                token in low
                for token in (
                    "reconect",
                    "extensión",
                    "extension",
                    "gmail no está conectado",
                    "token",
                    "no autorizado",
                )
            ):
                out["held"] = True
                out["block"] = {
                    "channel": channel or "email",
                    "code": "integration_error",
                    "error": detail_s[:400],
                    "action": "reconnect_extension",
                }
                out["error"] = detail_s[:400]
                return out
            _logger.warning(
                "assisted day kickoff failed campaign=%s prospect=%s day=%s: %s",
                campaign.id,
                prospect.id,
                day_i,
                detail_s[:300],
            )
            out["error"] = detail_s[:400]
            return out

        if result.get("omitted") or result.get("skipped"):
            out["omitted_days"].append(day_i)
            done = seq._completed_days(prospect, campaign)
            continue

        out["started"] = True
        out["delivered_day"] = day_i
        out["channel"] = result.get("channel") or channel
        if result.get("linkedin_assisted"):
            out["queued_linkedin"] = True
        if result.get("whatsapp_assisted"):
            out["queued_whatsapp"] = True
        return out

    if out["omitted_days"] and not out["started"]:
        out["skipped"] = True
        out["error"] = (
            "Sin canales disponibles para este prospecto "
            f"(días omitidos: {', '.join(str(d) for d in out['omitted_days'])})."
        )
    return out


def kickoff_assisted_day1_for_prospects(
    db: Session,
    campaign: Campaign,
    *,
    actor: User | None,
    prospect_ids: Iterable[int] | None = None,
    max_batch: int = 50,
) -> dict[str, Any]:
    """
    Encola/arranca secuencia para prospectos de la campaña (todos o un lote de IDs).
    """
    out: dict[str, Any] = {
        "channel": _day_channel(campaign, 1),
        "started": 0,
        "queued_linkedin": 0,
        "queued_whatsapp": 0,
        "skipped": 0,
        "held": 0,
        "omitted_to_next": 0,
        "errors": [],
        "block": None,
    }
    if actor is None:
        out["errors"].append({"detail": "Sin vendedor asignado."})
        return out

    product = db.get(Product, int(campaign.product_id)) if campaign.product_id else None
    ids = [int(x) for x in (prospect_ids or []) if x]
    if ids:
        rows = db.scalars(
            select(Prospect)
            .where(
                Prospect.campaign_id == campaign.id,
                Prospect.id.in_(ids),
                Prospect.status.notin_(list(_TERMINAL)),
            )
            .order_by(Prospect.id.asc())
        ).all()
    else:
        rows = db.scalars(
            select(Prospect)
            .where(
                Prospect.campaign_id == campaign.id,
                Prospect.status.notin_(list(_TERMINAL)),
            )
            .order_by(Prospect.id.asc())
            .limit(max(1, min(max_batch, 200)))
        ).all()

    held_block: dict[str, Any] | None = None
    held_count = 0
    for prospect in rows:
        result = kickoff_assisted_day1_for_prospect(
            db,
            campaign,
            prospect,
            actor=actor,
            product=product,
        )
        if result.get("held"):
            out["held"] += 1
            held_count += 1
            if held_block is None and result.get("block"):
                held_block = dict(result["block"])
            continue
        if result.get("error") and not result.get("started") and not result.get("skipped"):
            out["errors"].append(
                {"prospect_id": prospect.id, "detail": result["error"]}
            )
            continue
        if result.get("omitted_days"):
            out["omitted_to_next"] += len(result["omitted_days"])
        if result.get("skipped") and not result.get("started"):
            out["skipped"] += 1
            continue
        if result.get("started") or result.get("queued_linkedin") or result.get("queued_whatsapp"):
            out["started"] += 1
            if result.get("queued_linkedin"):
                out["queued_linkedin"] += 1
            if result.get("queued_whatsapp"):
                out["queued_whatsapp"] += 1

    if held_block:
        out["block"] = held_block
        set_campaign_integration_block(
            campaign,
            held_block,
            blocked_prospects=held_count,
        )
        label = channel_label(held_block.get("channel"))
        out["hold_message"] = (
            f"Secuencia en espera de {label}: {held_block.get('error')}"
        )

    return out


def kickoff_assisted_day1_for_campaign(
    db: Session,
    campaign: Campaign,
    *,
    actor: User | None,
    max_batch: int = 50,
) -> dict[str, Any]:
    """Compat: encola día 1 para hasta max_batch prospectos de la campaña."""
    return kickoff_assisted_day1_for_prospects(
        db,
        campaign,
        actor=actor,
        prospect_ids=None,
        max_batch=max_batch,
    )
