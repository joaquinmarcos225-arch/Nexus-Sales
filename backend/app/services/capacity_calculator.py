"""Calculadora de capacidad: saldo proveedores → secuencias Nexus (ops)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.models.ops_provider_balance import OpsProviderBalance
from app.models.seller_allocation import SellerCreditAllocation
from app.services.credit_plans import custom_tool_costs_for_credits
from app.services.lead_sourcing.cogs_runtime_metrics import snapshot as cogs_snapshot
from app.services.lead_sourcing.prospeo_api_health import fetch_prospeo_account_health

COGS_PER_SEQUENCE_USD = 0.30
PROSPEO_CREDITS_PER_SEQUENCE = 11
PROSPEO_USD_PER_CREDIT = 0.0245
BRAVE_QUERIES_PER_SEQUENCE = 6
BRAVE_USD_PER_QUERY = 0.005
BRAVE_USD_PER_SEQUENCE = round(BRAVE_QUERIES_PER_SEQUENCE * BRAVE_USD_PER_QUERY, 4)
OPENAI_USD_PER_SEQUENCE = round(COGS_PER_SEQUENCE_USD * 0.02, 4)


def sequence_economics_dict() -> dict[str, Any]:
    return {
        "cogs_per_sequence_usd": COGS_PER_SEQUENCE_USD,
        "prospeo_credits_per_sequence": PROSPEO_CREDITS_PER_SEQUENCE,
        "prospeo_usd_per_credit": PROSPEO_USD_PER_CREDIT,
        "brave_queries_per_sequence": BRAVE_QUERIES_PER_SEQUENCE,
        "brave_usd_per_sequence": BRAVE_USD_PER_SEQUENCE,
        "openai_usd_per_sequence": OPENAI_USD_PER_SEQUENCE,
        "tool_share_pct": {"prospeo": 90, "brave": 8, "openai": 2},
    }


def _sequences_from_prospeo_credits(credits: int | None) -> int | None:
    if credits is None:
        return None
    return max(0, int(credits) // PROSPEO_CREDITS_PER_SEQUENCE)


def _sequences_from_brave_usd(usd: float | None) -> int | None:
    if usd is None:
        return None
    if BRAVE_USD_PER_SEQUENCE <= 0:
        return None
    return max(0, int(float(usd) // BRAVE_USD_PER_SEQUENCE))


def _sequences_from_openai_usd(usd: float | None) -> int | None:
    if usd is None:
        return None
    if OPENAI_USD_PER_SEQUENCE <= 0:
        return None
    return max(0, int(float(usd) // OPENAI_USD_PER_SEQUENCE))


def _env_balance_usd(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _get_stored_balance(db: Session, provider: str) -> OpsProviderBalance | None:
    return db.get(OpsProviderBalance, provider)


def _upsert_stored_balance(
    db: Session,
    *,
    provider: str,
    balance_usd: float | None = None,
    balance_credits: int | None = None,
    source: str,
    notes: str | None = None,
    updated_by_user_id: int | None = None,
) -> OpsProviderBalance:
    row = db.get(OpsProviderBalance, provider)
    if row is None:
        row = OpsProviderBalance(provider=provider, source=source)
        db.add(row)
    if balance_usd is not None:
        row.balance_usd = round(float(balance_usd), 4)
    if balance_credits is not None:
        row.balance_credits = int(balance_credits)
    row.source = source
    if notes is not None:
        row.notes = notes[:512] if notes else None
    if updated_by_user_id is not None:
        row.updated_by_user_id = updated_by_user_id
    row.updated_at = datetime.now(UTC)
    db.flush()
    return row


def _try_fetch_openai_balance_usd() -> tuple[float | None, str, str | None]:
    env_usd = _env_balance_usd("OPENAI_OPS_BALANCE_USD")
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        if env_usd is not None:
            return env_usd, "env", "OPENAI_OPS_BALANCE_USD"
        return None, "unknown", "OPENAI_API_KEY no configurada"

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(
                "https://api.openai.com/v1/dashboard/billing/credit_grants",
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code == 200:
            body = resp.json()
            grants = body.get("grants") if isinstance(body, dict) else None
            if isinstance(grants, dict):
                total = grants.get("total_available") or grants.get("total_granted")
                if total is not None:
                    val = float(total)
                    usd = val / 100.0 if val > 1000 else val
                    return round(max(0.0, usd), 4), "api", None
    except Exception:
        pass

    if env_usd is not None:
        return env_usd, "env", "OPENAI_OPS_BALANCE_USD"
    return None, "unknown", "Sin lectura API; cargá saldo manual o OPENAI_OPS_BALANCE_USD"


def _resolve_openai_balance(db: Session, *, refresh: bool) -> dict[str, Any]:
    stored = _get_stored_balance(db, "openai")
    if refresh:
        usd, source, hint = _try_fetch_openai_balance_usd()
        if usd is not None:
            _upsert_stored_balance(db, provider="openai", balance_usd=usd, source=source, notes=hint)
            stored = _get_stored_balance(db, "openai")
    elif stored is None or stored.balance_usd is None:
        usd, source, hint = _try_fetch_openai_balance_usd()
        if usd is not None:
            _upsert_stored_balance(db, provider="openai", balance_usd=usd, source=source, notes=hint)
            stored = _get_stored_balance(db, "openai")

    balance_usd = float(stored.balance_usd) if stored and stored.balance_usd is not None else None
    source = stored.source if stored else "unknown"
    return {
        "key": "openai",
        "label": "OpenAI",
        "balance_usd": balance_usd,
        "balance_credits": None,
        "sequences_available": _sequences_from_openai_usd(balance_usd),
        "source": source,
        "updated_at": stored.updated_at.isoformat() if stored and stored.updated_at else None,
        "notes": stored.notes if stored else None,
        "bottleneck": False,
    }


def _resolve_brave_balance(db: Session, *, refresh: bool) -> dict[str, Any]:
    stored = _get_stored_balance(db, "brave")
    env_usd = _env_balance_usd("BRAVE_OPS_BALANCE_USD")
    if refresh and env_usd is not None:
        _upsert_stored_balance(db, provider="brave", balance_usd=env_usd, source="env")
        stored = _get_stored_balance(db, "brave")
    elif stored is None and env_usd is not None:
        _upsert_stored_balance(db, provider="brave", balance_usd=env_usd, source="env")
        stored = _get_stored_balance(db, "brave")

    balance_usd = float(stored.balance_usd) if stored and stored.balance_usd is not None else None
    source = stored.source if stored else ("env" if env_usd is not None else "unknown")
    if balance_usd is None and env_usd is not None:
        balance_usd = env_usd
        source = "env"
    return {
        "key": "brave",
        "label": "Brave Search",
        "balance_usd": balance_usd,
        "balance_credits": None,
        "sequences_available": _sequences_from_brave_usd(balance_usd),
        "source": source,
        "updated_at": stored.updated_at.isoformat() if stored and stored.updated_at else None,
        "notes": (stored.notes if stored else None) or "Brave no expone saldo por API; actualizá manual.",
        "bottleneck": False,
    }


def _resolve_prospeo_balance(db: Session, *, refresh: bool) -> dict[str, Any]:
    stored = _get_stored_balance(db, "prospeo")
    health = None
    if refresh or stored is None:
        health = fetch_prospeo_account_health()

    if health is not None:
        credits = health.remaining_credits
        source = "api"
        detail = health.detail or health.current_plan
        _upsert_stored_balance(
            db,
            provider="prospeo",
            balance_credits=credits,
            balance_usd=round(float(credits or 0) * PROSPEO_USD_PER_CREDIT, 2) if credits is not None else None,
            source="api",
            notes=detail,
        )
        stored = _get_stored_balance(db, "prospeo")
    else:
        credits = int(stored.balance_credits) if stored and stored.balance_credits is not None else None
        source = stored.source if stored else "unknown"
        detail = stored.notes if stored else "Sin lectura reciente"

    balance_credits = int(stored.balance_credits) if stored and stored.balance_credits is not None else credits
    balance_usd = (
        round(float(balance_credits) * PROSPEO_USD_PER_CREDIT, 2) if balance_credits is not None else None
    )
    return {
        "key": "prospeo",
        "label": "Prospeo",
        "balance_usd": balance_usd,
        "balance_credits": balance_credits,
        "sequences_available": _sequences_from_prospeo_credits(balance_credits),
        "source": source,
        "updated_at": stored.updated_at.isoformat() if stored and stored.updated_at else None,
        "notes": detail,
        "bottleneck": False,
        "search_blocked": bool(health.search_blocked) if health else None,
    }


def compute_client_liability(db: Session) -> dict[str, Any]:
    total_pool = int(db.scalar(select(func.coalesce(func.sum(CreditWallet.total_balance), 0))) or 0)
    total_used = int(
        db.scalar(select(func.coalesce(func.sum(SellerCreditAllocation.used_balance), 0))) or 0
    )
    committed = max(0, total_pool - total_used)

    rows = db.execute(
        select(
            CreditWallet.company_id,
            Company.name,
            CreditWallet.total_balance,
            func.coalesce(func.sum(SellerCreditAllocation.used_balance), 0).label("used"),
        )
        .join(Company, Company.id == CreditWallet.company_id)
        .outerjoin(
            SellerCreditAllocation,
            SellerCreditAllocation.company_id == CreditWallet.company_id,
        )
        .group_by(CreditWallet.company_id, Company.name, CreditWallet.total_balance)
    ).all()

    companies: list[dict[str, Any]] = []
    for company_id, name, pool, used in rows:
        pool_i = int(pool or 0)
        used_i = int(used or 0)
        avail = max(0, pool_i - used_i)
        if avail <= 0:
            continue
        companies.append(
            {
                "company_id": int(company_id),
                "company_name": name,
                "available_credits": avail,
                "pool_total": pool_i,
                "used_in_allocations": used_i,
            }
        )
    companies.sort(key=lambda r: -int(r["available_credits"]))
    return {
        "total_credits_committed": committed,
        "total_pool": total_pool,
        "total_used_in_allocations": total_used,
        "companies_with_balance": len(companies),
        "top_companies": companies[:8],
    }


def _min_sequences(providers: list[dict[str, Any]]) -> tuple[int | None, str | None]:
    candidates = [
        (p["key"], p.get("sequences_available"))
        for p in providers
        if p.get("sequences_available") is not None
    ]
    if not candidates:
        return None, None
    key, val = min(candidates, key=lambda x: int(x[1]))
    return int(val), str(key)


def build_reverse_plan(
    *,
    proposed_grant: int,
    providers: list[dict[str, Any]],
    net_headroom: int | None,
) -> dict[str, Any]:
    n = max(0, int(proposed_grant))
    topup = custom_tool_costs_for_credits(n)
    capacity = _min_sequences(providers)[0]
    shortfall = None
    feasible = True
    if capacity is not None and n > capacity:
        feasible = False
        shortfall = n - capacity
    if net_headroom is not None and n > net_headroom:
        feasible = False
        shortfall = max(shortfall or 0, n - net_headroom)
    return {
        "proposed_grant": n,
        "topup_usd": {
            "openai": topup["openai_usd"],
            "prospeo": topup["prospeo_usd"],
            "brave": topup["brave_usd"],
            "total": round(sum(topup.values()), 2),
        },
        "feasible_with_current_balances": feasible,
        "shortfall_sequences": shortfall,
        "note": (
            f"Top-up estimado para {n} secuencias (COGS USD {COGS_PER_SEQUENCE_USD}/seq, "
            "reparto 90/8/2 Prospeo/Brave/OpenAI)."
        ),
    }


def build_capacity_report(
    db: Session,
    *,
    refresh: bool = False,
    proposed_grant: int | None = None,
) -> dict[str, Any]:
    providers = [
        _resolve_prospeo_balance(db, refresh=refresh),
        _resolve_openai_balance(db, refresh=refresh),
        _resolve_brave_balance(db, refresh=refresh),
    ]

    capacity, bottleneck_key = _min_sequences(providers)
    for p in providers:
        p["bottleneck"] = p["key"] == bottleneck_key if bottleneck_key else False

    liability = compute_client_liability(db)
    committed = int(liability["total_credits_committed"])
    net_headroom = (capacity - committed) if capacity is not None else None

    runtime = cogs_snapshot()
    reverse = (
        build_reverse_plan(
            proposed_grant=proposed_grant,
            providers=providers,
            net_headroom=net_headroom,
        )
        if proposed_grant is not None and proposed_grant > 0
        else None
    )

    warnings: list[str] = []
    if capacity is not None and capacity < 50:
        warnings.append(f"Capacidad baja: ~{capacity} secuencias por cuello de botella ({bottleneck_key}).")
    if net_headroom is not None and net_headroom < 0:
        warnings.append(
            f"Comprometiste {committed} créditos a clientes pero la capacidad es ~{capacity}. "
            f"Déficit ~{abs(net_headroom)} secuencias."
        )
    unknown = [p["label"] for p in providers if p.get("sequences_available") is None]
    if unknown:
        warnings.append(f"Sin saldo conocido: {', '.join(unknown)}. Completá manualmente.")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "economics": sequence_economics_dict(),
        "providers": providers,
        "bottleneck": {
            "provider": bottleneck_key,
            "sequences_available": capacity,
        },
        "client_liability": liability,
        "gross_capacity_sequences": capacity,
        "net_headroom_sequences": net_headroom,
        "runtime_month_estimate": {
            "imports": runtime.get("imports"),
            "est_cogs_per_import_usd": runtime.get("est_cogs_per_import_usd"),
            "est_total_usd": runtime.get("est_total_usd"),
        },
        "reverse_plan": reverse,
        "warnings": warnings,
    }


def patch_provider_balance_manual(
    db: Session,
    *,
    provider: str,
    balance_usd: float,
    notes: str | None,
    updated_by_user_id: int,
) -> OpsProviderBalance:
    key = (provider or "").strip().lower()
    if key not in ("openai", "brave"):
        raise ValueError("Solo openai y brave admiten saldo manual en USD")
    if float(balance_usd) < 0:
        raise ValueError("balance_usd inválido")
    return _upsert_stored_balance(
        db,
        provider=key,
        balance_usd=float(balance_usd),
        source="manual",
        notes=notes,
        updated_by_user_id=updated_by_user_id,
    )
