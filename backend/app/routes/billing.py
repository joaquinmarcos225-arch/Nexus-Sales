"""Billing self-serve: checkout Stripe/MP, cambio de plan, webhooks."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.deps import require_permission
from app.core.permissions import Permission
from app.database.session import get_db
from app.deps import get_company
from app.models.company import Company
from app.models.user import User
from app.services.billing import config as billing_config
from app.services.billing import dlocal_client as dlocal
from app.services.billing import mercadopago_client as mp
from app.services.billing import service as billing
from app.services.billing import stripe_client as stripe
from app.services.billing.latam import is_latam_ex_br, normalize_country
from app.services.credit_plans import normalize_plan_key
from app.services.credits import CreditError

router = APIRouter(tags=["billing"])


class BillingCheckoutCreate(BaseModel):
    plan: str = Field(..., description="starter | growth | scaler | elite")
    provider: Literal["dlocal", "stripe", "mercadopago", "dev"] = "dlocal"
    country: str | None = Field(default=None, description="ISO país LatAm (ej. AR, MX, CL)")


class BillingChangePlan(BaseModel):
    plan: str = Field(..., description="Nuevo plan (upgrade inmediato / downgrade próximo ciclo)")


class BillingCheckoutRead(BaseModel):
    checkout_url: str | None = None
    provider: str
    session_id: str | None = None
    preapproval_id: str | None = None
    message: str | None = None
    activated: bool = False


class BillingStatusRead(BaseModel):
    company_id: int
    plan: str
    plan_label: str
    plan_contact_credits: int
    pending_plan: str | None = None
    pending_plan_label: str | None = None
    billing_provider: str | None = None
    billing_status: str
    billing_country: str | None = None
    billing_period_end: datetime | None = None
    last_payment_at: datetime | None = None
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    mp_preapproval_id: str | None = None
    has_paid_subscription: bool = False
    providers: dict[str, bool]
    plans: list[dict[str, Any]]
    latam_countries: list[dict[str, str]] = []
    can_self_serve: bool = True


class BillingChangePlanRead(BaseModel):
    action: str
    plan: str
    pending_plan: str | None = None
    credits_added: int = 0
    message: str
    requires_checkout: bool = False
    checkout_url: str | None = None


class BillingPortalRead(BaseModel):
    portal_url: str


@router.get("/companies/{company_id}/billing", response_model=BillingStatusRead)
def get_billing_status(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.BILLING_MANAGE)),
    company: Company = Depends(get_company),
) -> BillingStatusRead:
    _ = user
    data = billing.billing_summary(company)
    return BillingStatusRead.model_validate(data)


@router.post("/companies/{company_id}/billing/checkout", response_model=BillingCheckoutRead)
def create_billing_checkout(
    company_id: int,
    body: BillingCheckoutCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.BILLING_MANAGE)),
    company: Company = Depends(get_company),
) -> BillingCheckoutRead:
    plan = normalize_plan_key(body.plan)
    provider = body.provider
    country = normalize_country(body.country) or normalize_country(company.billing_country)

    # LatAm (ex-BR): forzar dLocal como camino principal
    if provider == "dlocal" or (country and is_latam_ex_br(country) and provider not in ("dev",)):
        provider = "dlocal"

    if billing.has_paid_subscription(company) and normalize_plan_key(company.plan) == plan:
        raise HTTPException(
            status_code=409,
            detail="Ya tenés ese plan activo. Para más créditos, pasá a un plan superior.",
        )

    try:
        if provider == "dlocal":
            if not country:
                raise HTTPException(
                    status_code=400,
                    detail="Elegí el país de facturación (LatAm, sin Brasil) antes de suscribirte.",
                )
            if not billing_config.dlocal_configured():
                if billing_config.billing_dev_mode():
                    company.billing_country = country
                    billing.activate_paid_plan(
                        db,
                        company.id,
                        plan_key=plan,
                        provider=billing.PROVIDER_DEV,
                        actor_user_id=user.id,
                        grant_credits=True,
                    )
                    db.commit()
                    return BillingCheckoutRead(
                        provider="dev",
                        activated=True,
                        message=(
                            f"Plan {plan} activado en modo desarrollo "
                            f"(dLocal aún sin claves; país={country})."
                        ),
                    )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "dLocal no configurado. Pedile a ops: "
                        "DLOCAL_X_LOGIN, DLOCAL_X_TRANS_KEY, DLOCAL_SECRET_KEY."
                    ),
                )
            result = dlocal.create_checkout(
                db,
                company=company,
                plan_key=plan,
                actor=user,
                country_code=country,
            )
            db.commit()
            return BillingCheckoutRead(
                checkout_url=result["checkout_url"],
                provider="dlocal",
                preapproval_id=result.get("payment_id"),
            )

        if provider == "mercadopago":
            if not billing_config.mp_configured():
                raise HTTPException(
                    status_code=503,
                    detail="Mercado Pago no configurado (MERCADOPAGO_ACCESS_TOKEN)",
                )
            result = mp.create_preapproval(db, company=company, plan_key=plan, actor=user)
            db.commit()
            return BillingCheckoutRead(
                checkout_url=result["checkout_url"],
                provider="mercadopago",
                preapproval_id=result.get("preapproval_id"),
            )

        if provider == "stripe":
            if not billing_config.stripe_configured():
                raise HTTPException(
                    status_code=503,
                    detail="Stripe no configurado (STRIPE_SECRET_KEY)",
                )
            result = stripe.create_checkout_session(
                db, company=company, plan_key=plan, actor=user
            )
            db.commit()
            return BillingCheckoutRead(
                checkout_url=result["checkout_url"],
                provider="stripe",
                session_id=result.get("session_id"),
            )

        if provider == "dev":
            if not billing_config.billing_dev_mode():
                raise HTTPException(
                    status_code=403,
                    detail="BILLING_DEV_MODE no está activo en el servidor",
                )
            if country:
                company.billing_country = country
            billing.activate_paid_plan(
                db,
                company.id,
                plan_key=plan,
                provider=billing.PROVIDER_DEV,
                actor_user_id=user.id,
                grant_credits=True,
            )
            db.commit()
            return BillingCheckoutRead(
                provider="dev",
                activated=True,
                message=f"Plan {plan} activado en modo desarrollo (sin cobro real).",
            )

        raise HTTPException(status_code=400, detail="provider inválido")
    except (stripe.StripeError, mp.MercadoPagoError, dlocal.DLocalError, CreditError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/companies/{company_id}/billing/change-plan", response_model=BillingChangePlanRead)
def change_billing_plan(
    company_id: int,
    body: BillingChangePlan,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.BILLING_MANAGE)),
    company: Company = Depends(get_company),
) -> BillingChangePlanRead:
    """
    Con suscripción de pago real: upgrade inmediato / downgrade al próximo ciclo.
    Sin pago (demo/dev o sin suscripción): el front debe llamar /billing/checkout.
    """
    _ = company_id
    if not billing.has_paid_subscription(company):
        raise HTTPException(
            status_code=400,
            detail=(
                "Todavía no hay suscripción de pago activa. "
                "Elegí un plan y usá «Suscribirme» (checkout con tarjeta) para activarlo."
            ),
        )
    try:
        result = billing.change_plan_self_serve(
            db,
            company.id,
            body.plan,
            actor_user_id=user.id,
        )
        db.commit()
    except CreditError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return BillingChangePlanRead(
        action=result["action"],
        plan=result["plan"],
        pending_plan=result.get("pending_plan"),
        credits_added=int(result.get("credits_added") or 0),
        message=str(result["message"]),
        requires_checkout=False,
        checkout_url=None,
    )


@router.post("/companies/{company_id}/billing/portal", response_model=BillingPortalRead)
def billing_customer_portal(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.BILLING_MANAGE)),
    company: Company = Depends(get_company),
) -> BillingPortalRead:
    _ = db, user
    try:
        result = stripe.create_portal_session(company=company)
    except stripe.StripeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BillingPortalRead(portal_url=result["portal_url"])


@router.post("/billing/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    payload = await request.body()
    sig = request.headers.get("stripe-signature") or ""
    try:
        event = stripe.construct_event(payload, sig)
        result = stripe.handle_webhook_event(db, event)
        db.commit()
        return result
    except stripe.StripeError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/billing/webhooks/dlocal")
async def dlocal_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        result = dlocal.handle_webhook(db, payload if isinstance(payload, dict) else {})
        db.commit()
        return result
    except dlocal.DLocalError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/billing/webhooks/mercadopago")
async def mercadopago_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = dict(request.query_params)
    # MP a veces manda topic + id por query
    if "topic" in request.query_params and "id" in request.query_params:
        payload = {
            "type": request.query_params.get("topic"),
            "data": {"id": request.query_params.get("id")},
        }
    try:
        result = mp.handle_webhook(db, payload if isinstance(payload, dict) else {})
        db.commit()
        return result
    except mp.MercadoPagoError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
