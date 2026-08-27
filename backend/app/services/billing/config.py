"""Billing híbrido: Stripe + Mercado Pago · planes de créditos fijos."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from app.services.credit_plans import CONTACT_PLANS, normalize_plan_key


@dataclass(frozen=True)
class PlanPrice:
    plan_key: str
    # Stripe: monto en centavos USD (o la moneda de STRIPE_CURRENCY)
    amount_cents: int
    # Mercado Pago: monto en la moneda de MP_CURRENCY (ARS por defecto)
    mp_amount: float
    stripe_price_id: str | None = None


DEFAULT_USD_CENTS = {
    "starter": 30_000,  # $300 USD
    "growth": 50_000,  # $500 USD
    "scaler": 70_000,  # $700 USD
    "elite": 90_000,  # $900 USD
}

DEFAULT_MP_ARS = {
    "starter": 300_000.0,
    "growth": 500_000.0,
    "scaler": 700_000.0,
    "elite": 900_000.0,
}


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def billing_dev_mode() -> bool:
    """
    Demo sin cobro real.
    - BILLING_DEV_MODE=1 → forzar on
    - BILLING_DEV_MODE=0 → forzar off
    - Sin pasarela real → on
    """
    raw = (os.getenv("BILLING_DEV_MODE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return not stripe_configured() and not mp_configured() and not dlocal_configured()


def stripe_secret_key() -> str:
    return (os.getenv("STRIPE_SECRET_KEY") or "").strip()


def stripe_webhook_secret() -> str:
    return (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()


def stripe_configured() -> bool:
    return bool(stripe_secret_key())


def stripe_currency() -> str:
    return ((os.getenv("STRIPE_CURRENCY") or "usd").strip().lower()) or "usd"


def mp_access_token() -> str:
    return (os.getenv("MERCADOPAGO_ACCESS_TOKEN") or "").strip()


def mp_webhook_secret() -> str:
    return (os.getenv("MERCADOPAGO_WEBHOOK_SECRET") or "").strip()


def mp_configured() -> bool:
    return bool(mp_access_token())


def mp_currency() -> str:
    return ((os.getenv("MP_CURRENCY") or "ARS").strip().upper()) or "ARS"


def dlocal_x_login() -> str:
    return (os.getenv("DLOCAL_X_LOGIN") or "").strip()


def dlocal_x_trans_key() -> str:
    return (os.getenv("DLOCAL_X_TRANS_KEY") or "").strip()


def dlocal_secret_key() -> str:
    return (os.getenv("DLOCAL_SECRET_KEY") or "").strip()


def dlocal_configured() -> bool:
    return bool(dlocal_x_login() and dlocal_x_trans_key() and dlocal_secret_key())


def dlocal_api_base() -> str:
    raw = (os.getenv("DLOCAL_API_BASE") or "").strip().rstrip("/")
    if raw:
        return raw
    # sandbox por defecto si no se fuerza prod
    env = (os.getenv("DLOCAL_ENV") or "sandbox").strip().lower()
    if env in ("prod", "production", "live"):
        return "https://api.dlocal.com"
    return "https://sandbox.dlocal.com"


def frontend_url() -> str:
    return (os.getenv("NEXUS_FRONTEND_URL") or "http://127.0.0.1:5173").strip().rstrip("/")


def backend_public_url() -> str:
    return (os.getenv("NEXUS_BACKEND_PUBLIC_URL") or "http://127.0.0.1:8002").strip().rstrip("/")


def plan_price(plan_key: str) -> PlanPrice:
    key = normalize_plan_key(plan_key)
    if key == "custom":
        return PlanPrice(plan_key=key, amount_cents=0, mp_amount=0.0, stripe_price_id=None)
    price_id = (os.getenv(f"STRIPE_PRICE_{key.upper()}") or "").strip() or None
    cents = _env_int(f"BILLING_PRICE_{key.upper()}_CENTS", DEFAULT_USD_CENTS[key])
    mp_amt = _env_float(f"BILLING_MP_PRICE_{key.upper()}", DEFAULT_MP_ARS[key])
    # Override JSON opcional: {"starter":{"cents":9900,"mp":99000},"…"}
    raw_json = (os.getenv("BILLING_PLAN_PRICES_JSON") or "").strip()
    if raw_json:
        try:
            data = json.loads(raw_json)
            row = data.get(key) if isinstance(data, dict) else None
            if isinstance(row, dict):
                if "cents" in row:
                    cents = int(row["cents"])
                if "mp" in row:
                    mp_amt = float(row["mp"])
                if row.get("stripe_price_id"):
                    price_id = str(row["stripe_price_id"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return PlanPrice(
        plan_key=key,
        amount_cents=cents,
        mp_amount=mp_amt,
        stripe_price_id=price_id,
    )


def list_billable_plans() -> list[dict]:
    out = []
    for key, plan in CONTACT_PLANS.items():
        if key == "custom":
            out.append(
                {
                    "key": plan.key,
                    "label": plan.label,
                    "monthly_contact_credits": plan.monthly_contact_credits,
                    "description": plan.description,
                    "amount_cents": 0,
                    "currency": stripe_currency(),
                    "mp_amount": 0.0,
                    "mp_currency": mp_currency(),
                    "price_per_credit_usd": 0.03,
                }
            )
            continue
        price = plan_price(key)
        out.append(
            {
                "key": plan.key,
                "label": plan.label,
                "monthly_contact_credits": plan.monthly_contact_credits,
                "description": plan.description,
                "amount_cents": price.amount_cents,
                "currency": stripe_currency(),
                "mp_amount": price.mp_amount,
                "mp_currency": mp_currency(),
            }
        )
    return out


def providers_status() -> dict[str, bool]:
    return {
        "dlocal": dlocal_configured(),
        "stripe": stripe_configured(),
        "mercadopago": mp_configured(),
        "dev_mode": billing_dev_mode(),
    }
