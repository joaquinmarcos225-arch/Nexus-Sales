"""Alta Ops de cliente (sales-led).

Crea empresa + owner (+ SDR/director opcional) con wallet en 0.
Créditos solo con --grant-plan-credits después de pagar proveedores.
Siempre abre hilo en Nexus Support.

Uso:
  cd backend
  python scripts/setup_cliente.py \\
    --company-name "Acme SA" \\
    --plan growth \\
    --owner-email director@acme.com --owner-password '...' \\
    --owner-first-name Ana --owner-last-name Ruiz \\
    --sdr-email sdr@acme.com --sdr-password '...' \\
    --sdr-first-name Luis
  # Post-pago proveedores:
  #   ... --grant-plan-credits --sdr-credits 500
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.security import hash_password
from app.database.session import SessionLocal
from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credit_plans import credits_for_plan, normalize_plan_key, plan_definition
from app.services.credits import allocate_to_seller
from app.services.support import get_or_create_company_thread


def main() -> int:
    parser = argparse.ArgumentParser(description="Alta Ops cliente (créditos solo post-pago)")
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--plan", default="starter", help="starter|growth|scaler|elite")
    parser.add_argument("--employees", type=int, default=10)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--owner-password", required=True)
    parser.add_argument("--owner-first-name", default="Director")
    parser.add_argument("--owner-last-name", default="")
    parser.add_argument("--sdr-email", default="")
    parser.add_argument("--sdr-password", default="")
    parser.add_argument("--sdr-first-name", default="SDR")
    parser.add_argument("--sdr-last-name", default="")
    parser.add_argument(
        "--sdr-credits",
        type=int,
        default=0,
        help="Asignar del pool al SDR (requiere --grant-plan-credits)",
    )
    parser.add_argument(
        "--grant-plan-credits",
        action="store_true",
        help="Acreditar cupo del plan (solo si ya se pagaron OpenAI/Prospeo/Brave)",
    )
    args = parser.parse_args()

    plan_key = normalize_plan_key(args.plan)
    owner_email = args.owner_email.strip().lower()
    sdr_email = (args.sdr_email or "").strip().lower()

    if args.sdr_credits > 0 and not args.grant_plan_credits:
        print(
            "ERROR: --sdr-credits requiere --grant-plan-credits (créditos solo post-pago proveedores)",
            file=sys.stderr,
        )
        return 1

    db = SessionLocal()
    try:
        if db.scalars(select(User).where(User.email == owner_email)).first():
            print(f"ERROR: ya existe usuario {owner_email}", file=sys.stderr)
            return 1
        if sdr_email and db.scalars(select(User).where(User.email == sdr_email)).first():
            print(f"ERROR: ya existe usuario {sdr_email}", file=sys.stderr)
            return 1

        company = Company(
            name=args.company_name.strip(),
            employee_count=max(0, int(args.employees)),
            plan=plan_key,
            billing_status="active" if args.grant_plan_credits else "none",
            billing_provider="ops",
        )
        db.add(company)
        db.flush()

        credits = credits_for_plan(plan_key) if args.grant_plan_credits else 0
        cycle = current_plan_cycle_key() if args.grant_plan_credits else None
        db.add(
            CreditWallet(
                company_id=company.id,
                total_balance=credits,
                plan_cycle_key=cycle,
                plan_last_credited_at=datetime.now(UTC) if args.grant_plan_credits else None,
            )
        )
        if args.grant_plan_credits and credits > 0:
            plan_def = plan_definition(plan_key)
            record_credit_ledger(
                db,
                company_id=company.id,
                kind="plan_seed",
                amount=credits,
                note=f"Ops alta post-pago proveedores {plan_def.label}: +{credits} ({cycle})",
            )

        db.add(
            Product(
                company_id=company.id,
                name="Mi producto",
                description="Completá qué vende la empresa en Productos.",
                value_proposition="",
                target_notes="",
                is_active=True,
            )
        )

        owner = User(
            company_id=company.id,
            first_name=args.owner_first_name.strip(),
            last_name=args.owner_last_name.strip(),
            name=f"{args.owner_first_name.strip()} {args.owner_last_name.strip()}".strip(),
            email=owner_email,
            password_hash=hash_password(args.owner_password),
            role=UserRole.owner.value,
            is_active=True,
        )
        db.add(owner)
        db.flush()
        print(
            f"Empresa id={company.id} · {company.name} · plan={plan_key} · "
            f"créditos={credits}" + (" (post-pago)" if args.grant_plan_credits else " (sin pago)")
        )
        print(f"Owner id={owner.id} · {owner.email}")

        if sdr_email:
            if not args.sdr_password:
                print("ERROR: --sdr-password requerido si hay --sdr-email", file=sys.stderr)
                return 1
            sdr = User(
                company_id=company.id,
                first_name=args.sdr_first_name.strip(),
                last_name=args.sdr_last_name.strip(),
                name=f"{args.sdr_first_name.strip()} {args.sdr_last_name.strip()}".strip(),
                email=sdr_email,
                password_hash=hash_password(args.sdr_password),
                role=UserRole.sdr.value,
                is_active=True,
            )
            db.add(sdr)
            db.flush()
            print(f"SDR id={sdr.id} · {sdr.email}")
            if args.sdr_credits > 0:
                row = allocate_to_seller(
                    db,
                    company.id,
                    sdr.id,
                    int(args.sdr_credits),
                    actor_user_id=owner.id,
                )
                print(f"Créditos SDR: {args.sdr_credits} (saldo={row.allocated_balance})")

        thread = get_or_create_company_thread(db, company_id=company.id, user=owner)
        print(f"Nexus Support thread id={thread.id}")

        db.commit()
        print("OK — cliente listo (login owner/SDR). Registro público no hace falta.")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
