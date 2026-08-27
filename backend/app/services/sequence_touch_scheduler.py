"""Scheduler de toques de secuencia (días 4–19) en NEXUS_REAL_MODE."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.sequence_playbook import (
    PLAYBOOK_DAYS,
    playbook_step_for_day,
    sequence_calendar_day_index,
)
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.models.user import User
from app.services import multichannel_sequence as mseq
from app.services import outreach_metrics as om
from app.services.prospect_sequence import (
    TOUCH_ENVIADO,
    TOUCH_GENERADO,
    _channel_ready,
    _playbook_step,
    _touch_log,
    advance_auto_skipped_linkedin_touches,
    execute_sequence_touch,
    is_assisted_sequence_touch_due,
    next_executable_day,
)

logger = logging.getLogger(__name__)

_SKIP_GROUPS = frozenset(
    {
        mseq.SEQUENCE_GROUP_ENCAJONADO,
        mseq.SEQUENCE_GROUP_POSTERGADO,
        mseq.SEQUENCE_GROUP_REUNIONES,
    }
)


def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def sequence_touches_scheduler_enabled() -> bool:
    if not om.is_real_mode():
        return False
    explicit = (os.getenv("NEXUS_SEQUENCE_TOUCHES_ENABLED") or "").strip().lower()
    if explicit in ("0", "false", "no", "off"):
        return False
    if explicit in ("1", "true", "yes", "on"):
        return True
    return _truthy("NEXUS_AUTOMATION_SCHEDULER")


def _prospect_eligible(prospect: Prospect) -> bool:
    if prospect.sequence_started_at is None:
        return False
    if getattr(prospect, "sequence_paused", False):
        return False
    if getattr(prospect, "sequence_group", None) in _SKIP_GROUPS:
        return False
    return True


def _linkedin_touch_pending_in_queue(prospect: Prospect, day: int) -> bool:
    entry = _touch_log(prospect).get(str(day), {})
    if entry.get("status") != TOUCH_GENERADO:
        return False
    return bool(
        (prospect.linkedin_assisted_draft or "").strip()
        or (entry.get("message_body") or "").strip()
        or (entry.get("body") or "").strip()
    )


def _whatsapp_touch_pending_in_queue(prospect: Prospect, day: int) -> bool:
    """True si el toque WA ya está en cola o ya lo envió el SDR (no regenerar frío)."""
    entry = _touch_log(prospect).get(str(day), {})
    status = entry.get("status")
    if status == TOUCH_ENVIADO and (
        entry.get("whatsapp_assisted_sent")
        or entry.get("sdr_marked_sent")
        or getattr(prospect, "whatsapp_sdr_marked_sent_at", None)
    ):
        return True
    if status != TOUCH_GENERADO:
        return False
    if getattr(prospect, "whatsapp_sdr_marked_sent_at", None) and not (
        prospect.whatsapp_assisted_draft or ""
    ).strip():
        return True
    return bool(
        (prospect.whatsapp_assisted_draft or "").strip()
        or (entry.get("message_body") or "").strip()
        or (entry.get("body") or "").strip()
    )


def evaluate_scheduled_touch(
    prospect: Prospect,
    *,
    now: datetime | None = None,
    campaign: Campaign | None = None,
) -> tuple[int | None, str | None]:
    """
    Devuelve (day, skip_reason). day=None si no hay toque listo para el scheduler.
    """
    now = now or datetime.now(UTC)
    if not _prospect_eligible(prospect):
        return None, "not_eligible"

    next_day = next_executable_day(prospect, campaign)
    if next_day is None:
        return None, "sequence_complete"
    if next_day == 1:
        step = _playbook_step(1, campaign)
        channel = str(getattr(step, "channel", None) or "email").strip().lower()
        # Email día 1 lo dispara el kickoff / initial outreach, no el scheduler.
        if channel == "email":
            return None, "day1_initial_outreach"
        if channel == "linkedin" and _linkedin_touch_pending_in_queue(prospect, 1):
            return None, "linkedin_pending_sdr"
        if channel == "whatsapp" and _whatsapp_touch_pending_in_queue(prospect, 1):
            return None, "whatsapp_pending_sdr"
        # LinkedIn/WhatsApp día 1: si aún no se encoló al activar, el scheduler lo ejecuta.
        if channel in ("linkedin", "whatsapp"):
            if not _channel_ready(prospect, channel):
                return None, f"channel_unavailable:{channel}"
            return 1, None
        return None, "day1_awaiting_manual"

    if not is_assisted_sequence_touch_due(
        prospect, next_day, campaign=campaign, now=now
    ):
        return None, "not_calendar_due"

    step = _playbook_step(next_day, campaign)
    if step is None:
        return None, "invalid_step"

    if step.channel == "linkedin" and _linkedin_touch_pending_in_queue(prospect, next_day):
        return None, "linkedin_pending_sdr"
    if step.channel == "whatsapp" and _whatsapp_touch_pending_in_queue(prospect, next_day):
        return None, "whatsapp_pending_sdr"

    if not _channel_ready(prospect, step.channel):
        return None, f"channel_unavailable:{step.channel}"

    return next_day, None


def process_campaign_scheduled_touches(
    db: Session,
    campaign: Campaign,
    seller: User,
    *,
    max_batch: int = 5,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "campaign_id": campaign.id,
        "considered": 0,
        "executed": 0,
        "linkedin_queued": 0,
        "gmail_drafts": 0,
        "whatsapp_sent": 0,
        "skipped": 0,
        "errors": 0,
        "skip_reasons": {},
        "error_messages": [],
        "prospect_results": [],
    }

    if not campaign.seller_id or seller.id != campaign.seller_id:
        stats["skipped"] = 1
        stats["skip_reasons"]["no_seller"] = 1
        return stats

    rows = db.scalars(
        select(Prospect)
        .where(
            Prospect.campaign_id == campaign.id,
            Prospect.sequence_started_at.isnot(None),
            Prospect.sequence_paused.is_(False),
        )
        .order_by(Prospect.next_touch_at.asc().nullslast(), Prospect.id.asc())
    ).all()

    now = datetime.now(UTC)
    for prospect in rows:
        if stats["executed"] >= max_batch:
            break
        if prospect.owner_user_id and prospect.owner_user_id != seller.id:
            # Director/owner puede iniciar la secuencia; el scheduler igual corre como seller.
            from app.core.permissions import is_company_admin

            owner = db.get(User, prospect.owner_user_id)
            if owner is None or not is_company_admin(owner.role):
                stats["skipped"] += 1
                stats["skip_reasons"]["owner_mismatch"] = (
                    int(stats["skip_reasons"].get("owner_mismatch", 0)) + 1
                )
                continue

        stats["considered"] += 1
        omitted = advance_auto_skipped_linkedin_touches(
            db, prospect=prospect, campaign=campaign, now=now
        )
        if omitted:
            db.commit()
        day, skip_reason = evaluate_scheduled_touch(prospect, now=now, campaign=campaign)
        if day is None:
            stats["skipped"] += 1
            if skip_reason:
                stats["skip_reasons"][skip_reason] = int(stats["skip_reasons"].get(skip_reason, 0)) + 1
            continue

        try:
            result = execute_sequence_touch(
                db,
                user=seller,
                prospect=prospect,
                day=day,
                scheduled=True,
            )
            db.commit()
            stats["executed"] += 1
            if result.get("linkedin_assisted"):
                stats["linkedin_queued"] += 1
            if result.get("gmail_draft_created"):
                stats["gmail_drafts"] += 1
            if result.get("whatsapp_sent"):
                stats["whatsapp_sent"] += 1
            step = playbook_step_for_day(day)
            try:
                step = _playbook_step(day, campaign) or step
            except Exception:  # noqa: BLE001
                pass
            channel = getattr(step, "channel", None) if step else None
            stats["prospect_results"].append(
                {
                    "prospect_id": prospect.id,
                    "day": day,
                    "channel": channel,
                    "touch_status": result.get("touch_status"),
                    "message": result.get("message"),
                }
            )
            logger.info(
                "sequence touch scheduled ok campaign=%s prospect=%s day=%s channel=%s",
                campaign.id,
                prospect.id,
                day,
                channel or "?",
            )
        except HTTPException as exc:
            db.rollback()
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            low = detail.lower()
            if exc.status_code in (429, 409):
                stats["skipped"] += 1
                reason = "daily_cap" if exc.status_code == 429 else "not_qualified"
                stats["skip_reasons"][reason] = int(stats["skip_reasons"].get(reason, 0)) + 1
                continue
            if exc.status_code == 400 and ("cola" in low or "próximo toque" in low or "calendar" in low):
                stats["skipped"] += 1
                reason = "http_skip"
                stats["skip_reasons"][reason] = int(stats["skip_reasons"].get(reason, 0)) + 1
                continue
            stats["errors"] += 1
            stats["error_messages"].append(
                f"prospect={prospect.id} day={day}: {detail[:300]}"
            )
            logger.warning(
                "sequence touch scheduled failed campaign=%s prospect=%s day=%s: %s",
                campaign.id,
                prospect.id,
                day,
                detail[:200],
            )
        except Exception as exc:
            db.rollback()
            stats["errors"] += 1
            stats["error_messages"].append(
                f"prospect={prospect.id} day={day}: {type(exc).__name__}: {exc}"[:300]
            )
            logger.exception(
                "sequence touch scheduled error campaign=%s prospect=%s day=%s",
                campaign.id,
                prospect.id,
                day,
            )

    return stats


def process_active_campaigns_scheduled_touches(
    db: Session,
    campaigns: list[Campaign],
    *,
    max_batch_per_campaign: int | None = None,
) -> dict[str, Any]:
    batch = max_batch_per_campaign or int(os.getenv("NEXUS_SEQUENCE_TOUCH_BATCH_SIZE", "500"))
    batch = max(1, min(int(batch), 500))
    totals = {
        "campaigns": len(campaigns),
        "executed": 0,
        "linkedin_queued": 0,
        "gmail_drafts": 0,
        "whatsapp_sent": 0,
        "skipped": 0,
        "errors": 0,
        "per_campaign": [],
        "finished_at": datetime.now(UTC).isoformat(),
    }
    for campaign in campaigns:
        if not campaign.seller_id:
            continue
        seller = db.get(User, campaign.seller_id)
        if seller is None:
            continue
        st = process_campaign_scheduled_touches(
            db,
            campaign,
            seller,
            max_batch=batch,
        )
        totals["executed"] += int(st.get("executed") or 0)
        totals["linkedin_queued"] += int(st.get("linkedin_queued") or 0)
        totals["gmail_drafts"] += int(st.get("gmail_drafts") or 0)
        totals["whatsapp_sent"] += int(st.get("whatsapp_sent") or 0)
        totals["skipped"] += int(st.get("skipped") or 0)
        totals["errors"] += int(st.get("errors") or 0)
        totals["per_campaign"].append(st)
    return totals
