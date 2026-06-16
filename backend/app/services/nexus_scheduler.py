"""Scheduler en background (APScheduler) — activar con NEXUS_AUTOMATION_SCHEDULER=1."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_scheduler: Any = None


def _truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def start_automation_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    if not _truthy("NEXUS_AUTOMATION_SCHEDULER"):
        logger.info("Automation scheduler deshabilitado (NEXUS_AUTOMATION_SCHEDULER).")
        return

    from apscheduler.schedulers.background import BackgroundScheduler

    from app.services import automation_runner as ar
    from app.services.gmail_automation_flags import gmail_automation_enabled

    gmail_on = gmail_automation_enabled()
    g_sec = int(os.getenv("NEXUS_GMAIL_POLL_INTERVAL_SEC", "120"))
    c_sec = int(os.getenv("NEXUS_CALENDAR_SYNC_INTERVAL_SEC", "300"))
    f_sec = int(os.getenv("NEXUS_FOLLOWUPS_POLL_INTERVAL_SEC", "180"))
    i_sec = int(os.getenv("NEXUS_INITIAL_OUTREACH_INTERVAL_SEC", "90"))
    r_sec = int(os.getenv("NEXUS_INBOUND_REPLY_POLL_INTERVAL_SEC", "45"))

    sched = BackgroundScheduler(timezone="UTC")

    def _gmail() -> None:
        try:
            ar.run_gmail_inbound_tick()
        except Exception:
            logger.exception("tick gmail inbound")

    def _cal() -> None:
        try:
            ar.run_calendar_sync_tick()
        except Exception:
            logger.exception("tick calendar")

    def _initial() -> None:
        try:
            ar.run_initial_outreach_tick()
        except Exception:
            logger.exception("tick initial outreach")

    def _fu() -> None:
        try:
            ar.run_followups_tick()
        except Exception:
            logger.exception("tick followups")

    def _inbound_reply() -> None:
        logger.debug("scheduler firing nexus_inbound_auto_reply (interval=%ss)", r_sec)
        try:
            ar.run_inbound_auto_reply_tick()
        except Exception:
            logger.exception("tick inbound auto-reply send")

    if gmail_on:
        sched.add_job(_gmail, "interval", seconds=max(30, g_sec), id="nexus_gmail_inbound", replace_existing=True)
        sched.add_job(
            _inbound_reply,
            "interval",
            seconds=max(30, r_sec),
            id="nexus_inbound_auto_reply",
            replace_existing=True,
        )
    sched.add_job(_cal, "interval", seconds=max(60, c_sec), id="nexus_calendar_sync", replace_existing=True)
    sched.add_job(
        _initial,
        "interval",
        seconds=max(45, i_sec),
        id="nexus_initial_outreach",
        replace_existing=True,
    )
    sched.add_job(_fu, "interval", seconds=max(45, f_sec), id="nexus_followups", replace_existing=True)
    sched.start()
    _scheduler = sched
    logger.info(
        "Automation scheduler iniciado (gmail_on=%s gmail=%ss calendar=%ss initial=%ss followups=%ss inbound_reply=%ss).",
        gmail_on,
        g_sec if gmail_on else "off",
        c_sec,
        i_sec,
        f_sec,
        r_sec if gmail_on else "off",
    )


def shutdown_automation_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("shutdown scheduler")
    _scheduler = None


def scheduler_running() -> bool:
    sched = _scheduler
    if sched is None:
        return False
    return bool(getattr(sched, "running", True))
