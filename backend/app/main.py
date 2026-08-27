from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar backend/.env ANTES del resto de imports de app (misma ruta que uvicorn usa).
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_DOTENV_LOADED = load_dotenv(_ENV_FILE, override=True)

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.database.seed import prepare_production_workspace, seed_demo_if_empty
from app.database.session import SessionLocal, init_db
from app.services import outreach_metrics as om
from app.routes.analytics import analytics_dashboard_router, router as analytics_router
from app.routes.companies import router as companies_router
from app.routes.credits import router as credits_router
from app.routes.billing_ops import router as billing_ops_router
from app.routes.operations import router as operations_router
from app.routes.campaigns import router as campaigns_router
from app.routes.sequence_templates import router as sequence_templates_router
from app.routes.go_live import router as go_live_router
from app.routes.health import router as health_router
from app.routes.outreach import router as outreach_router
from app.routes.outreach_tasks import router as outreach_tasks_router
from app.routes.meetings import router as meetings_router
from app.routes.products import router as products_router
from app.routes.prospects import router as prospects_router
from app.routes.lead_sourcing import router as lead_sourcing_router
from app.routes.auth import router as auth_router
from app.routes.prospect_sequence import router as prospect_sequence_router
from app.routes.prospect_ownership import router as prospect_ownership_router
from app.routes.teams import router as teams_router
from app.routes.users import router as users_router
from app.routes.connections import router as connections_router
from app.routes.auth_google import router as auth_google_router
from app.routes.gmail import router as gmail_router
from app.routes.google_calendar import router as google_calendar_router
from app.routes.onboarding import router as onboarding_router
from app.routes.dev_testing import router as dev_testing_router
from app.routes.auth_crm import router as auth_crm_router
from app.routes.crm import router as crm_router
from app.routes.whatsapp_webhooks import router as whatsapp_webhooks_router
from app.routes.extension import router as extension_router
from app.routes.support import router as support_router
from app.routes.notifications import router as notifications_router
from app.services.gmail_automation_flags import log_startup_gmail_automation_flag
from app.middleware.dashboard_http import dashboard_http_guard
from app.services.nexus_scheduler import shutdown_automation_scheduler, start_automation_scheduler

_log_level = (os.getenv("NEXUS_LOG_LEVEL") or "INFO").strip().upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

from app.services.lead_sourcing.providers.web_search_backends import (
    configured_backend,
    legacy_google_search_env_present,
)
from app.services.lead_sourcing.env_config import getenv

_logger = logging.getLogger("nexus.startup")
_web_backend = configured_backend()
if legacy_google_search_env_present() and _web_backend is None:
    _logger.warning(
        "[lead-sourcing] GOOGLE_SEARCH_API_KEY/ENGINE_ID detectados pero IGNORADOS. "
        "Company sourcing usa Brave/SerpAPI — agregá BRAVE_SEARCH_API_KEY en backend/.env"
    )
_logger.info(
    "[lead-sourcing] dotenv loaded=%s | web_search=%s (%s) prospeo=%s",
    _DOTENV_LOADED,
    _web_backend is not None,
    _web_backend.label if _web_backend else "none",
    bool((os.getenv("PROSPEO_API_KEY") or "").strip()),
)
_logger.info(
    "[oauth] env_file=%s | client_id_set=%s | client_secret_set=%s",
    _ENV_FILE,
    bool((os.getenv("GOOGLE_CLIENT_ID") or "").strip()),
    bool((os.getenv("GOOGLE_CLIENT_SECRET") or "").strip()),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as db:
        skip = (os.getenv("NEXUS_SKIP_DEMO_SEED") or "").strip().lower() in ("1", "true", "yes", "on")
        if om.is_real_mode():
            skip = True
        if not skip:
            seed_demo_if_empty(db)
        else:
            prepare_production_workspace(db)
        db.commit()
    log_startup_gmail_automation_flag()
    start_automation_scheduler()
    yield
    shutdown_automation_scheduler()


app = FastAPI(
    title="Nexus Sales API",
    description="Nexus Sales — fase 4 (prospectos por campaña, simulados).",
    version="0.4.0",
    lifespan=lifespan,
)

_diag_logger = logging.getLogger("lead_sourcing.diag")


@app.middleware("http")
async def dashboard_http_middleware(request: Request, call_next):
    return await dashboard_http_guard(request, call_next)


@app.middleware("http")
async def lead_sourcing_http_diag(request: Request, call_next):
    """Diagnóstico: primer log al recibir HTTP (antes de deps y handler)."""
    path = request.url.path
    if "lead-sourcing" not in path:
        return await call_next(request)
    t0 = time.perf_counter()
    _diag_logger.warning(
        "[LS-DIAG] >>> HTTP RECEIVED %s %s client=%s",
        request.method,
        path,
        request.client.host if request.client else "?",
    )
    try:
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _diag_logger.warning(
            "[LS-DIAG] <<< HTTP DONE %s %s status=%s elapsed_ms=%s",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
        )
        return response
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        _diag_logger.warning(
            "[LS-DIAG] <<< HTTP ERROR %s %s elapsed_ms=%s err=%s",
            request.method,
            path,
            elapsed_ms,
            exc,
        )
        raise


def _cors_origins() -> list[str]:
    raw = (os.getenv("NEXUS_CORS_ORIGINS") or "").strip()
    defaults = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://[::1]:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://[::1]:5174",
    ]
    if not raw:
        frontend = (os.getenv("NEXUS_FRONTEND_URL") or "").strip().rstrip("/")
        if frontend and frontend not in defaults:
            defaults.append(frontend)
        return defaults
    return [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(go_live_router)
app.include_router(auth_router)
app.include_router(onboarding_router)
app.include_router(companies_router)
app.include_router(analytics_dashboard_router)
app.include_router(analytics_router)
app.include_router(users_router)
app.include_router(teams_router)
app.include_router(prospect_ownership_router)
app.include_router(prospect_sequence_router)
app.include_router(auth_google_router)
app.include_router(gmail_router)
app.include_router(google_calendar_router)
app.include_router(connections_router)
app.include_router(products_router)
app.include_router(credits_router)
app.include_router(billing_ops_router)
app.include_router(campaigns_router)
app.include_router(sequence_templates_router)
app.include_router(prospects_router)
app.include_router(lead_sourcing_router)
app.include_router(outreach_router)
app.include_router(outreach_tasks_router)
app.include_router(meetings_router)
app.include_router(operations_router)
app.include_router(auth_crm_router)
app.include_router(crm_router)
app.include_router(whatsapp_webhooks_router)
app.include_router(extension_router)
app.include_router(support_router)
app.include_router(notifications_router)
app.include_router(dev_testing_router)
