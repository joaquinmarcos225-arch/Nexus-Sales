"""Verificación manual/automática del flujo créditos + prospecciones por campaña."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models import Campaign, Company, Product, User
from app.models.enums import UserRole
from app.models.seller_allocation import SellerCreditAllocation
from app.routes.prospects import _persist_new_prospect
from app.schemas.prospect import ProspectCreate
from app.services.campaign_prospects import count_campaign_prospects
from app.services.credits import (
    CreditError,
    get_user_available_credits,
    release_user_credits,
    reserve_campaign_prospection_credits,
)


def _line(ok: bool, msg: str) -> None:
    print(f"{'OK' if ok else 'FAIL'}: {msg}")


def main() -> int:
    db = SessionLocal()
    failures = 0
    campaign_id = None
    company_id = None
    seller_id = None
    reserved = 0

    try:
        company = db.scalars(
            select(Company).where(Company.name == "CostGuard Demo Client")
        ).first()
        if company is None:
            _line(False, "Empresa demo no encontrada")
            return 1
        company_id = company.id

        seller = db.scalars(
            select(User).where(User.company_id == company.id, User.email == "sdr@test.com")
        ).first()
        if seller is None:
            _line(False, "Usuario sdr@test.com no encontrado")
            return 1
        seller_id = seller.id

        product = db.scalars(
            select(Product).where(Product.company_id == company.id).order_by(Product.id)
        ).first()
        if product is None:
            _line(False, "Sin productos en empresa demo")
            return 1

        alloc = db.scalars(
            select(SellerCreditAllocation).where(
                SellerCreditAllocation.company_id == company_id,
                SellerCreditAllocation.seller_id == seller_id,
            )
        ).first()
        if alloc is None:
            alloc = SellerCreditAllocation(
                company_id=company_id,
                seller_id=seller_id,
                allocated_balance=200,
                used_balance=0,
            )
            db.add(alloc)
            db.flush()
            _line(True, "Asignación demo creada para sdr@test.com (200 créditos)")

        before = get_user_available_credits(db, company_id, seller_id)
        reserved = 5
        _line(before >= reserved, f"SDR creditos disponibles antes: {before} (necesita >={reserved})")
        if before < reserved:
            failures += 1
            return 1

        reserve_campaign_prospection_credits(
            db,
            company_id,
            seller_id,
            reserved,
            campaign_name="QA prospecciones",
        )
        after_reserve = get_user_available_credits(db, company_id, seller_id)
        _line(
            after_reserve == before - reserved,
            f"Reserva campana: {before} -> {after_reserve} (esperado {before - reserved})",
        )
        if after_reserve != before - reserved:
            failures += 1

        campaign = Campaign(
            company_id=company_id,
            seller_id=seller_id,
            product_id=product.id,
            name=f"QA prospecciones {datetime.now(UTC).strftime('%H%M%S')}",
            status="draft",
            autopilot_status="off",
            target_company_size="50-200",
            target_industry="SaaS",
            target_country="Argentina",
            target_language="es",
            target_role="CEO",
            prospect_count=reserved,
            calendar_link="https://calendar.google.com/calendar/u/0/r",
            timezone="America/Argentina/Buenos_Aires",
            available_hours="9-18",
            tone="profesional",
            allowed_channels=["linkedin", "email"],
            estimated_meetings_min=1,
            estimated_meetings_max=2,
            estimated_cost_min=0,
            estimated_cost_max=0,
            estimated_avg_cost_per_meeting=0.0,
        )
        db.add(campaign)
        db.flush()
        campaign_id = campaign.id
        _line(True, f"Campaña QA creada id={campaign_id} cupo={reserved}")

        imported_target = 3
        for i in range(imported_target):
            _persist_new_prospect(
                db,
                campaign,
                ProspectCreate(
                    name=f"QA Contact {i + 1}",
                    company_name=f"QA Co {i + 1}",
                    role="CEO",
                    email=f"qa{i + 1}@example.com",
                ),
            )
        imported = count_campaign_prospects(db, campaign.id)
        credits_after_imports = get_user_available_credits(db, company_id, seller_id)
        _line(imported == imported_target, f"Importados {imported}/{imported_target} sin cobro extra")
        _line(
            credits_after_imports == after_reserve,
            f"Creditos tras imports sin cambio: {credits_after_imports}",
        )
        if imported != imported_target or credits_after_imports != after_reserve:
            failures += 1

        blocked = False
        try:
            _persist_new_prospect(
                db,
                campaign,
                ProspectCreate(
                    name="QA Overflow",
                    company_name="QA Overflow Co",
                    role="CEO",
                    email="overflow@example.com",
                ),
            )
            for extra in range(reserved - imported_target):
                _persist_new_prospect(
                    db,
                    campaign,
                    ProspectCreate(
                        name=f"QA Extra {extra}",
                        company_name=f"QA Extra Co {extra}",
                        role="CEO",
                        email=f"qaextra{extra}@example.com",
                    ),
                )
        except Exception as exc:
            blocked = "limite" in str(exc).lower() or "alcanzaste" in str(exc).lower()
        imported_full = count_campaign_prospects(db, campaign.id)
        _line(imported_full == reserved, f"Cupo completo {imported_full}/{reserved}")
        _line(blocked, "Sexto import bloqueado cuando cupo lleno")
        if imported_full != reserved:
            failures += 1

        # Borrar campaña con 5/5 usados: no devuelve créditos (prospecciones consumidas)
        db.delete(campaign)
        db.commit()
        final_full = get_user_available_credits(db, company_id, seller_id)
        _line(
            final_full == before - reserved,
            f"Delete con cupo lleno: {final_full} (esperado {before - reserved})",
        )
        if final_full != before - reserved:
            failures += 1

        # Segunda campaña: uso parcial + delete devuelve slots sin usar
        reserve_campaign_prospection_credits(
            db, company_id, seller_id, 4, campaign_name="QA parcial"
        )
        campaign2 = Campaign(
            company_id=company_id,
            seller_id=seller_id,
            product_id=product.id,
            name=f"QA parcial {datetime.now(UTC).strftime('%H%M%S')}",
            status="draft",
            autopilot_status="off",
            target_company_size="50-200",
            target_industry="SaaS",
            target_country="Argentina",
            target_language="es",
            target_role="CEO",
            prospect_count=4,
            calendar_link="https://calendar.google.com/calendar/u/0/r",
            timezone="America/Argentina/Buenos_Aires",
            available_hours="9-18",
            tone="profesional",
            allowed_channels=["linkedin", "email"],
            estimated_meetings_min=1,
            estimated_meetings_max=2,
            estimated_cost_min=0,
            estimated_cost_max=0,
            estimated_avg_cost_per_meeting=0.0,
        )
        db.add(campaign2)
        db.flush()
        _persist_new_prospect(
            db,
            campaign2,
            ProspectCreate(
                name="QA Partial 1",
                company_name="QA Partial Co",
                role="CEO",
                email="partial1@example.com",
            ),
        )
        mid = get_user_available_credits(db, company_id, seller_id)
        unused_slots = 4 - 1
        release_user_credits(
            db,
            company_id,
            seller_id,
            unused_slots,
            reason="simular delete campaña parcial",
        )
        db.delete(campaign2)
        db.commit()
        final_partial = get_user_available_credits(db, company_id, seller_id)
        _line(
            final_partial == before - reserved - 1,
            f"Delete parcial (1/4 usados): {final_partial} (esperado {before - reserved - 1})",
        )
        if final_partial != before - reserved - 1:
            failures += 1
        _line(True, f"Estado intermedio tras reserva parcial: {mid}")

        print(f"\nResultado: {failures} fallo(s)")
        return 1 if failures else 0
    except CreditError as e:
        print(f"FAIL: CreditError — {e}")
        db.rollback()
        return 1
    except Exception as e:
        print(f"FAIL: {e}")
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
