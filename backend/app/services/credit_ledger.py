"""Historial de movimientos de créditos (pool, asignaciones, campañas)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.credit_ledger import CreditLedgerEntry

LEDGER_KIND_LABELS = {
    "plan_renewal": "Renovación mensual del plan",
    "plan_manual": "Acreditación manual del plan",
    "plan_seed": "Acreditación demo (arranque)",
    "plan_expiry": "Créditos no usados (fin de mes)",
    "pool_top_up": "Ajuste manual del pool",
    "allocate_manager": "Asignación a manager",
    "transfer": "Transferencia entre usuarios",
    "campaign_reserve": "Reserva por campaña",
    "campaign_release": "Devolución de campaña",
    "reconcile": "Ajuste de reconciliación",
    "subscription_payment": "Pago de suscripción",
    "subscription_renewal": "Renovación de suscripción",
    "plan_upgrade": "Upgrade de plan",
    "payment_failed": "Pago fallido",
}


def record_credit_ledger(
    session: Session,
    *,
    company_id: int,
    kind: str,
    amount: int,
    note: str,
    user_id: int | None = None,
    from_user_id: int | None = None,
    actor_user_id: int | None = None,
) -> CreditLedgerEntry:
    row = CreditLedgerEntry(
        company_id=company_id,
        user_id=user_id,
        from_user_id=from_user_id,
        actor_user_id=actor_user_id,
        kind=kind,
        amount=int(amount),
        note=(note or "").strip()[:500],
    )
    session.add(row)
    session.flush()
    return row


def list_credit_ledger(
    session: Session,
    company_id: int,
    *,
    limit: int = 60,
) -> list[CreditLedgerEntry]:
    cap = max(1, min(200, int(limit)))
    return list(
        session.scalars(
            select(CreditLedgerEntry)
            .where(CreditLedgerEntry.company_id == company_id)
            .order_by(CreditLedgerEntry.id.desc())
            .limit(cap)
        ).all()
    )


def list_peer_transfer_ledger(
    session: Session,
    company_id: int,
    *,
    me_user_id: int,
    peer_user_id: int,
    limit: int = 80,
) -> list[CreditLedgerEntry]:
    """Transferencias entre dos usuarios (historial tipo chat)."""
    from sqlalchemy import and_, or_

    cap = max(1, min(200, int(limit)))
    return list(
        session.scalars(
            select(CreditLedgerEntry)
            .where(
                CreditLedgerEntry.company_id == company_id,
                CreditLedgerEntry.kind == "transfer",
                or_(
                    and_(
                        CreditLedgerEntry.from_user_id == me_user_id,
                        CreditLedgerEntry.user_id == peer_user_id,
                    ),
                    and_(
                        CreditLedgerEntry.from_user_id == peer_user_id,
                        CreditLedgerEntry.user_id == me_user_id,
                    ),
                    # Legacy rows without from_user_id: actor → subject
                    and_(
                        CreditLedgerEntry.from_user_id.is_(None),
                        CreditLedgerEntry.actor_user_id == me_user_id,
                        CreditLedgerEntry.user_id == peer_user_id,
                    ),
                    and_(
                        CreditLedgerEntry.from_user_id.is_(None),
                        CreditLedgerEntry.actor_user_id == peer_user_id,
                        CreditLedgerEntry.user_id == me_user_id,
                    ),
                ),
            )
            .order_by(CreditLedgerEntry.id.asc())
            .limit(cap)
        ).all()
    )


def current_plan_cycle_key(now: datetime | None = None) -> str:
    ref = now or datetime.now(UTC)
    return ref.strftime("%Y-%m")
