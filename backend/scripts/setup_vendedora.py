"""Crea usuario SDR. Créditos solo si ya se pagaron proveedores (--credits > 0).

Uso:
  cd backend
  python scripts/setup_vendedora.py --company-id 1 --email sdr@cliente.com \\
    --password '...' --first-name Maria --last-name Lopez
"""

from __future__ import annotations

import argparse
import sys

from app.core.security import hash_password
from app.database.session import SessionLocal
from app.models.company import Company
from app.models.enums import UserRole
from app.models.user import User
from app.services.credits import allocate_to_seller
from sqlalchemy import select


def main() -> int:
    parser = argparse.ArgumentParser(description="Alta SDR + créditos para go-live")
    parser.add_argument("--company-id", type=int, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--first-name", default="SDR")
    parser.add_argument("--last-name", default="")
    parser.add_argument(
        "--credits",
        type=int,
        default=0,
        help="Solo >0 si ya se pagaron proveedores / Ops acreditó el mes",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        company = db.get(Company, args.company_id)
        if company is None:
            print(f"ERROR: empresa {args.company_id} no existe", file=sys.stderr)
            return 1

        email = args.email.strip().lower()
        existing = db.scalars(select(User).where(User.email == email)).first()
        if existing:
            user = existing
            print(f"Usuario existente id={user.id} email={user.email}")
        else:
            user = User(
                company_id=company.id,
                first_name=args.first_name.strip(),
                last_name=args.last_name.strip(),
                name=f"{args.first_name.strip()} {args.last_name.strip()}".strip(),
                email=email,
                password_hash=hash_password(args.password),
                role=UserRole.sdr.value,
                is_active=True,
            )
            db.add(user)
            db.flush()
            print(f"SDR creado id={user.id} email={user.email}")

        if args.credits > 0:
            row = allocate_to_seller(
                db,
                company.id,
                user.id,
                int(args.credits),
                actor_user_id=None,
            )
            print(
                f"Créditos asignados: {args.credits} → SDR {user.id} "
                f"(allocated={row.allocated_balance})"
            )

        db.commit()
        print(f"OK — {company.name} (id={company.id}) lista para login SDR")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
