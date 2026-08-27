"""Observabilidad global para Nexus Support (solo equipo interno)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.automation_job_state import AutomationJobState
from app.models.billing_ops_cycle import BillingOpsCycle
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.connected_account import ConnectedAccount
from app.models.enums import IntegrationProvider, IntegrationStatus, UserRole
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.models.user import User
from app.services import nexus_scheduler as ns
from app.services import openai_diagnostics as openai_diag
from app.services import outreach_metrics as om
from app.services.credit_ledger import current_plan_cycle_key
from app.services.billing_ops import tools_ready
from app.services.daily_send_limits import DEFAULT_LIMITS, snapshot as daily_limit_snapshot
from app.services.operations_service import _JOB_LABELS
from app.services.support import list_ops_threads

_COGS_PER_SEQUENCE_USD = 0.20  # efectivo post lazy-mobile (peor caso lista ~0.30)
_COGS_SHARES = {"openai": 0.02, "prospeo": 0.90, "brave": 0.08}


def _configured(*keys: str) -> bool:
    return any(bool((os.getenv(key) or "").strip()) for key in keys)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _month_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _job_status(job: AutomationJobState, now: datetime) -> str:
    if job.last_error:
        return "error"
    locked_until = _aware_utc(job.locked_until)
    last_success_at = _aware_utc(job.last_success_at)
    if locked_until and locked_until > now:
        return "running"
    if not last_success_at:
        return "never"
    if last_success_at < now - timedelta(hours=2):
        return "stale"
    return "healthy"


def _jobs(db: Session, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = db.scalars(select(AutomationJobState).order_by(AutomationJobState.job_key)).all()
    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        status = _job_status(row, now)
        item = {
            "job_key": row.job_key,
            "label": _JOB_LABELS.get(row.job_key, row.job_key),
            "status": status,
            "last_started_at": _iso(row.last_started_at),
            "last_finished_at": _iso(row.last_finished_at),
            "last_success_at": _iso(row.last_success_at),
            "last_error": row.last_error,
            "run_count": int(row.run_count or 0),
        }
        jobs.append(item)
        if row.last_error:
            errors.append(
                {
                    "source": "scheduler",
                    "label": item["label"],
                    "message": str(row.last_error)[:500],
                    "at": item["last_finished_at"] or item["last_started_at"],
                }
            )
    return jobs, errors


def _latest_prospeo_health(db: Session) -> dict[str, Any] | None:
    rows = db.scalars(
        select(AutomationJobState).order_by(AutomationJobState.updated_at.desc()).limit(20)
    ).all()
    for row in rows:
        meta = row.last_result_meta or {}
        health = meta.get("prospeo_health") if isinstance(meta, dict) else None
        if isinstance(health, dict):
            return health
    return None


def _provider_budget(db: Session, cycle_key: str) -> dict[str, float]:
    rows = db.scalars(
        select(BillingOpsCycle).where(BillingOpsCycle.cycle_key == cycle_key)
    ).all()
    return {
        "openai": round(sum(float(r.openai_usd or 0) for r in rows), 2),
        "prospeo": round(sum(float(r.prospeo_usd or 0) for r in rows), 2),
        "brave": round(sum(float(r.brave_usd or 0) for r in rows), 2),
    }


def _provider_cards(
    db: Session,
    *,
    sequences_month: int,
    cycle_key: str,
    refresh_prospeo: bool,
) -> list[dict[str, Any]]:
    openai = openai_diag.build_diagnostics(probe=False)
    prospeo = _latest_prospeo_health(db)
    if refresh_prospeo:
        from app.services.lead_sourcing.prospeo_api_health import fetch_prospeo_account_health

        prospeo = fetch_prospeo_account_health().to_dict()

    budget = _provider_budget(db, cycle_key)
    estimates = {
        key: round(sequences_month * _COGS_PER_SEQUENCE_USD * share, 4)
        for key, share in _COGS_SHARES.items()
    }
    openai_errors = openai.get("recent_errors") or []
    prospeo_blocked = bool((prospeo or {}).get("search_blocked"))

    connected = db.execute(
        select(ConnectedAccount.provider, func.count(ConnectedAccount.id))
        .where(ConnectedAccount.status == IntegrationStatus.connected.value)
        .group_by(ConnectedAccount.provider)
    ).all()
    connected_by_provider = {str(provider): int(count) for provider, count in connected}

    from app.services.whatsapp_cloud_service import (
        is_whatsapp_api_configured,
        is_whatsapp_dry_run,
    )

    return [
        {
            "key": "openai",
            "label": "OpenAI",
            "status": (
                "offline"
                if not openai.get("configured")
                else "degraded"
                if openai_errors or openai.get("possible_request_loop")
                else "healthy"
            ),
            "configured": bool(openai.get("configured")),
            "detail": (
                (openai_errors[0].get("error_full") if openai_errors else None)
                or f"{openai.get('requests_per_minute', 0)} req/min · {openai.get('model')}"
            ),
            "usage": {
                "requests_per_minute": int(openai.get("requests_per_minute") or 0),
                "requests_last_5_minutes": int(openai.get("requests_last_5_minutes") or 0),
            },
            "estimated_cost_usd": estimates["openai"],
            "planned_budget_usd": budget["openai"],
        },
        {
            "key": "prospeo",
            "label": "Prospeo",
            "status": (
                "offline"
                if not _configured("PROSPEO_API_KEY")
                else "degraded"
                if prospeo_blocked
                else "healthy"
            ),
            "configured": _configured("PROSPEO_API_KEY"),
            "detail": (prospeo or {}).get("detail")
            or (
                f"{prospeo.get('remaining_credits')} créditos restantes"
                if prospeo and prospeo.get("remaining_credits") is not None
                else "Sin lectura reciente de la cuenta"
            ),
            "usage": {
                "remaining_credits": (prospeo or {}).get("remaining_credits"),
                "used_credits": (prospeo or {}).get("used_credits"),
                "rate_limited": bool((prospeo or {}).get("rate_limited")),
            },
            "estimated_cost_usd": estimates["prospeo"],
            "planned_budget_usd": budget["prospeo"],
        },
        {
            "key": "brave",
            "label": "Brave Search",
            "status": "healthy" if _configured("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY") else "offline",
            "configured": _configured("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY"),
            "detail": "API configurada" if _configured("BRAVE_API_KEY", "BRAVE_SEARCH_API_KEY") else "API sin configurar",
            "usage": {},
            "estimated_cost_usd": estimates["brave"],
            "planned_budget_usd": budget["brave"],
        },
        {
            "key": "gmail",
            "label": "Gmail",
            "status": "healthy" if connected_by_provider.get(IntegrationProvider.gmail.value, 0) else "offline",
            "configured": connected_by_provider.get(IntegrationProvider.gmail.value, 0) > 0,
            "detail": f"{connected_by_provider.get(IntegrationProvider.gmail.value, 0)} cuentas conectadas",
            "usage": {"connected_accounts": connected_by_provider.get(IntegrationProvider.gmail.value, 0)},
            "estimated_cost_usd": 0.0,
            "planned_budget_usd": 0.0,
        },
        {
            "key": "whatsapp",
            "label": "WhatsApp",
            "status": (
                "offline"
                if not is_whatsapp_api_configured()
                else "degraded"
                if is_whatsapp_dry_run()
                else "healthy"
            ),
            "configured": is_whatsapp_api_configured(),
            "detail": "Dry run activo" if is_whatsapp_dry_run() else "Cloud API configurada",
            "usage": {},
            "estimated_cost_usd": 0.0,
            "planned_budget_usd": 0.0,
        },
    ]


def _support_inbox(db: Session) -> dict[str, int]:
    threads = list_ops_threads(db)
    resolved = 0
    waiting = 0
    for thread in threads:
        status = (thread.status or "open").strip().lower()
        if status == "resolved":
            resolved += 1
            continue
        messages = list(thread.messages or [])
        last = messages[-1] if messages else None
        if last is not None and last.role == "user":
            waiting += 1
    return {
        "total": len(threads),
        "open": len(threads) - resolved,
        "waiting": waiting,
        "resolved": resolved,
    }


def _billing_cycle_summary(db: Session, cycle_key: str) -> dict[str, Any]:
    rows = db.scalars(select(BillingOpsCycle).where(BillingOpsCycle.cycle_key == cycle_key)).all()
    planned_cogs = round(
        sum(
            float(row.openai_usd or 0) + float(row.prospeo_usd or 0) + float(row.brave_usd or 0)
            for row in rows
        ),
        2,
    )
    return {
        "basis": "planned",
        "cycle_key": cycle_key,
        "companies_with_cycle": len(rows),
        "paid": sum(1 for row in rows if row.paid),
        "tools_ready": sum(1 for row in rows if row.paid and tools_ready(row)),
        "credits_granted": sum(1 for row in rows if row.credits_granted),
        "planned_cogs_usd": planned_cogs,
    }


def _channel_limits(db: Session) -> list[dict[str, Any]]:
    sellers = db.scalars(
        select(User)
        .join(Campaign, Campaign.seller_id == User.id)
        .where(User.role == UserRole.sdr.value, User.is_active.is_(True))
        .distinct()
    ).all()
    totals = {kind: {"used": 0, "limit": 0} for kind in DEFAULT_LIMITS}
    for seller in sellers:
        snap = daily_limit_snapshot(db, seller.id)
        for kind, row in snap.items():
            totals[kind]["used"] += int(row["used"])
            totals[kind]["limit"] += int(row["limit"])
    return [
        {
            "key": kind,
            "used": row["used"],
            "limit": row["limit"],
            "remaining": max(0, row["limit"] - row["used"]),
            "sellers": len(sellers),
        }
        for kind, row in totals.items()
    ]


def build_support_observability(
    db: Session,
    *,
    refresh_prospeo: bool = False,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    month_start = _month_start(now)
    cycle_key = current_plan_cycle_key()

    company_count = int(db.scalar(select(func.count(Company.id))) or 0)
    active_campaigns = int(
        db.scalar(select(func.count(Campaign.id)).where(Campaign.status == "running")) or 0
    )
    sequences_month = int(
        db.scalar(
            select(func.count(Prospect.id)).where(Prospect.sequence_started_at >= month_start)
        )
        or 0
    )
    messages_today = int(
        db.scalar(
            select(func.count(OutreachMessage.id)).where(
                OutreachMessage.created_at >= day_start,
                OutreachMessage.direction == "outbound",
                OutreachMessage.is_testing.is_(False),
            )
        )
        or 0
    )
    pending_tasks = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(OutreachTask.status == "pending")
        )
        or 0
    )
    overdue_tasks = int(
        db.scalar(
            select(func.count(OutreachTask.id)).where(
                OutreachTask.status == "pending",
                OutreachTask.due_at < now,
            )
        )
        or 0
    )

    jobs, errors = _jobs(db, now)
    openai = openai_diag.build_diagnostics(probe=False)
    for row in (openai.get("recent_errors") or [])[:5]:
        errors.append(
            {
                "source": "openai",
                "label": "OpenAI",
                "message": str(row.get("error_full") or row.get("error_type") or "Error")[:500],
                "at": row.get("timestamp"),
            }
        )
    errors.sort(key=lambda row: str(row.get("at") or ""), reverse=True)

    estimated_total = round(sequences_month * _COGS_PER_SEQUENCE_USD, 4)
    return {
        "generated_at": now.isoformat(),
        "cycle_key": cycle_key,
        "summary": {
            "companies": company_count,
            "active_campaigns": active_campaigns,
            "sequences_started_month": sequences_month,
            "outbound_messages_today": messages_today,
            "pending_tasks": pending_tasks,
            "overdue_tasks": overdue_tasks,
            "estimated_cost_month_usd": estimated_total,
            "estimated_cost_per_sequence_usd": _COGS_PER_SEQUENCE_USD,
        },
        "scheduler": {
            "running": ns.scheduler_running(),
            "real_mode": om.is_real_mode(),
            "jobs_total": len(jobs),
            "jobs_with_errors": sum(1 for job in jobs if job["status"] == "error"),
            "jobs_stale": sum(1 for job in jobs if job["status"] == "stale"),
        },
        "providers": _provider_cards(
            db,
            sequences_month=sequences_month,
            cycle_key=cycle_key,
            refresh_prospeo=refresh_prospeo,
        ),
        "channel_limits": _channel_limits(db),
        "support_inbox": _support_inbox(db),
        "billing_cycle": _billing_cycle_summary(db, cycle_key),
        "jobs": jobs,
        "recent_errors": errors[:12],
        "cost_note": (
            "Estimación: USD 0,025 por persona que inició secuencia "
            "(OpenAI 14/88, Prospeo 70/88, Brave 4/88). "
            "No incluye reintentos extraordinarios ni conversaciones inbound largas."
        ),
    }
