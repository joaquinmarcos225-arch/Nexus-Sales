"""Evaluación de readiness para vender (servidor + workspace)."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User
from app.services.onboarding import workspace_signup_allowed

_DEV_JWT = "nexus-dev-jwt-secret-change-in-production"

_PLACEHOLDER_PRODUCT = "describí qué vende tu empresa"


def _env_ok(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def assess_server_go_live() -> dict[str, Any]:
    from app.core.security import JWT_SECRET
    from app.services import outreach_metrics as om

    cfg = om.outreach_simulation_config()
    skip_demo = _env_ok("NEXUS_SKIP_DEMO_SEED")
    jwt_ok = bool(JWT_SECRET) and JWT_SECRET != _DEV_JWT
    real_mode = bool(cfg.get("real_mode"))
    prod_ready = bool(real_mode and skip_demo and jwt_ok)

    checks = [
        {
            "id": "real_mode",
            "label": "Modo real (NEXUS_REAL_MODE)",
            "ok": real_mode,
            "hint": "Sin simulaciones de outreach en producción.",
        },
        {
            "id": "jwt",
            "label": "JWT secreto configurado",
            "ok": jwt_ok,
            "hint": "Definí NEXUS_JWT_SECRET distinto al default de dev.",
        },
        {
            "id": "demo_seed",
            "label": "Seed demo desactivado",
            "ok": skip_demo,
            "hint": "NEXUS_SKIP_DEMO_SEED=1 en prod.",
        },
        {
            "id": "openai",
            "label": "OpenAI configurado",
            "ok": _env_ok("OPENAI_API_KEY"),
            "hint": "Mensajes IA y respuestas inbound.",
        },
        {
            "id": "prospeo",
            "label": "Prospeo configurado",
            "ok": _env_ok("PROSPEO_API_KEY"),
            "hint": "Búsqueda de prospectos y WhatsApp (enrich-person).",
        },
        {
            "id": "google_oauth",
            "label": "Google OAuth configurado",
            "ok": _env_ok("GOOGLE_CLIENT_ID") and _env_ok("GOOGLE_CLIENT_SECRET"),
            "hint": "Gmail + Calendar por SDR.",
        },
        {
            "id": "signup",
            "label": "Alta de clientes habilitada",
            "ok": workspace_signup_allowed(),
            "hint": "NEXUS_ALLOW_WORKSPACE_SIGNUP=1 para /registro.",
        },
    ]
    pending = [c for c in checks if not c["ok"]]
    return {
        "prod_ready": prod_ready,
        "ready": prod_ready and not pending,
        "checks": checks,
        "pending_count": len(pending),
    }


def assess_company_go_live(db: Session, company_id: int) -> dict[str, Any]:
    company = db.get(Company, company_id)
    if company is None:
        return {"ready": False, "checks": [], "pending_count": 0}

    products = db.scalars(select(Product).where(Product.company_id == company_id)).all()
    product_ok = False
    for p in products:
        desc = (p.description or "").strip().lower()
        if p.is_active and desc and _PLACEHOLDER_PRODUCT not in desc:
            product_ok = True
            break
    if not product_ok and products:
        # Al menos un producto activo aunque falte editar copy.
        product_ok = any(p.is_active for p in products)

    campaign_count = db.scalar(
        select(func.count()).select_from(Campaign).where(Campaign.company_id == company_id)
    ) or 0

    sdr_count = db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.company_id == company_id,
            User.role == UserRole.sdr.value,
            User.is_active.is_(True),
        )
    ) or 0

    wallet = db.scalars(select(CreditWallet).where(CreditWallet.company_id == company_id)).first()
    pool = int(wallet.total_balance or 0) if wallet else 0
    assigned = int(
        db.scalar(
            select(func.coalesce(func.sum(SellerCreditAllocation.allocated_balance), 0)).where(
                SellerCreditAllocation.company_id == company_id
            )
        )
        or 0
    )
    credits_ok = pool > 0 or assigned > 0

    checks = [
        {
            "id": "product",
            "label": "Producto cargado",
            "ok": bool(products),
            "hint": "Completá nombre y descripción en Productos.",
        },
        {
            "id": "product_copy",
            "label": "Descripción de producto usable",
            "ok": product_ok,
            "hint": "La IA necesita saber qué vendés (no el texto placeholder).",
        },
        {
            "id": "credits",
            "label": "Créditos disponibles",
            "ok": credits_ok,
            "hint": "Asigná créditos al SDR en Créditos (mín. ~30 para arrancar).",
        },
        {
            "id": "sdr",
            "label": "Usuario SDR creado",
            "ok": sdr_count > 0,
            "hint": "Creá un vendedor en Equipo (no uses sdr@test.com en prod).",
        },
        {
            "id": "campaign",
            "label": "Al menos una campaña",
            "ok": int(campaign_count) > 0,
            "hint": "Plantilla LinkedIn → Email → WhatsApp.",
        },
    ]
    pending = [c for c in checks if not c["ok"]]
    return {
        "company_id": company_id,
        "company_name": company.name,
        "plan": company.plan,
        "credit_pool": pool,
        "credits_assigned": assigned,
        "sdr_count": int(sdr_count),
        "campaign_count": int(campaign_count),
        "ready": len(pending) == 0,
        "checks": checks,
        "pending_count": len(pending),
    }
