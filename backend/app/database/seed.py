"""Datos demo idempotentes para desarrollo local."""

import logging

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import Company, User
from app.models.credit_wallet import CreditWallet
from app.models.campaign import Campaign
from app.models.enums import UserRole
from app.models.product import Product
from app.models.seller_allocation import SellerCreditAllocation
from app.models.team import Team
from app.services.credit_ledger import current_plan_cycle_key, record_credit_ledger
from app.services.credit_plans import credits_for_plan, normalize_plan_key, plan_definition
from app.services.credits import ensure_wallet

_logger = logging.getLogger("nexus.seed")

DEMO_COMPANY_NAME = "CostGuard Demo Client"
DEMO_COMPANY_ALIASES: tuple[str, ...] = ("CostGuard Demo Client", "CostGuard")
DEMO_VENTAS_TEAM_NAME = "Ventas"
COMPA_COMPANY_NAME = "Compa"
DEFAULT_DEMO_PASSWORD = "demo123"

NEXUS_PRODUCT_NAME = "Plataforma Nexus"
NEXUS_VALUE_PROPOSITION = (
    "Automatiza entre un 60% y un 90% de las tareas manuales de prospección outbound, "
    "orquestando email, LinkedIn y WhatsApp en un solo flujo con IA — el SDR solo interviene "
    "cuando el prospecto muestra interés real."
)
NEXUS_PRODUCT_DESCRIPTION = (
    "Plataforma Nexus es un software B2B de ventas outbound para equipos que pierden horas en "
    "tareas operativas. Orquesta secuencias multicanal, genera borradores con IA alineados al "
    "playbook, detecta inbound y centraliza prospectos, campañas y reporting para managers."
)
NEXUS_TARGET_NOTES = (
    "Problemas: SDR operativo en lugar de vender; canales dispersos; inbound que se pierde. "
    "Beneficios: 60–90% menos carga manual, secuencias coordinadas, cola operativa y visibilidad manager."
)
DEMO_ICP_ANALYSIS = {
    "summary": "Equipos comerciales B2B mid-market con outbound multicanal en LATAM.",
    "recommendations": (
        "Priorizar roles comerciales/ventas; evitar agencias, recruiters y consultoras puras."
    ),
    "notes": "ICP demo — refiná con «Analizar ICP» en la campaña.",
}

DEMO_TEST_USERS: tuple[tuple[str, str, str, UserRole], ...] = (
    ("SDR", "Test", "sdr@test.com", UserRole.sdr),
    ("Manager", "Test", "manager@test.com", UserRole.manager),
    ("Director", "Test", "director@test.com", UserRole.gerente),
    ("Owner", "Test", "owner@test.com", UserRole.owner),
)
DEMO_TEST_EMAILS: frozenset[str] = frozenset(email.strip().lower() for _, _, email, _ in DEMO_TEST_USERS)


def is_demo_test_email(email: str | None) -> bool:
    return str(email or "").strip().lower() in DEMO_TEST_EMAILS


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


def _campaign_count(db: Session, company_id: int) -> int:
    return len(db.scalars(select(Campaign.id).where(Campaign.company_id == company_id)).all())


def _get_or_create_demo_company(db: Session) -> Company:
    """
    Empresa demo canónica = la que ya tiene datos (campañas), nunca una CostGuard vacía nueva.
    Unifica aliases huérfanos (p.ej. 'CostGuard' vacía + 'CostGuard Demo Client' con data).
    """
    found: list[Company] = []
    for name in DEMO_COMPANY_ALIASES:
        row = db.scalars(select(Company).where(Company.name == name)).first()
        if row is not None and all(r.id != row.id for r in found):
            found.append(row)

    if not found:
        company = Company(name=DEMO_COMPANY_NAME, employee_count=72, plan="starter")
        db.add(company)
        db.flush()
        db.add(CreditWallet(company_id=company.id, total_balance=credits_for_plan("starter")))
        _logger.info("[seed] empresa demo creada: %s (id=%s)", DEMO_COMPANY_NAME, company.id)
        return company

    # Preferir la que tiene campañas; si empatan, el id más bajo (histórico).
    found.sort(key=lambda c: (-_campaign_count(db, c.id), c.id))
    primary = found[0]
    if primary.name != DEMO_COMPANY_NAME:
        _logger.info(
            "[seed] empresa demo canónica id=%s name=%r (sin renombrar; outreach limpia Demo)",
            primary.id,
            primary.name,
        )

    for dup in found[1:]:
        _merge_orphan_demo_company(db, primary=primary, orphan=dup)

    return primary


def _merge_orphan_demo_company(db: Session, *, primary: Company, orphan: Company) -> None:
    """Mueve usuarios demo del huérfano a la empresa con datos y borra el huérfano vacío."""
    if primary.id == orphan.id:
        return
    demo_emails = {email.strip().lower() for _, _, email, _ in DEMO_TEST_USERS}
    moved = 0
    users = db.scalars(select(User).where(User.company_id == orphan.id)).all()
    for u in users:
        email = (u.email or "").strip().lower()
        if email in demo_emails or _campaign_count(db, orphan.id) == 0:
            u.company_id = primary.id
            moved += 1
    orphan_wallet = db.scalars(
        select(CreditWallet).where(CreditWallet.company_id == orphan.id)
    ).first()
    if orphan_wallet is not None and _campaign_count(db, orphan.id) == 0:
        db.delete(orphan_wallet)
    remaining_users = db.scalars(select(User).where(User.company_id == orphan.id)).all()
    if not remaining_users and _campaign_count(db, orphan.id) == 0:
        db.delete(orphan)
        _logger.info(
            "[seed] empresa demo huérfana id=%s (%r) fusionada → id=%s (usuarios movidos=%s)",
            orphan.id,
            orphan.name,
            primary.id,
            moved,
        )
    else:
        _logger.warning(
            "[seed] no se pudo borrar empresa huérfana id=%s; quedan users=%s campaigns=%s",
            orphan.id,
            len(remaining_users),
            _campaign_count(db, orphan.id),
        )
    db.flush()


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
        # No pisar el nombre del login (seed "Director Test" destruía la firma real).
        if not (user.first_name or "").strip():
            user.first_name = first_name
            user.last_name = last_name
            user.name = display_name
        user.email = email_norm
        user.password_hash = password_hash
        user.role = role.value
        user.is_active = True
        _logger.info(
            "[seed] usuario demo ACTUALIZADO: %s | rol=%s | empresa=%s (id=%s) | nombre=%s",
            email_norm,
            role.value,
            DEMO_COMPANY_NAME,
            company.id,
            user.name,
        )


def deactivate_demo_test_users(db: Session) -> int:
    """Prod / real mode: los @test.com no deben operar ni aparecer en créditos."""
    n = 0
    for email_norm in DEMO_TEST_EMAILS:
        users = db.scalars(select(User).where(User.email == email_norm)).all()
        for user in users:
            if user.is_active:
                user.is_active = False
                n += 1
                _logger.info(
                    "[seed] usuario demo desactivado: %s id=%s company_id=%s",
                    email_norm,
                    user.id,
                    user.company_id,
                )
    db.flush()
    return n


def ensure_individual_campaigns_for_all_companies(db: Session) -> int:
    """Crea «Secuencias individuales» en cada empresa que tenga producto y usuario."""
    from app.models.company import Company
    from app.services.manual_sequence_kickoff import ensure_individual_container_for_company

    created_or_kept = 0
    companies = db.scalars(select(Company).order_by(Company.id.asc())).all()
    for company in companies:
        try:
            row = ensure_individual_container_for_company(db, company_id=int(company.id))
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "[seed] no se pudo garantizar Secuencias individuales company_id=%s: %s",
                company.id,
                str(exc)[:240],
            )
            continue
        if row is not None:
            created_or_kept += 1
    db.flush()
    return created_or_kept


def prepare_production_workspace(db: Session) -> None:
    deactivate_demo_test_users(db)
    n = ensure_individual_campaigns_for_all_companies(db)
    _logger.info("[seed] prod: Secuencias individuales listas en %s empresa(s)", n)


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


def seed_demo_credit_wallet(db: Session, company: Company) -> None:
    """
    Demo: plan Starter acreditado al pool (600).
    No pisa asignaciones existentes (evita dejar al SDR en 0 en cada reload).
    Si no hay ninguna asignación, reparte un cupo demo al SDR.
    """
    if company.name != DEMO_COMPANY_NAME:
        return

    company.plan = normalize_plan_key(company.plan or "starter")
    company.billing_status = company.billing_status or "none"
    if (company.billing_provider or "").strip().lower() in ("", "none", "dev"):
        company.billing_provider = None
    plan_credits = credits_for_plan(company.plan)
    wallet = ensure_wallet(db, company)
    cycle = current_plan_cycle_key()
    prev_balance = int(wallet.total_balance or 0)

    # Solo reconciliar el pool si está vacío o por debajo del plan (no quemar saldos altos de demo).
    if prev_balance < plan_credits:
        wallet.total_balance = plan_credits
        wallet.plan_cycle_key = cycle
        wallet.plan_last_credited_at = datetime.now(UTC)
        plan = plan_definition(company.plan)
        record_credit_ledger(
            db,
            company_id=company.id,
            kind="reconcile",
            amount=plan_credits - prev_balance,
            note=(
                f"Demo top-up pool → {plan_credits} ({plan.label} / {cycle}); "
                f"antes={prev_balance}"
            ),
        )
        db.flush()

    existing = db.scalars(
        select(SellerCreditAllocation).where(SellerCreditAllocation.company_id == company.id)
    ).all()
    if existing:
        # Si ya hay cupos pero quedaron en 0 (consumo / reload), reponer demo al SDR/manager.
        demo_sdr_credits = min(2_000, int(wallet.total_balance or 0))
        refilled = 0
        for alloc in existing:
            left = int(alloc.allocated_balance or 0) - int(alloc.used_balance or 0)
            if left <= 0 and demo_sdr_credits > 0:
                used = int(alloc.used_balance or 0)
                alloc.allocated_balance = used + demo_sdr_credits
                refilled += 1
        if refilled:
            db.flush()
            _logger.info(
                "[seed] creditos demo repuestos asignaciones=%s empresa=%s cupo=%s pool=%s",
                refilled,
                company.id,
                demo_sdr_credits,
                int(wallet.total_balance or 0),
            )
        else:
            _logger.info(
                "[seed] creditos demo empresa=%s pool=%s asignaciones=%s (sin reset)",
                company.id,
                int(wallet.total_balance or 0),
                len(existing),
            )
        return

    sdr = db.scalars(
        select(User).where(
            User.company_id == company.id,
            User.email == "sdr@test.com",
        )
    ).first()
    demo_sdr_credits = min(2_000, int(wallet.total_balance or 0))
    if sdr is not None and demo_sdr_credits > 0:
        db.add(
            SellerCreditAllocation(
                company_id=company.id,
                seller_id=sdr.id,
                allocated_balance=demo_sdr_credits,
                used_balance=0,
            )
        )
        db.flush()
        _logger.info(
            "[seed] creditos demo SDR asignados=%s empresa=%s pool=%s",
            demo_sdr_credits,
            company.id,
            int(wallet.total_balance or 0),
        )
    else:
        _logger.info(
            "[seed] creditos demo empresa=%s pool=%s asignado=0 (sin SDR)",
            company.id,
            int(wallet.total_balance or 0),
        )


def seed_demo_nexus_product_copy(db: Session, company: Company) -> None:
    """Actualiza copy del producto Nexus en demo (idempotente)."""
    if company.name != DEMO_COMPANY_NAME:
        return
    product = db.scalars(
        select(Product).where(
            Product.company_id == company.id,
            Product.name == NEXUS_PRODUCT_NAME,
        )
    ).first()
    if product is None:
        return
    product.description = NEXUS_PRODUCT_DESCRIPTION
    product.value_proposition = NEXUS_VALUE_PROPOSITION
    product.target_notes = NEXUS_TARGET_NOTES
    # No tocar is_active: si el usuario lo eliminó (soft delete), no lo reactivamos.
    db.flush()


def seed_demo_campaign_outreach_defaults(db: Session, company: Company) -> None:
    """Campañas demo: remitente, ICP IA y producto Nexus cuando falten."""
    if company.name != DEMO_COMPANY_NAME:
        return
    nexus_product = db.scalars(
        select(Product).where(
            Product.company_id == company.id,
            Product.name == NEXUS_PRODUCT_NAME,
        )
    ).first()
    campaigns = db.scalars(select(Campaign).where(Campaign.company_id == company.id)).all()
    for campaign in campaigns:
        if nexus_product is not None and not campaign.product_id:
            campaign.product_id = nexus_product.id
        if not (getattr(campaign, "sender_name", None) or "").strip():
            campaign.sender_name = "Joaquin"
        if not getattr(campaign, "icp_ai_last_analysis", None):
            campaign.icp_ai_last_analysis = dict(DEMO_ICP_ANALYSIS)
        if getattr(campaign, "post_sequence_followup_enabled", True) is False:
            campaign.post_sequence_followup_enabled = True
    db.flush()


def seed_demo_if_empty(db: Session) -> None:
    # Unifica aliases huérfanos antes de cualquier otra cosa.
    primary = _get_or_create_demo_company(db)
    exists = primary
    if exists:
        migrate_legacy_roles(db)
        _ensure_user_fields(db)
        seed_demo_test_users(db)
        seed_demo_ventas_team(db)
        seed_compa_company(db)
        seed_demo_nexus_product_copy(db, exists)
        seed_demo_campaign_outreach_defaults(db, exists)
        seed_demo_credit_wallet(db, exists)
        return

    company = Company(name=DEMO_COMPANY_NAME, employee_count=72, plan="starter")
    db.add(company)
    db.flush()

    wallet = CreditWallet(company_id=company.id, total_balance=credits_for_plan("starter"))
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
            name=NEXUS_PRODUCT_NAME,
            description=NEXUS_PRODUCT_DESCRIPTION,
            value_proposition=NEXUS_VALUE_PROPOSITION,
            target_notes=NEXUS_TARGET_NOTES,
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

    seed_demo_test_users(db)
    seed_demo_ventas_team(db)
    seed_demo_nexus_product_copy(db, company)
    seed_demo_campaign_outreach_defaults(db, company)
    seed_demo_credit_wallet(db, company)
    seed_compa_company(db)


def seed_compa_company(db: Session) -> None:
    """Empresa ejemplo del spec con Juan, Martina, Laura, Pedro."""
    company = db.scalars(select(Company).where(Company.name == COMPA_COMPANY_NAME)).first()
    if company is None:
        company = Company(name=COMPA_COMPANY_NAME, employee_count=24, plan="starter")
        db.add(company)
        db.flush()
        db.add(CreditWallet(company_id=company.id, total_balance=0))

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
