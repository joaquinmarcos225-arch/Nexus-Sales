"""Alta de workspace (empresa + directora) sin depender del seed demo."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.company import Company
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credit_plans import credits_for_plan, normalize_plan_key, plan_definition


class OnboardingError(Exception):
    pass


def workspace_signup_allowed() -> bool:
    return (os.getenv("NEXUS_ALLOW_WORKSPACE_SIGNUP") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def register_workspace(
    db: Session,
    *,
    company_name: str,
    employee_count: int,
    plan: str,
    first_name: str,
    last_name: str,
    email: str,
    password: str,
) -> tuple[Company, User]:
    if not workspace_signup_allowed():
        raise OnboardingError(
            "El registro de empresas está deshabilitado. Contactá a soporte Nexus."
        )

    name = company_name.strip()
    if not name:
        raise OnboardingError("Nombre de empresa requerido")

    email_norm = email.strip().lower()
    existing_user = db.scalars(select(User).where(User.email == email_norm)).first()
    if existing_user:
        raise OnboardingError("Ya existe un usuario con ese email")

    plan_key = normalize_plan_key(plan)
    company = Company(name=name, employee_count=max(0, int(employee_count)), plan=plan_key)
    db.add(company)
    db.flush()

    initial_credits = credits_for_plan(plan_key)
    cycle = current_plan_cycle_key()
    wallet = CreditWallet(
        company_id=company.id,
        total_balance=initial_credits,
        plan_cycle_key=cycle,
        plan_last_credited_at=datetime.now(UTC),
    )
    db.add(wallet)
    plan_def = plan_definition(plan_key)
    record_credit_ledger(
        db,
        company_id=company.id,
        kind="plan_seed",
        amount=initial_credits,
        note=f"Alta {plan_def.label}: +{initial_credits} créditos al pool ({cycle})",
    )

    product = Product(
        company_id=company.id,
        name="Mi producto",
        description="Describí qué vende tu empresa en Productos.",
        value_proposition="Completá la propuesta de valor en Configuración → Productos.",
        target_notes="",
        is_active=True,
    )
    db.add(product)

    user = User(
        company_id=company.id,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        name=f"{first_name.strip()} {last_name.strip()}".strip(),
        email=email_norm,
        password_hash=hash_password(password),
        role=UserRole.owner.value,
        is_active=True,
    )
    db.add(user)
    db.flush()

    from app.services.manual_sequence_kickoff import ensure_individual_container_for_company

    ensure_individual_container_for_company(db, company_id=company.id)

    return company, user
