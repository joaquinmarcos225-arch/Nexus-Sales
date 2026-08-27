import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.user import User
from app.services.billing import config as cfg
from app.services.billing import service as billing
from app.services.credit_plans import normalize_plan_key, plan_definition

_logger = logging.getLogger("nexus.billing.mercadopago")

API = "https://api.mercadopago.com"


class MercadoPagoError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    token = cfg.mp_access_token()
    if not token:
        raise MercadoPagoError("MERCADOPAGO_ACCESS_TOKEN no configurado")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def create_preapproval(
    db: Session,
    *,
    company: Company,
    plan_key: str,
    actor: User,
) -> dict[str, str]:
    """
    Checkout de Mercado Pago (Preference / Checkout Pro).

    La app con Checkout + Pagos no soporta Suscripciones (preapproval → 500).
    Cobramos el ciclo con Preference; el webhook de payment activa el plan.
    """
    plan = normalize_plan_key(plan_key)
    price = cfg.plan_price(plan)
    pdef = plan_definition(plan)

    if billing.has_paid_subscription(company) and company.billing_provider == billing.PROVIDER_MP:
        raise MercadoPagoError(
            "Ya tenés una suscripción activa. Usá «Cambiar plan» en Facturación."
        )

    success = f"{cfg.frontend_url()}/creditos?billing=success&provider=mercadopago"
    cancel = f"{cfg.frontend_url()}/creditos?billing=cancel&provider=mercadopago"
    notify = f"{cfg.backend_public_url()}/billing/webhooks/mercadopago"
    amount = float(price.mp_amount)
    if amount <= 0:
        raise MercadoPagoError("Monto de plan inválido para Mercado Pago")

    payload: dict[str, Any] = {
        "items": [
            {
                "id": plan,
                "title": f"Nexus {pdef.label} — {pdef.monthly_contact_credits} créditos/mes",
                "quantity": 1,
                "currency_id": cfg.mp_currency(),
                "unit_price": amount,
            }
        ],
        "payer": {"email": actor.email},
        "back_urls": {
            "success": success,
            "failure": cancel,
            "pending": success,
        },
        "external_reference": f"nexus-company-{company.id}-plan-{plan}",
        "metadata": {
            "company_id": str(company.id),
            "plan": plan,
        },
        "statement_descriptor": "NEXUS",
    }
    # auto_return solo con HTTPS público (localhost suele romper)
    if success.startswith("https://"):
        payload["auto_return"] = "approved"
    if notify.startswith("https://"):
        payload["notification_url"] = notify

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{API}/checkout/preferences", headers=_headers(), json=payload)

    if resp.status_code not in (200, 201):
        raise MercadoPagoError(f"Checkout Preference: {resp.status_code} {resp.text[:400]}")
    data = resp.json()
    pref_id = str(data.get("id") or "")
    token = cfg.mp_access_token()
    if token.startswith("TEST-"):
        init_point = str(data.get("sandbox_init_point") or data.get("init_point") or "")
    else:
        init_point = str(data.get("init_point") or data.get("sandbox_init_point") or "")
    if not init_point:
        raise MercadoPagoError("Mercado Pago no devolvió init_point")
    if pref_id:
        company.mp_preapproval_id = pref_id
        company.mp_payer_email = actor.email
        db.flush()
    _logger.info(
        "mp preference created company=%s plan=%s pref=%s",
        company.id,
        plan,
        pref_id,
    )
    return {
        "checkout_url": init_point,
        "preapproval_id": pref_id,
    }


def _fetch_preapproval(preapproval_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{API}/preapproval/{preapproval_id}", headers=_headers())
    if resp.status_code != 200:
        raise MercadoPagoError(f"Get preapproval: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def _fetch_payment(payment_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(f"{API}/v1/payments/{payment_id}", headers=_headers())
    if resp.status_code != 200:
        raise MercadoPagoError(f"Get payment: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def _company_from_external_ref(db: Session, ref: str) -> tuple[Company | None, str | None]:
    # nexus-company-{id}-plan-{plan}
    ref = (ref or "").strip()
    if not ref.startswith("nexus-company-"):
        return None, None
    parts = ref.split("-")
    # nexus, company, {id}, plan, {plan}
    try:
        idx = parts.index("company")
        company_id = int(parts[idx + 1])
        plan = None
        if "plan" in parts:
            plan = parts[parts.index("plan") + 1]
        company = db.get(Company, company_id)
        return company, plan
    except (ValueError, IndexError):
        return None, None


def handle_webhook(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """
    MP envía topic/type + data.id.
    Cubremos preapproval y payment.
    """
    topic = str(payload.get("type") or payload.get("topic") or "").lower()
    data = payload.get("data") or {}
    resource_id = str(data.get("id") or payload.get("id") or "").strip()

    if topic in ("subscription_preapproval", "preapproval") and resource_id:
        pre = _fetch_preapproval(resource_id)
        status = str(pre.get("status") or "").lower()
        company = billing.find_company_by_mp_preapproval(db, resource_id)
        plan = None
        if company is None:
            company, plan = _company_from_external_ref(
                db, str(pre.get("external_reference") or "")
            )
        meta = pre.get("metadata") or {}
        if isinstance(meta, dict):
            plan = plan or meta.get("plan")
            if company is None and meta.get("company_id"):
                company = db.get(Company, int(meta["company_id"]))
        if company is None:
            return {"ok": False, "reason": "company_not_found"}
        plan = normalize_plan_key(plan or company.plan)
        company.mp_preapproval_id = resource_id
        if status == "authorized":
            # Demo/dev "active" no cuenta: hay que activar de verdad al autorizar MP.
            if not billing.has_paid_subscription(company):
                billing.activate_paid_plan(
                    db,
                    company.id,
                    plan_key=plan,
                    provider=billing.PROVIDER_MP,
                    mp_preapproval_id=resource_id,
                    mp_payer_email=str(pre.get("payer_email") or "") or None,
                    grant_credits=True,
                )
            else:
                company.billing_status = billing.STATUS_ACTIVE
                company.billing_provider = billing.PROVIDER_MP
                company.plan = plan
                company.mp_preapproval_id = resource_id
                db.flush()
            return {"ok": True, "action": "authorized", "company_id": company.id}
        if status in ("cancelled", "canceled"):
            billing.mark_canceled(db, company.id)
            return {"ok": True, "action": "canceled", "company_id": company.id}
        if status == "paused":
            billing.mark_payment_failed(db, company.id)
            return {"ok": True, "action": "paused", "company_id": company.id}
        return {"ok": True, "action": "preapproval_ignored", "status": status}

    if topic in ("payment", "subscription_authorized_payment") and resource_id:
        pay = _fetch_payment(resource_id)
        status = str(pay.get("status") or "").lower()
        company, plan = _company_from_external_ref(
            db, str(pay.get("external_reference") or "")
        )
        meta = pay.get("metadata") or {}
        if isinstance(meta, dict) and company is None and meta.get("company_id"):
            company = db.get(Company, int(meta["company_id"]))
            plan = plan or meta.get("plan")
        pre_id = str(
            (pay.get("metadata") or {}).get("preapproval_id")
            or pay.get("point_of_interaction", {})
            .get("transaction_data", {})
            .get("subscription_id")
            or ""
        )
        if company is None and pre_id:
            company = billing.find_company_by_mp_preapproval(db, pre_id)
        if company is None:
            return {"ok": False, "reason": "company_not_found"}
        if status == "approved":
            if not billing.has_paid_subscription(company):
                billing.activate_paid_plan(
                    db,
                    company.id,
                    plan_key=normalize_plan_key(plan or company.plan),
                    provider=billing.PROVIDER_MP,
                    mp_preapproval_id=company.mp_preapproval_id,
                    grant_credits=True,
                )
            else:
                billing.renew_paid_cycle(
                    db,
                    company.id,
                    provider=billing.PROVIDER_MP,
                )
            return {"ok": True, "action": "payment_approved", "company_id": company.id}
        if status in ("rejected", "cancelled", "refunded", "charged_back"):
            billing.mark_payment_failed(db, company.id)
            return {"ok": True, "action": "payment_failed", "company_id": company.id}
        return {"ok": True, "action": "payment_ignored", "status": status}

    return {"ok": True, "action": "ignored", "topic": topic}
