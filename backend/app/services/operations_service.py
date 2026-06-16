"""Agregación operativa: salud, colas, métricas y feed de decisiones."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.automation_job_state import AutomationJobState
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus
from app.models.inbound_auto_reply_receipt import InboundAutoReplyReceipt
from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.services import nexus_scheduler as ns
from app.services import outreach_metrics as om
from app.services.ai_decision_log import list_company_feed
from app.services.inbound_auto_reply import count_inbound_auto_reply_tasks

_JOB_LABELS: dict[str, str] = {
    "automation:tick_gmail_inbound": "Gmail inbound sync",
    "automation:tick_calendar_sync": "Google Calendar sync",
    "automation:tick_followups": "Follow-ups programados",
    "automation:tick_initial_outreach": "Primer contacto email",
    "automation:tick_inbound_auto_reply": "Auto-respuesta inbound",
}


def _job_duration_sec(started: datetime | None, finished: datetime | None) -> float | None:
    if not started or not finished:
        return None
    try:
        return max(0.0, (finished - started).total_seconds())
    except Exception:
        return None


def _integration_health(db: Session, company_id: int) -> dict[str, Any]:
    rows = db.scalars(
        select(ConnectedAccount).where(ConnectedAccount.company_id == company_id)
    ).all()
    gmail = [r for r in rows if r.provider == IntegrationProvider.gmail.value]
    gcal = [r for r in rows if r.provider == IntegrationProvider.google_calendar.value]
    gmail_ok = sum(1 for r in gmail if r.status == IntegrationStatus.connected.value)
    gcal_ok = sum(1 for r in gcal if r.status == IntegrationStatus.connected.value)
    last_gmail = max(
        (r.updated_at for r in gmail if r.updated_at),
        default=None,
    )
    last_cal = max(
        (r.updated_at for r in gcal if r.updated_at),
        default=None,
    )
    return {
        "gmail_connected": gmail_ok,
        "gmail_accounts": len(gmail),
        "calendar_connected": gcal_ok,
        "calendar_accounts": len(gcal),
        "last_gmail_activity_at": last_gmail.isoformat() if last_gmail else None,
        "last_calendar_activity_at": last_cal.isoformat() if last_cal else None,
        "status": "healthy" if gmail_ok and gcal_ok else ("degraded" if gmail_ok or gcal_ok else "offline"),
    }


def _metrics_window(db: Session, company_id: int, *, hours: int = 24) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    camp_ids = list(
        db.scalars(select(Campaign.id).where(Campaign.company_id == company_id)).all()
    )
    if not camp_ids:
        return {
            "window_hours": hours,
            "emails_sent": 0,
            "inbound_detected": 0,
            "meetings_booked": 0,
            "auto_replies_sent": 0,
            "auto_replies_draft": 0,
            "auto_replies_skipped": 0,
            "failed_sends": 0,
        }

    outbound_sent = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.campaign_id.in_(camp_ids),
                OutreachMessage.direction == "outbound",
                OutreachMessage.sender_type.in_(("ai", "system")),
                OutreachMessage.created_at >= since,
                OutreachMessage.message.notlike("%Borrador Gmail%"),
            )
        )
        or 0
    )
    inbound_n = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.campaign_id.in_(camp_ids),
                OutreachMessage.direction == "inbound",
                OutreachMessage.created_at >= since,
            )
        )
        or 0
    )
    meetings_n = int(
        db.scalar(
            select(func.count(Meeting.id)).where(
                Meeting.campaign_id.in_(camp_ids),
                Meeting.created_at >= since,
            )
        )
        or 0
    )

    receipts = db.scalars(
        select(InboundAutoReplyReceipt).where(
            InboundAutoReplyReceipt.company_id == company_id,
            InboundAutoReplyReceipt.created_at >= since,
        )
    ).all()
    sent_r = sum(1 for r in receipts if r.outcome == "sent")
    draft_r = sum(1 for r in receipts if r.outcome == "draft")
    skipped_r = sum(
        1 for r in receipts if str(r.outcome or "").startswith("skipped")
    )

    contacted = int(
        db.scalar(
            select(func.count(Prospect.id)).where(
                Prospect.company_id == company_id,
                Prospect.last_outbound_at.isnot(None),
            )
        )
        or 0
    )
    replied = int(
        db.scalar(
            select(func.count(Prospect.id)).where(
                Prospect.company_id == company_id,
                Prospect.last_inbound_at.isnot(None),
            )
        )
        or 0
    )
    reply_rate = (replied / contacted) if contacted else 0.0
    meeting_rate = (meetings_n / replied) if replied else 0.0

    return {
        "window_hours": hours,
        "emails_sent": outbound_sent,
        "inbound_detected": inbound_n,
        "meetings_booked": meetings_n,
        "auto_replies_sent": sent_r,
        "auto_replies_draft": draft_r,
        "auto_replies_skipped": skipped_r,
        "reply_rate": round(reply_rate, 4),
        "meeting_rate": round(meeting_rate, 4),
        "prospects_contacted": contacted,
        "prospects_replied": replied,
    }


def _task_queue(db: Session, company_id: int) -> dict[str, Any]:
    rows = db.execute(
        select(OutreachTask.task_kind, OutreachTask.status, func.count(OutreachTask.id))
        .where(OutreachTask.company_id == company_id)
        .group_by(OutreachTask.task_kind, OutreachTask.status)
    ).all()
    by_kind: dict[str, dict[str, int]] = {}
    total_pending = 0
    for kind, status, cnt in rows:
        by_kind.setdefault(kind, {})[status] = int(cnt)
        if status == "pending":
            total_pending += int(cnt)
    due_soon = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(
                OutreachTask.company_id == company_id,
                OutreachTask.status == "pending",
                OutreachTask.due_at <= datetime.now(UTC) + timedelta(minutes=15),
            )
        )
        or 0
    )
    return {
        "total_pending": total_pending,
        "due_within_15m": due_soon,
        "by_kind_status": by_kind,
    }


def _campaigns_ops(db: Session, company_id: int) -> list[dict[str, Any]]:
    camps = db.scalars(
        select(Campaign)
        .where(Campaign.company_id == company_id)
        .options(selectinload(Campaign.outreach_sequence))
        .order_by(Campaign.updated_at.desc().nullslast(), Campaign.id.desc())
    ).all()
    out: list[dict[str, Any]] = []
    for c in camps:
        seq = c.outreach_sequence
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "automation_paused": bool(c.automation_paused),
                "automation_mode": getattr(c, "automation_mode", None) or "semi_auto",
                "inbound_reply_mode": c.inbound_reply_mode,
                "outreach_email_mode": c.outreach_email_mode,
                "sequence_running": bool(seq and seq.is_running),
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        )
    return out


def build_operations_overview(db: Session, company_id: int) -> dict[str, Any]:
    company = db.get(Company, company_id)
    global_stop = bool(getattr(company, "global_automation_stop", False)) if company else False

    jobs_raw = db.scalars(select(AutomationJobState).order_by(AutomationJobState.job_key)).all()
    jobs: list[dict[str, Any]] = []
    for j in jobs_raw:
        dur = _job_duration_sec(j.last_started_at, j.last_finished_at)
        jobs.append(
            {
                "job_key": j.job_key,
                "label": _JOB_LABELS.get(j.job_key, j.job_key),
                "last_started_at": j.last_started_at.isoformat() if j.last_started_at else None,
                "last_finished_at": j.last_finished_at.isoformat() if j.last_finished_at else None,
                "last_success_at": j.last_success_at.isoformat() if j.last_success_at else None,
                "last_error": j.last_error,
                "run_count": j.run_count,
                "duration_sec": dur,
                "last_result_meta": j.last_result_meta,
                "locked_until": j.locked_until.isoformat() if j.locked_until else None,
            }
        )

    recent_errors = [
        {"job_key": j["job_key"], "label": j["label"], "error": j["last_error"]}
        for j in jobs
        if j.get("last_error")
    ][:8]

    camps = _campaigns_ops(db, company_id)
    running = sum(1 for c in camps if c["status"] == "running" and not c["automation_paused"])
    paused = sum(1 for c in camps if c["automation_paused"] or c["status"] == "paused")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "global_automation_stop": global_stop,
        "real_mode": om.is_real_mode(),
        "scheduler": {
            "running": ns.scheduler_running(),
            "enabled_env": (os.getenv("NEXUS_AUTOMATION_SCHEDULER") or "").strip(),
            "auto_send_env": (os.getenv("NEXUS_AUTO_SEND_ENABLED") or "").strip(),
            "inbound_auto_reply_env": (os.getenv("NEXUS_INBOUND_AUTO_REPLY") or "1").strip(),
            "inbound_poll_sec": (os.getenv("NEXUS_INBOUND_REPLY_POLL_INTERVAL_SEC") or "45").strip(),
        },
        "integrations": _integration_health(db, company_id),
        "jobs": jobs,
        "recent_errors": recent_errors,
        "inbound_auto_reply_tasks": count_inbound_auto_reply_tasks(db),
        "task_queue": _task_queue(db, company_id),
        "metrics_24h": _metrics_window(db, company_id, hours=24),
        "metrics_7d": _metrics_window(db, company_id, hours=24 * 7),
        "campaigns": camps,
        "campaigns_running": running,
        "campaigns_paused": paused,
    }


def build_activity_feed(db: Session, company_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    events = list_company_feed(db, company_id=company_id, limit=limit)
    feed: list[dict[str, Any]] = []
    for e in events:
        feed.append(
            {
                "id": e.id,
                "at": e.created_at.isoformat(),
                "event_type": e.event_type,
                "decision": e.decision,
                "summary": e.summary,
                "campaign_id": e.campaign_id,
                "prospect_id": e.prospect_id,
                "confidence": e.confidence,
                "payload": e.payload,
            }
        )
    return feed


def apply_automation_mode(campaign: Campaign, mode: str) -> None:
    m = (mode or "semi_auto").strip().lower()
    campaign.automation_mode = m
    if m == "manual":
        campaign.inbound_reply_mode = "draft_only"
        campaign.outreach_email_mode = "draft_only"
        campaign.automation_paused = False
    elif m == "full_auto":
        campaign.inbound_reply_mode = "auto_send"
        campaign.outreach_email_mode = "auto_send"
        campaign.automation_paused = False
    else:
        campaign.inbound_reply_mode = "auto_send"
        campaign.outreach_email_mode = "draft_only"
        campaign.automation_paused = False
