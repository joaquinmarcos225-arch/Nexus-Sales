"""Paquete comercial WhatsApp, destinos y tope de únicos 24 h.

Separado de los créditos de secuencia. Sin Stripe: Support asigna a mano.

- Paquete: unidades facturables (billing-event billable=true). Un fallido no descuenta
  salvo que Gupshup/Meta lo marquen facturable.
- Destinos: lista ISO aprobada (default LatAm hispano, sin BR).
- Tope económico opcional: suma de costos estimados (reservados + facturados).
- Únicos 24 h: tope técnico de la WABA (env, default 250) + subtope opcional por empresa.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.whatsapp_dispatch import WhatsAppDispatch
from app.services.whatsapp_destination import (
    DEFAULT_APPROVED_DESTINATIONS,
    country_from_digits,
    estimated_marketing_usd,
    normalize_destination_list,
)

DEFAULT_UNIQUE_24H = 250


class WhatsAppCommercialError(Exception):
    def __init__(self, code: str, detail: str, *, http_status: int = 429) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name, "") or "").strip()
    return int(raw) if raw.isdigit() else default


def account_unique_24h_limit() -> int:
    """Tope Meta/WABA de destinatarios únicos en 24 h (cuenta compartida v1)."""
    return max(
        1,
        _env_int("GUPSHUP_UNIQUE_24H_LIMIT", _env_int("WHATSAPP_UNIQUE_24H_LIMIT", DEFAULT_UNIQUE_24H)),
    )


def commercial_config(company: Company) -> dict[str, Any]:
    raw = company.whatsapp_commercial if isinstance(company.whatsapp_commercial, dict) else {}
    packet = raw.get("packet_units")
    unique = raw.get("unique_24h_limit")
    spend = raw.get("spend_cap_usd")
    dests = raw.get("approved_destinations")
    try:
        packet_units = int(packet or 0)
    except (TypeError, ValueError):
        packet_units = 0
    try:
        unique_limit = int(unique) if unique not in (None, "") else None
    except (TypeError, ValueError):
        unique_limit = None
    try:
        spend_cap = float(spend) if spend not in (None, "") else None
    except (TypeError, ValueError):
        spend_cap = None
    return {
        "packet_units": max(0, packet_units),
        "unique_24h_limit": unique_limit if unique_limit and unique_limit > 0 else None,
        "approved_destinations": normalize_destination_list(
            dests if isinstance(dests, list) else None
        ),
        "spend_cap_usd": spend_cap if spend_cap and spend_cap > 0 else None,
        "auto_paused": bool(raw.get("auto_paused")),
        "paused_reason": (str(raw.get("paused_reason") or "").strip() or None),
    }


def save_commercial(company: Company, **updates: Any) -> dict[str, Any]:
    cfg = commercial_config(company)
    if "packet_units" in updates and updates["packet_units"] is not None:
        cfg["packet_units"] = max(0, int(updates["packet_units"]))
    if "unique_24h_limit" in updates:
        val = updates["unique_24h_limit"]
        cfg["unique_24h_limit"] = int(val) if val not in (None, "") else None
        if cfg["unique_24h_limit"] is not None and cfg["unique_24h_limit"] <= 0:
            cfg["unique_24h_limit"] = None
    if "approved_destinations" in updates:
        cfg["approved_destinations"] = normalize_destination_list(updates["approved_destinations"])
    if "spend_cap_usd" in updates:
        val = updates["spend_cap_usd"]
        cfg["spend_cap_usd"] = float(val) if val not in (None, "") else None
        if cfg["spend_cap_usd"] is not None and cfg["spend_cap_usd"] <= 0:
            cfg["spend_cap_usd"] = None
    if "auto_paused" in updates:
        cfg["auto_paused"] = bool(updates["auto_paused"])
        if not cfg["auto_paused"]:
            cfg["paused_reason"] = None
    if "paused_reason" in updates:
        cfg["paused_reason"] = (str(updates["paused_reason"] or "").strip() or None)
    company.whatsapp_commercial = cfg
    return cfg


def pause_whatsapp_auto(company: Company, reason: str) -> None:
    save_commercial(company, auto_paused=True, paused_reason=reason)


def _now(now: datetime | None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(tzinfo=UTC) if now.tzinfo is None else now


def _window_start(now: datetime | None = None) -> datetime:
    return _now(now) - timedelta(hours=24)


def unique_destinations_24h(
    db: Session,
    *,
    company_id: int | None = None,
    now: datetime | None = None,
) -> int:
    """Únicos con envío aceptado en las últimas 24 h. Un fallido al submit no cuenta."""
    stmt = (
        select(func.count(func.distinct(WhatsAppDispatch.destination_digits)))
        .where(
            WhatsAppDispatch.created_at >= _window_start(now),
            WhatsAppDispatch.dry_run.is_(False),
            WhatsAppDispatch.submit_status == "submitted",
        )
    )
    if company_id is not None:
        stmt = stmt.where(WhatsAppDispatch.company_id == int(company_id))
    return int(db.scalar(stmt) or 0)


def packet_committed(db: Session, company_id: int) -> int:
    """Unidades reservadas (submit vivo) + facturadas. Fallidos liberan la reserva."""
    billed = db.scalar(
        select(func.coalesce(func.sum(WhatsAppDispatch.billed_units), 0)).where(
            WhatsAppDispatch.company_id == int(company_id),
            WhatsAppDispatch.dry_run.is_(False),
        )
    )
    reserved = db.scalar(
        select(func.coalesce(func.sum(WhatsAppDispatch.reserved_units), 0)).where(
            WhatsAppDispatch.company_id == int(company_id),
            WhatsAppDispatch.dry_run.is_(False),
            WhatsAppDispatch.billed_units == 0,
        )
    )
    return int(billed or 0) + int(reserved or 0)


def estimated_spend_usd(db: Session, company_id: int) -> float:
    rows = db.scalars(
        select(WhatsAppDispatch).where(
            WhatsAppDispatch.company_id == int(company_id),
            WhatsAppDispatch.dry_run.is_(False),
            or_(
                WhatsAppDispatch.billed_units > 0,
                WhatsAppDispatch.reserved_units > 0,
            ),
        )
    ).all()
    return round(sum(float(r.estimated_cost_usd or 0) for r in rows), 4)


def destination_gate(company: Company, digits: str) -> tuple[str, float]:
    cfg = commercial_config(company)
    country = country_from_digits(digits)
    approved = set(cfg["approved_destinations"] or DEFAULT_APPROVED_DESTINATIONS)
    if not country or country not in approved:
        iso = country or "??"
        raise WhatsAppCommercialError(
            "dest_not_approved",
            f"Destino {iso} no está en la lista de países aprobados para WhatsApp de esta empresa.",
            http_status=409,
        )
    return country, estimated_marketing_usd(country)


def assert_can_send_whatsapp_meow(
    db: Session,
    company: Company,
    *,
    digits: str,
) -> dict[str, Any]:
    """Validación mínima para WhatsApp nativo (Meow): no usa paquete Gupshup/Meta."""
    _ = db
    cfg = commercial_config(company)
    if cfg["auto_paused"]:
        raise WhatsAppCommercialError(
            "auto_paused",
            cfg["paused_reason"]
            or "WhatsApp automático pausado. Revisá la configuración en Support.",
        )
    country, _cost = destination_gate(company, digits)
    return {
        "country": country,
        "estimated_cost_usd": 0.0,
        "packet_used": 0,
        "packet_units": 0,
        "account_unique_used": 0,
        "account_unique_limit": account_unique_24h_limit(),
    }


def assert_can_send_whatsapp(
    db: Session,
    company: Company,
    *,
    digits: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Corta el auto-envío si falta paquete, se fue de destinos, únicos o gasto."""
    cfg = commercial_config(company)
    if cfg["auto_paused"]:
        raise WhatsAppCommercialError(
            "auto_paused",
            cfg["paused_reason"]
            or "WhatsApp automático pausado. Revisá el paquete o el tope de gasto en Support.",
        )
    if cfg["packet_units"] <= 0:
        raise WhatsAppCommercialError(
            "packet_unassigned",
            "Esta empresa no tiene paquete de WhatsApp asignado. Cargalo a mano en Support.",
            http_status=409,
        )
    country, cost = destination_gate(company, digits)
    used = packet_committed(db, company.id)
    if used >= cfg["packet_units"]:
        pause_whatsapp_auto(company, "Paquete de WhatsApp agotado.")
        raise WhatsAppCommercialError(
            "packet_exhausted",
            f"Paquete de WhatsApp agotado ({used}/{cfg['packet_units']} unidades).",
        )
    account_used = unique_destinations_24h(db, now=now)
    account_limit = account_unique_24h_limit()
    already = db.scalar(
        select(WhatsAppDispatch.id).where(
            WhatsAppDispatch.destination_digits == digits,
            WhatsAppDispatch.created_at >= _window_start(now),
            WhatsAppDispatch.dry_run.is_(False),
            WhatsAppDispatch.submit_status == "submitted",
        )
    )
    if not already and account_used >= account_limit:
        raise WhatsAppCommercialError(
            "unique_24h_account",
            f"Tope de destinatarios únicos en 24 h de la cuenta ({account_used}/{account_limit}).",
        )
    company_limit = cfg["unique_24h_limit"]
    if company_limit and not already:
        company_used = unique_destinations_24h(db, company_id=company.id, now=now)
        if company_used >= company_limit:
            raise WhatsAppCommercialError(
                "unique_24h_company",
                f"Tope de destinatarios únicos en 24 h de la empresa ({company_used}/{company_limit}).",
            )
    spend_cap = cfg["spend_cap_usd"]
    if spend_cap is not None:
        spent = estimated_spend_usd(db, company.id)
        if spent + cost > spend_cap + 1e-9:
            pause_whatsapp_auto(
                company,
                f"Tope económico de WhatsApp alcanzado (≈USD {spent:.2f} / {spend_cap:.2f}).",
            )
            raise WhatsAppCommercialError(
                "spend_cap",
                f"Tope económico de WhatsApp alcanzado (≈USD {spent:.2f} + {cost:.3f} > {spend_cap:.2f}).",
            )
    return {
        "country": country,
        "estimated_cost_usd": cost,
        "packet_used": used,
        "packet_units": cfg["packet_units"],
        "account_unique_used": account_used,
        "account_unique_limit": account_limit,
    }


def maybe_pause_after_billing(db: Session, company: Company) -> None:
    cfg = commercial_config(company)
    if cfg["auto_paused"] or cfg["packet_units"] <= 0:
        return
    if packet_committed(db, company.id) >= cfg["packet_units"]:
        pause_whatsapp_auto(company, "Paquete de WhatsApp agotado.")
        return
    if cfg["spend_cap_usd"] is not None:
        spent = estimated_spend_usd(db, company.id)
        if spent >= cfg["spend_cap_usd"]:
            pause_whatsapp_auto(
                company,
                f"Tope económico de WhatsApp alcanzado (≈USD {spent:.2f} / {cfg['spend_cap_usd']:.2f}).",
            )


def snapshot(db: Session, company: Company, *, now: datetime | None = None) -> dict[str, Any]:
    cfg = commercial_config(company)
    used = packet_committed(db, company.id)
    spent = estimated_spend_usd(db, company.id)
    cap = cfg.get("spend_cap_usd")
    spend_alert: str | None = None
    if cap and float(cap) > 0:
        ratio = float(spent) / float(cap)
        if ratio >= 1.0:
            spend_alert = "100"
        elif ratio >= 0.9:
            spend_alert = "90"
        elif ratio >= 0.75:
            spend_alert = "75"
    return {
        **cfg,
        "packet_used": used,
        "packet_remaining": max(0, cfg["packet_units"] - used),
        "estimated_spend_usd": spent,
        "spend_alert_level": spend_alert,
        "account_unique_24h_used": unique_destinations_24h(db, now=now),
        "account_unique_24h_limit": account_unique_24h_limit(),
        "company_unique_24h_used": unique_destinations_24h(db, company_id=company.id, now=now),
        "default_approved_destinations": list(DEFAULT_APPROVED_DESTINATIONS),
    }
