"""Explorar: filtros nuevos (tamaño, nivel, área) y «Posibles clientes» por producto."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.services import explorar as svc
from app.services import explorar_potential_customers as pc


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _product(db, **kw):
    company = Company(name="Co", employee_count=5, plan="starter")
    db.add(company)
    db.flush()
    product = Product(company_id=company.id, name=kw.get("name", "CRM"), description="CRM para pymes")
    db.add(product)
    db.flush()
    return product


def test_build_filters_headcount_and_department_only_valid_values():
    f = svc.build_filters(
        headcounts=["11-20", "21-50", "999-1"],
        departments=["Sales", "Customer Service"],
    )
    assert f["company_headcount_range"] == ["11-20", "21-50"]
    assert f["person_department"] == {"include": ["Sales"]}
    assert svc.has_targeting(svc.build_filters(headcounts=["1-10"]))


def test_normalize_keeps_only_prospeo_values_and_drops_department_with_titles():
    out = pc._normalize(
        {
            "titles": ["Gerente Comercial", "Head of Sales", "gerente comercial"],
            "industries": ["software development", "Inventada"],
            "headcounts": ["11-20", "nope"],
            "seniorities": ["Director", "Jefe"],
            "departments": ["Sales"],
            "summary": "Vendedores B2B",
        }
    )
    assert out["titles"] == ["Gerente Comercial", "Head of Sales"]
    assert out["industries"] == ["Software Development"]
    assert out["headcounts"] == ["11-20"]
    assert out["seniorities"] == ["Director"]
    assert out["departments"] == []
    assert out["location"] == pc.DEFAULT_SEARCH_LOCATION


def test_without_openai_uses_last_campaign_role():
    pc.clear_cache()
    db = _session()
    product = _product(db)
    seller = User(
        company_id=product.company_id,
        email="sdr@test.com",
        first_name="S",
        last_name="T",
        name="S T",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add(seller)
    db.flush()
    db.add(
        Campaign(
            company_id=product.company_id,
            seller_id=seller.id,
            product_id=product.id,
            name="Pymes AR",
            target_role="Gerente comercial, Dueño",
            prospect_count=10,
            calendar_link="https://cal.example/x",
            timezone="America/Argentina/Buenos_Aires",
            available_hours="9-18",
            tone="cercano",
            estimated_meetings_min=1,
            estimated_meetings_max=2,
            estimated_cost_min=1,
            estimated_cost_max=2,
        )
    )
    db.flush()
    out = pc.potential_customer_filters(db, product)
    assert out["source"] == "campaign"
    assert out["titles"] == ["Gerente comercial", "Dueño"]
    assert out["location"] == pc.DEFAULT_SEARCH_LOCATION


def test_without_openai_or_campaign_defaults_to_decision_makers():
    pc.clear_cache()
    db = _session()
    product = _product(db, name="Otro")
    out = pc.potential_customer_filters(db, product)
    assert out["source"] == "default"
    assert "C-Suite" in out["seniorities"]
    assert out["product_name"] == "Otro"
    assert out["location"] == pc.DEFAULT_SEARCH_LOCATION


def test_ai_runs_on_product_refresh_not_on_search(monkeypatch):
    pc.clear_cache()
    calls = []

    def fake_ai(product):
        calls.append(product.id)
        return pc._normalize(
            {"titles": ["CFO"], "industries": ["Banking"], "summary": "x"},
            fallback_location=pc.location_from_product(product),
        )

    monkeypatch.setattr(pc, "_ai_filters", fake_ai)
    db = _session()
    product = _product(db)
    search_before = pc.potential_customer_filters(db, product)
    assert search_before["source"] == "default"
    assert calls == []

    saved = pc.refresh_product_potential_filters(db, product)
    assert saved["source"] == "ai" and saved["titles"] == ["CFO"]
    assert calls == [product.id]

    second = pc.potential_customer_filters(db, product)
    assert second["titles"] == ["CFO"]
    assert second["location"] == pc.DEFAULT_SEARCH_LOCATION
    assert calls == [product.id]


def test_stale_snapshot_is_ignored_until_refresh(monkeypatch):
    pc.clear_cache()
    monkeypatch.setattr(
        pc,
        "_ai_filters",
        lambda product: pc._normalize({"titles": ["CFO"], "summary": "x"}),
    )
    db = _session()
    product = _product(db)
    pc.refresh_product_potential_filters(db, product)
    product.description = "Nexus vende en LatAm nada más, sin incluir Brasil."
    monkeypatch.setattr(pc, "_ai_filters", lambda product: (_ for _ in ()).throw(AssertionError("no AI on search")))
    out = pc.potential_customer_filters(db, product)
    assert out["source"] == "default"
    assert out["location"] == "LATAM (sin Brasil)"


def test_location_from_product_text_latam_without_brazil():
    assert (
        pc.location_from_product_text(
            "Nexus vende en LatAm nada más, sin incluir Brasil."
        )
        == "LATAM (sin Brasil)"
    )
    assert pc.location_from_product_text("SaaS para clínicas en México y Argentina") == "LATAM (sin Brasil)"
    assert pc.location_from_product_text("We only sell in the United States") == "United States"
    assert pc.location_from_product_text("") == pc.DEFAULT_SEARCH_LOCATION
