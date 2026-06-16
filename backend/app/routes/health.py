import os

from fastapi import APIRouter
from sqlalchemy import select

router = APIRouter()


@router.get("", summary="Comprobación de servicio")
def health() -> dict[str, str | bool]:
    from app.services import outreach_metrics as om

    from app.services.testing_reset import is_testing_reset_enabled

    cfg = om.outreach_simulation_config()
    return {
        "status": "ok",
        "service": "Nexus Sales API",
        "testing_reset_enabled": is_testing_reset_enabled(),
        "real_mode": cfg["real_mode"],
        "outreach_simulation_disabled": cfg["outreach_simulation_disabled"],
        "sequence_testing_enabled": cfg["sequence_testing_enabled"],
        "env_nexus_real_mode": cfg["env_nexus_real_mode"],
        "env_nexus_disable_outreach_simulation": cfg["env_nexus_disable_outreach_simulation"],
        "env_nexus_enable_sequence_testing": cfg["env_nexus_enable_sequence_testing"],
    }


@router.get("/openai", summary="Diagnóstico OpenAI (modelo, RPM, errores, fallback dev)")
def health_openai(probe: bool = False) -> dict:
    from app.services import openai_diagnostics as od

    return od.build_diagnostics(probe=probe)


@router.get("/automation", summary="Estado operativo de automatización (jobs, scheduler)")
def health_automation() -> dict:
    from app.database.session import SessionLocal
    from app.models.automation_job_state import AutomationJobState
    from app.services import nexus_scheduler as ns
    from app.services import outreach_metrics as om

    rows: list[dict] = []
    db = SessionLocal()
    try:
        jobs = db.scalars(select(AutomationJobState).order_by(AutomationJobState.job_key.asc())).all()
        for j in jobs:
            rows.append(
                {
                    "job_key": j.job_key,
                    "last_started_at": j.last_started_at.isoformat() if j.last_started_at else None,
                    "last_finished_at": j.last_finished_at.isoformat() if j.last_finished_at else None,
                    "last_success_at": j.last_success_at.isoformat() if j.last_success_at else None,
                    "last_error": j.last_error,
                    "run_count": j.run_count,
                    "last_result_meta": j.last_result_meta,
                }
            )
    finally:
        db.close()

    inbound_task_counts: dict[str, int] = {}
    db2 = SessionLocal()
    try:
        from app.services.inbound_auto_reply import count_inbound_auto_reply_tasks

        inbound_task_counts = count_inbound_auto_reply_tasks(db2)
    except Exception:
        inbound_task_counts = {}
    finally:
        db2.close()

    return {
        "real_mode": om.is_real_mode(),
        "scheduler_running": ns.scheduler_running(),
        "scheduler_enabled_env": (os.getenv("NEXUS_AUTOMATION_SCHEDULER") or "").strip(),
        "auto_send_enabled_env": (os.getenv("NEXUS_AUTO_SEND_ENABLED") or "").strip(),
        "inbound_auto_reply_enabled_env": (os.getenv("NEXUS_INBOUND_AUTO_REPLY") or "1").strip(),
        "inbound_reply_poll_interval_sec": (os.getenv("NEXUS_INBOUND_REPLY_POLL_INTERVAL_SEC") or "45").strip(),
        "inbound_auto_reply_tasks": inbound_task_counts,
        "jobs": rows,
    }
