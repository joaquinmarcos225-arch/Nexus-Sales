"""dLocal — checkout redirect para LatAm (ex-Brasil)."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.user import User
from app.services.billing import config as cfg
from app.services.billing import service as billing
from app.services.billing.latam import normalize_country
from app.services.credit_plans import normalize_plan_key, plan_definition

_logger = logging.getLogger("nexus.billing.dlocal")


class DLocalError(RuntimeError):
    pass


def _api_base() -> str:
    return cfg.dlocal_api_base()


def _sign(body: str, *, x_login: str, x_date: str) -> str:
    secret = cfg.dlocal_secret_key()
    msg = f"{x_login}{x_date}{body}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _headers(body: str) -> dict[str, str]:
    login = cfg.dlocal_x_login()
    trans = cfg.dlocal_x_trans_key()
    if not login or not trans or not cfg.dlocal_secret_key():
        raise DLocalError(
            "dLocal no configurado (DLOCAL_X_LOGIN / DLOCAL_X_TRANS_KEY / DLOCAL_SECRET_KEY)"
        )
    x_date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    sig = _sign(body, x_login=login, x_date=x_date)
    return {
        "X-Date": x_date,
        "X-Login": login,
        "X-Trans-Key": trans,
        "X-Version": "2.1",
        "User-Agent": "NexusSales/1.0",
        "Content-Type": "application/json",
        "Authorization": f"V2-HMAC-SHA256, Signature: {sig}",
    }


def create_checkout(
    db: Session,
    *,
    company: Company,
    plan_key: str,
    actor: User,
    country_code: str,
) -> dict[str, str]:
    """
    Crea un pago REDIRECT en dLocal (tarjeta / métodos locales del país).
    El webhook confirma y activa el plan.
    """
    country = normalize_country(country_code)
    if not country:
        raise DLocalError("Elegí un país de LatAm (sin Brasil) para pagar con dLocal.")

    if billing.has_paid_subscription(company) and company.billing_provider == billing.PROVIDER_DLOCAL:
        raise DLocalError("Ya tenés una suscripción activa. Usá «Cambiar plan» en Facturación.")

    plan = normalize_plan_key(plan_key)
    price = cfg.plan_price(plan)
    pdef = plan_definition(plan)
    amount_usd = round(float(price.amount_cents) / 100.0, 2)

    success = f"{cfg.frontend_url()}/creditos?billing=success&provider=dlocal"
    notify = f"{cfg.backend_public_url()}/billing/webhooks/dlocal"

    payload: dict[str, Any] = {
        "amount": amount_usd,
        "currency": "USD",
        "country": country,
        "payment_method_flow": "REDIRECT",
        "payer": {
            "name": (actor.name or actor.first_name or "Nexus Owner").strip(),
            "email": actor.email,
        },
        "order_id": f"nexus-{company.id}-{plan}-{int(datetime.now(UTC).timestamp())}",
        "description": f"Nexus {pdef.label} ({pdef.monthly_contact_credits} créditos/mes)",
        "callback_url": success,
    }
    if notify.startswith("https://"):
        payload["notification_url"] = notify

    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{_api_base()}/payments", headers=_headers(body), content=body)

    if resp.status_code not in (200, 201):
        raise DLocalError(f"dLocal payments: {resp.status_code} {resp.text[:400]}")
    data = resp.json()
    redirect = str(data.get("redirect_url") or data.get("checkout_url") or "")
    if not redirect and isinstance(data.get("payment"), dict):
        redirect = str(data["payment"].get("redirect_url") or "")
    payment_id = str(data.get("id") or data.get("payment_id") or "")
    if not redirect:
        raise DLocalError("dLocal no devolvió redirect_url")

    company.billing_country = country
    company.dlocal_payment_id = payment_id or None
    company.pending_plan = plan
    db.flush()
    _logger.info(
        "dlocal checkout company=%s plan=%s country=%s payment=%s",
        company.id,
        plan,
        country,
        payment_id,
    )
    return {"checkout_url": redirect, "payment_id": payment_id}


def handle_webhook(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    status = str(payload.get("status") or "").upper()
    payment_id = str(payload.get("id") or payload.get("payment_id") or "").strip()
    order_id = str(payload.get("order_id") or "")
    company: Company | None = None
    plan: str | None = None

    if payment_id:
        company = db.scalars(
            select(Company).where(Company.dlocal_payment_id == payment_id)
        ).first()

    if company is None and order_id.startswith("nexus-"):
        parts = order_id.split("-")
        try:
            company = db.get(Company, int(parts[1]))
            plan = parts[2] if len(parts) > 2 else None
        except (ValueError, IndexError):
            company = None

    if company is None:
        return {"ok": False, "reason": "company_not_found"}

    plan = normalize_plan_key(plan or company.pending_plan or company.plan)
    if status in ("PAID", "AUTHORIZED", "SUCCESS", "APPROVED"):
        if not billing.has_paid_subscription(company):
            billing.activate_paid_plan(
                db,
                company.id,
                plan_key=plan,
                provider=billing.PROVIDER_DLOCAL,
                grant_credits=True,
            )
        else:
            billing.renew_paid_cycle(db, company.id, provider=billing.PROVIDER_DLOCAL)
        if payment_id:
            company.dlocal_payment_id = payment_id
        company.pending_plan = None
        db.flush()
        return {"ok": True, "action": "paid", "company_id": company.id}
    if status in ("REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "FAILED"):
        billing.mark_payment_failed(db, company.id)
        return {"ok": True, "action": "failed", "company_id": company.id}
    return {"ok": True, "action": "ignored", "status": status}
