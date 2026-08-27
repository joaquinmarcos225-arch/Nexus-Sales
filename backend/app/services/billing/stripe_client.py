"""Stripe Checkout + Customer Portal + webhooks helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.user import User
from app.services.billing import config as cfg
from app.services.billing import service as billing
from app.services.credit_plans import normalize_plan_key

_logger = logging.getLogger("nexus.billing.stripe")

API = "https://api.stripe.com/v1"


class StripeError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    key = cfg.stripe_secret_key()
    if not key:
        raise StripeError("STRIPE_SECRET_KEY no configurado")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _form(data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        out[str(k)] = str(v)
    return out


def create_checkout_session(
    db: Session,
    *,
    company: Company,
    plan_key: str,
    actor: User,
) -> dict[str, str]:
    plan = normalize_plan_key(plan_key)
    price = cfg.plan_price(plan)
    success = f"{cfg.frontend_url()}/creditos?billing=success&provider=stripe"
    cancel = f"{cfg.frontend_url()}/creditos?billing=cancel&provider=stripe"

    customer_id = company.stripe_customer_id
    if not customer_id:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{API}/customers",
                headers=_headers(),
                data=_form(
                    {
                        "email": actor.email,
                        "name": company.name,
                        "metadata[company_id]": company.id,
                        "metadata[nexus_company_id]": company.id,
                    }
                ),
            )
        if resp.status_code not in (200, 201):
            raise StripeError(f"Crear customer: {resp.status_code} {resp.text[:300]}")
        customer_id = str(resp.json().get("id") or "")
        company.stripe_customer_id = customer_id
        db.flush()

    # Si ya hay suscripción Stripe cobrada, el cambio de plan va por /change-plan
    if billing.has_paid_subscription(company) and company.stripe_subscription_id:
        raise StripeError(
            "Ya tenés una suscripción activa. Usá «Cambiar plan» en Facturación."
        )

    line: dict[str, Any]
    if price.stripe_price_id:
        line = {"price": price.stripe_price_id, "quantity": 1}
    else:
        line = {
            "price_data[currency]": cfg.stripe_currency(),
            "price_data[unit_amount]": price.amount_cents,
            "price_data[recurring][interval]": "month",
            "price_data[product_data][name]": f"Nexus {plan.title()} — {price.amount_cents}",
            "price_data[product_data][metadata][plan]": plan,
            "quantity": 1,
        }
        # Fix product name to be human
        from app.services.credit_plans import plan_definition

        pdef = plan_definition(plan)
        line["price_data[product_data][name]"] = (
            f"Nexus {pdef.label} ({pdef.monthly_contact_credits:,} créditos/mes)"
        )

    payload: dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "success_url": success,
        "cancel_url": cancel,
        "client_reference_id": str(company.id),
        "metadata[company_id]": company.id,
        "metadata[plan]": plan,
        "subscription_data[metadata][company_id]": company.id,
        "subscription_data[metadata][plan]": plan,
        "allow_promotion_codes": "true",
    }
    if price.stripe_price_id:
        payload["line_items[0][price]"] = price.stripe_price_id
        payload["line_items[0][quantity]"] = 1
    else:
        payload["line_items[0][price_data][currency]"] = cfg.stripe_currency()
        payload["line_items[0][price_data][unit_amount]"] = price.amount_cents
        payload["line_items[0][price_data][recurring][interval]"] = "month"
        payload["line_items[0][price_data][product_data][name]"] = line[
            "price_data[product_data][name]"
        ]
        payload["line_items[0][price_data][product_data][metadata][plan]"] = plan
        payload["line_items[0][quantity]"] = 1

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{API}/checkout/sessions", headers=_headers(), data=_form(payload))
    if resp.status_code not in (200, 201):
        raise StripeError(f"Checkout: {resp.status_code} {resp.text[:400]}")
    data = resp.json()
    url = str(data.get("url") or "")
    if not url:
        raise StripeError("Stripe no devolvió URL de checkout")
    return {"checkout_url": url, "session_id": str(data.get("id") or "")}


def create_portal_session(*, company: Company) -> dict[str, str]:
    if not company.stripe_customer_id:
        raise StripeError("Esta empresa no tiene cliente Stripe")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{API}/billing_portal/sessions",
            headers=_headers(),
            data=_form(
                {
                    "customer": company.stripe_customer_id,
                    "return_url": f"{cfg.frontend_url()}/creditos",
                }
            ),
        )
    if resp.status_code not in (200, 201):
        raise StripeError(f"Portal: {resp.status_code} {resp.text[:300]}")
    url = str(resp.json().get("url") or "")
    if not url:
        raise StripeError("Stripe portal sin URL")
    return {"portal_url": url}


def construct_event(payload: bytes, sig_header: str) -> dict[str, Any]:
    """
    Verificación liviana del webhook.
    Si hay STRIPE_WEBHOOK_SECRET, exige firma Stripe (HMAC).
    """
    import hmac
    import hashlib
    import time

    secret = cfg.stripe_webhook_secret()
    if not secret:
        # Dev: aceptar JSON sin firma
        import json

        return json.loads(payload.decode("utf-8"))

    if not sig_header:
        raise StripeError("Falta Stripe-Signature")

    parts = {}
    for item in sig_header.split(","):
        k, _, v = item.partition("=")
        parts.setdefault(k.strip(), []).append(v.strip())
    timestamp = (parts.get("t") or [None])[0]
    signatures = parts.get("v1") or []
    if not timestamp or not signatures:
        raise StripeError("Firma Stripe inválida")
    if abs(time.time() - int(timestamp)) > 300:
        raise StripeError("Webhook Stripe expirado")

    signed = f"{timestamp}.".encode("utf-8") + payload
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise StripeError("Firma Stripe no coincide")

    import json

    return json.loads(payload.decode("utf-8"))


def handle_webhook_event(db: Session, event: dict[str, Any]) -> dict[str, Any]:
    etype = str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        company_id = int(
            obj.get("client_reference_id")
            or (obj.get("metadata") or {}).get("company_id")
            or 0
        )
        plan = normalize_plan_key((obj.get("metadata") or {}).get("plan"))
        customer = str(obj.get("customer") or "")
        sub = str(obj.get("subscription") or "")
        if not company_id:
            return {"ok": False, "reason": "missing company_id"}
        billing.activate_paid_plan(
            db,
            company_id,
            plan_key=plan,
            provider=billing.PROVIDER_STRIPE,
            stripe_customer_id=customer or None,
            stripe_subscription_id=sub or None,
            grant_credits=True,
        )
        return {"ok": True, "action": "activated", "company_id": company_id}

    if etype in ("invoice.paid", "invoice.payment_succeeded"):
        sub = str(obj.get("subscription") or "")
        customer = str(obj.get("customer") or "")
        company = None
        if sub:
            company = billing.find_company_by_stripe_subscription(db, sub)
        if company is None and customer:
            company = billing.find_company_by_stripe_customer(db, customer)
        if company is None:
            return {"ok": False, "reason": "company_not_found"}
        # Primer invoice a veces llega junto con checkout — renew es no-op si ya acreditó el ciclo
        billing_reason = str(obj.get("billing_reason") or "")
        period_end = None
        lines = ((obj.get("lines") or {}).get("data") or [])
        if lines:
            period = (lines[0].get("period") or {}).get("end")
            if period:
                period_end = datetime.fromtimestamp(int(period), tz=UTC)
        if billing_reason == "subscription_create":
            # checkout.session.completed ya activa; no duplicar
            return {"ok": True, "action": "skip_create_invoice"}
        billing.renew_paid_cycle(
            db,
            company.id,
            provider=billing.PROVIDER_STRIPE,
            period_end=period_end,
        )
        return {"ok": True, "action": "renewed", "company_id": company.id}

    if etype in ("invoice.payment_failed", "invoice.payment_action_required"):
        sub = str(obj.get("subscription") or "")
        customer = str(obj.get("customer") or "")
        company = None
        if sub:
            company = billing.find_company_by_stripe_subscription(db, sub)
        if company is None and customer:
            company = billing.find_company_by_stripe_customer(db, customer)
        if company is None:
            return {"ok": False, "reason": "company_not_found"}
        billing.mark_payment_failed(db, company.id)
        return {"ok": True, "action": "past_due", "company_id": company.id}

    if etype in ("customer.subscription.deleted", "customer.subscription.canceled"):
        sub = str(obj.get("id") or "")
        company = billing.find_company_by_stripe_subscription(db, sub) if sub else None
        if company is None:
            return {"ok": False, "reason": "company_not_found"}
        billing.mark_canceled(db, company.id)
        return {"ok": True, "action": "canceled", "company_id": company.id}

    if etype == "customer.subscription.updated":
        sub = str(obj.get("id") or "")
        company = billing.find_company_by_stripe_subscription(db, sub) if sub else None
        if company is None:
            return {"ok": False, "reason": "company_not_found"}
        status = str(obj.get("status") or "")
        if status == "active":
            company.billing_status = billing.STATUS_ACTIVE
        elif status in ("past_due", "unpaid"):
            company.billing_status = billing.STATUS_PAST_DUE
        elif status in ("canceled", "incomplete_expired"):
            company.billing_status = billing.STATUS_CANCELED
        meta_plan = (obj.get("metadata") or {}).get("plan")
        if meta_plan:
            company.plan = normalize_plan_key(meta_plan)
        db.flush()
        return {"ok": True, "action": "subscription_updated", "company_id": company.id}

    return {"ok": True, "action": "ignored", "type": etype}
