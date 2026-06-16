"""Datos demo idempotentes para desarrollo local."""

import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Company, User
from app.models.credit_wallet import CreditWallet
from app.models.enums import UserRole
from app.models.product import Product
from app.models.seller_allocation import SellerCreditAllocation
from app.models.team import Team

_logger = logging.getLogger("nexus.seed")

DEMO_COMPANY_NAME = "CostGuard Demo Client"
DEMO_VENTAS_TEAM_NAME = "Ventas"
COMPA_COMPANY_NAME = "Compa"
DEFAULT_DEMO_PASSWORD = "demo123"

DEMO_TEST_USERS: tuple[tuple[str, str, str, UserRole], ...] = (
    ("SDR", "Test", "sdr@test.com", UserRole.sdr),
    ("Manager", "Test", "manager@test.com", UserRole.manager),
    ("Director", "Test", "director@test.com", UserRole.gerente),
)


def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return "Usuario", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def migrate_legacy_roles(db: Session) -> None:
    db.execute(text("UPDATE users SET role = 'sdr' WHERE role = 'seller'"))
    db.execute(text("UPDATE users SET role = 'gerente' WHERE role = 'admin'"))


def _ensure_user_fields(db: Session) -> None:
    """Rellena first_name / password en filas legacy."""
    users = db.scalars(select(User)).all()
    for u in users:
        if not (u.first_name or "").strip():
            first, last = _split_name(u.name)
            u.first_name = first
            u.last_name = last
        if not u.password_hash:
            u.password_hash = hash_password(DEFAULT_DEMO_PASSWORD)
        if u.name != f"{u.first_name} {u.last_name}".strip():
            u.sync_display_name()
        if not hasattr(u, "is_active") or u.is_active is None:
            u.is_active = True


def _get_or_create_demo_company(db: Session) -> Company:
    company = db.scalars(select(Company).where(Company.name == DEMO_COMPANY_NAME)).first()
    if company is not None:
        return company
    company = Company(name=DEMO_COMPANY_NAME, employee_count=72, plan="starter")
    db.add(company)
    db.flush()
    db.add(CreditWallet(company_id=company.id, total_balance=500))
    _logger.info("[seed] empresa demo creada: %s (id=%s)", DEMO_COMPANY_NAME, company.id)
    return company


def seed_demo_test_users(db: Session) -> None:
    """Usuarios @test.com para probar login y roles — upsert en cada arranque."""
    company = _get_or_create_demo_company(db)
    password_hash = hash_password(DEFAULT_DEMO_PASSWORD)

    for first_name, last_name, email, role in DEMO_TEST_USERS:
        email_norm = email.strip().lower()
        user = db.scalars(
            select(User).where(User.company_id == company.id, User.email == email_norm)
        ).first()
        if user is None:
            user = db.scalars(select(User).where(User.email == email_norm)).first()

        display_name = f"{first_name} {last_name}".strip()
        if user is None:
            db.add(
                User(
                    company_id=company.id,
                    first_name=first_name,
                    last_name=last_name,
                    name=display_name,
                    email=email_norm,
                    password_hash=password_hash,
                    role=role.value,
                    is_active=True,
                )
            )
            _logger.info(
                "[seed] usuario demo CREADO: %s | rol=%s | empresa=%s (id=%s)",
                email_norm,
                role.value,
                DEMO_COMPANY_NAME,
                company.id,
            )
            continue

        user.company_id = company.id
        user.first_name = first_name
        user.last_name = last_name
        user.name = display_name
        user.email = email_norm
        user.password_hash = password_hash
        user.role = role.value
        user.is_active = True
        _logger.info(
            "[seed] usuario demo ACTUALIZADO: %s | rol=%s | empresa=%s (id=%s)",
            email_norm,
            role.value,
            DEMO_COMPANY_NAME,
            company.id,
        )


def seed_demo_ventas_team(db: Session) -> None:
    """Equipo demo Ventas y asignación de usuarios @test.com."""
    company = _get_or_create_demo_company(db)
    team = db.scalars(
        select(Team).where(Team.company_id == company.id, Team.name == DEMO_VENTAS_TEAM_NAME)
    ).first()
    if team is None:
        team = Team(
            company_id=company.id,
            name=DEMO_VENTAS_TEAM_NAME,
            description="Equipo comercial demo",
        )
        db.add(team)
        db.flush()
        _logger.info(
            "[seed] equipo demo CREADO: %s | empresa=%s (id=%s) team_id=%s",
            DEMO_VENTAS_TEAM_NAME,
            DEMO_COMPANY_NAME,
            company.id,
            team.id,
        )
    else:
        if not team.description:
            team.description = "Equipo comercial demo"
        _logger.info(
            "[seed] equipo demo ACTUALIZADO: %s | empresa=%s (id=%s) team_id=%s",
            DEMO_VENTAS_TEAM_NAME,
            DEMO_COMPANY_NAME,
            company.id,
            team.id,
        )

    for _first, _last, email, _role in DEMO_TEST_USERS:
        email_norm = email.strip().lower()
        user = db.scalars(
            select(User).where(User.company_id == company.id, User.email == email_norm)
        ).first()
        if user is None:
            user = db.scalars(select(User).where(User.email == email_norm)).first()
        if user is None:
            continue
        user.company_id = company.id
        user.team_id = team.id
        _logger.info(
            "[seed] usuario asignado a equipo %s: %s",
            DEMO_VENTAS_TEAM_NAME,
            email_norm,
        )


def seed_demo_if_empty(db: Session) -> None:
    exists = db.scalars(select(Company).where(Company.name == DEMO_COMPANY_NAME)).first()
    if exists:
        migrate_legacy_roles(db)
        _ensure_user_fields(db)
        seed_demo_test_users(db)
        seed_demo_ventas_team(db)
        seed_compa_company(db)
        return

    company = Company(name=DEMO_COMPANY_NAME, employee_count=72, plan="starter")
    db.add(company)
    db.flush()

    wallet = CreditWallet(company_id=company.id, total_balance=500)
    db.add(wallet)

    gerente = User(
        company_id=company.id,
        first_name="Admin",
        last_name="Demo",
        name="Admin Demo",
        email="admin@costguard.demo",
        password_hash=hash_password(DEFAULT_DEMO_PASSWORD),
        role=UserRole.gerente.value,
        is_active=True,
    )
    manager = User(
        company_id=company.id,
        first_name="Manager",
        last_name="Demo",
        name="Manager Demo",
        email="manager@costguard.demo",
        password_hash=hash_password(DEFAULT_DEMO_PASSWORD),
        role=UserRole.manager.value,
        is_active=True,
    )
    sellers = [
        User(
            company_id=company.id,
            first_name="Ana",
            last_name="Vendedora",
            name="Ana Vendedora",
            email="ana@costguard.demo",
            password_hash=hash_password(DEFAULT_DEMO_PASSWORD),
            role=UserRole.sdr.value,
            is_active=True,
        ),
        User(
            company_id=company.id,
            first_name="Luis",
            last_name="Vendedor",
            name="Luis Vendedor",
            email="luis@costguard.demo",
            password_hash=hash_password(DEFAULT_DEMO_PASSWORD),
            role=UserRole.sdr.value,
            is_active=True,
        ),
        User(
            company_id=company.id,
            first_name="María",
            last_name="Vendedora",
            name="María Vendedora",
            email="maria@costguard.demo",
            password_hash=hash_password(DEFAULT_DEMO_PASSWORD),
            role=UserRole.sdr.value,
            is_active=True,
        ),
    ]
    db.add_all([gerente, manager, *sellers])
    db.flush()

    products = [
        Product(
            company_id=company.id,
            name="Plataforma Nexus",
            description="Software de ventas y seguimiento de pipeline.",
            value_proposition="Consolida prospectos, campañas y reporting en un solo lugar.",
            target_notes="Mid-market B2B, equipos de 5–50 vendedores.",
        ),
        Product(
            company_id=company.id,
            name="Paquete Onboarding",
            description="Implementación guiada y plantillas iniciales.",
            value_proposition="Arranque rápido con datos de ejemplo y buenas prácticas.",
            target_notes="Nuevas cuentas enterprise o migraciones.",
        ),
    ]
    db.add_all(products)
    db.flush()

    s1, s2, s3 = sellers
    allocations = [
        SellerCreditAllocation(
            company_id=company.id,
            seller_id=s1.id,
            allocated_balance=150,
            used_balance=30,
        ),
        SellerCreditAllocation(
            company_id=company.id,
            seller_id=s2.id,
            allocated_balance=120,
            used_balance=20,
        ),
        SellerCreditAllocation(
            company_id=company.id,
            seller_id=s3.id,
            allocated_balance=130,
            used_balance=10,
        ),
    ]
    db.add_all(allocations)
    seed_demo_test_users(db)
    seed_demo_ventas_team(db)
    seed_compa_company(db)


def seed_compa_company(db: Session) -> None:
    """Empresa ejemplo del spec con Juan, Martina, Laura, Pedro."""
    company = db.scalars(select(Company).where(Company.name == COMPA_COMPANY_NAME)).first()
    if company is None:
        company = Company(name=COMPA_COMPANY_NAME, employee_count=24, plan="starter")
        db.add(company)
        db.flush()
        db.add(CreditWallet(company_id=company.id, total_balance=300))

    spec = [
        ("Juan", "Pérez", "juan@compa.demo", UserRole.sdr),
        ("Martina", "López", "martina@compa.demo", UserRole.sdr),
        ("Laura", "García", "laura@compa.demo", UserRole.manager),
        ("Pedro", "Gómez", "pedro@compa.demo", UserRole.gerente),
    ]
    for first, last, email, role in spec:
        existing = db.scalars(
            select(User).where(User.company_id == company.id, User.email == email)
        ).first()
        if existing:
            if not existing.password_hash:
                existing.password_hash = hash_password(DEFAULT_DEMO_PASSWORD)
            continue
        db.add(
            User(
                company_id=company.id,
                first_name=first,
                last_name=last,
                name=f"{first} {last}",
                email=email,
                password_hash=hash_password(DEFAULT_DEMO_PASSWORD),
                role=role.value,
                is_active=True,
            )
        )
