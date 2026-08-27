import os

from fastapi import APIRouter
from sqlalchemy import select

router = APIRouter()

_DEV_JWT_SECRET = "nexus-dev-jwt-secret-change-in-production"


@router.get("", summary="Comprobación de servicio")
def health() -> dict[str, str | bool]:
    from app.core.security import JWT_SECRET
    from app.services import outreach_metrics as om
    from app.services.gmail_automation_flags import gmail_automation_enabled

    from app.services.testing_reset import is_testing_reset_enabled

    cfg = om.outreach_simulation_config()
    skip_demo = (os.getenv("NEXUS_SKIP_DEMO_SEED") or "").strip().lower() in ("1", "true", "yes", "on")
    jwt_ok = bool(JWT_SECRET) and JWT_SECRET != _DEV_JWT_SECRET
    real_mode = cfg["real_mode"]
    prod_ready = bool(real_mode and skip_demo and jwt_ok)
    from app.services import google_oauth as goauth

    return {
        "status": "ok" if prod_ready or not real_mode else "degraded",
        "service": "Nexus Sales API",
        "google_oauth_configured": goauth.oauth_is_configured(),
        "prod_ready": prod_ready,
        "jwt_secret_configured": jwt_ok,
        "demo_seed_disabled": skip_demo,
        "testing_reset_enabled": is_testing_reset_enabled(),
        "real_mode": real_mode,
        "outreach_simulation_disabled": cfg["outreach_simulation_disabled"],
        "sequence_testing_enabled": cfg["sequence_testing_enabled"],
        "env_nexus_real_mode": cfg["env_nexus_real_mode"],
        "env_nexus_disable_outreach_simulation": cfg["env_nexus_disable_outreach_simulation"],
        "env_nexus_enable_sequence_testing": cfg["env_nexus_enable_sequence_testing"],
    }


@router.get("/cogs-metrics", summary="Contadores runtime: enrich móvil vs imports vs WA")
def health_cogs_metrics() -> dict:
    from app.services.lead_sourcing.cogs_runtime_metrics import snapshot

    return snapshot()


@router.get("/go-live", summary="Readiness de deploy (servidor)")
def health_go_live() -> dict:
    from app.services.go_live import assess_server_go_live

    return assess_server_go_live()


@router.get("/openai", summary="Diagnóstico OpenAI (modelo, RPM, errores, fallback dev)")
def health_openai(probe: bool = False) -> dict:
    from app.services import openai_diagnostics as od

    return od.build_diagnostics(probe=probe)


@router.get("/whatsapp", summary="Estado WhatsApp Cloud API (Meta o dry run)")
def health_whatsapp(deep: bool = False) -> dict:
    from app.services.whatsapp_cloud_service import verify_whatsapp_api

    return verify_whatsapp_api(deep=deep)


@router.get("/sequence-playbook", summary="Definición canónica de la secuencia Nexus (toques + reactivación)")
def health_sequence_playbook() -> dict:
    from app.core.sequence_playbook import sequence_playbook_public

    return sequence_playbook_public()


@router.get("/automation", summary="Estado operativo de automatización (jobs, scheduler)")
def health_automation() -> dict:
    from app.database.session import SessionLocal
    from app.models.automation_job_state import AutomationJobState
    from app.services import nexus_scheduler as ns
    from app.services import outreach_metrics as om
    from app.services.gmail_automation_flags import gmail_automation_enabled
    from app.services.sequence_touch_scheduler import sequence_touches_scheduler_enabled
    from app.services.operations_service import _JOB_LABELS

    rows: list[dict] = []
    db = SessionLocal()
    try:
        jobs = db.scalars(select(AutomationJobState).order_by(AutomationJobState.job_key.asc())).all()
        for j in jobs:
            rows.append(
                {
                    "job_key": j.job_key,
                    "label": _JOB_LABELS.get(j.job_key, j.job_key),
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
        "gmail_automation_enabled_env": (os.getenv("ENABLE_GMAIL_AUTOMATION") or "0").strip(),
        "gmail_automation_active": gmail_automation_enabled(),
        "sequence_touches_scheduler_enabled": sequence_touches_scheduler_enabled(),
        "sourcing_refill_enabled_env": (os.getenv("NEXUS_SOURCING_REFILL_ENABLED") or "").strip(),
        "manual_gmail_sync_allowed": True,
        "inbound_reply_poll_interval_sec": (os.getenv("NEXUS_INBOUND_REPLY_POLL_INTERVAL_SEC") or "45").strip(),
        "inbound_auto_reply_tasks": inbound_task_counts,
        "jobs": rows,
    }
