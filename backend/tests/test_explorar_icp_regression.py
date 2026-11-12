"""Checklist Search 8–10 ICPs — solo mocks (cero Prospeo real).

Cubre: fit duro, off-ICP fuera, scopes con industria, total honest, contacto 1ª hoja.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.company import Company
from app.models.enums import UserRole
from app.models.seller_allocation import SellerCreditAllocation
from app.models.user import User
from app.services import explorar as svc


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _setup(db, *, credits=20):
    company = Company(name="Co", employee_count=5, plan="starter")
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id,
        email="sdr@test.com",
        first_name="SDR",
        last_name="T",
        name="SDR T",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add(user)
    db.flush()
    db.add(
        SellerCreditAllocation(
            company_id=company.id, seller_id=user.id, allocated_balance=credits, used_balance=0
        )
    )
    db.flush()
    return company.id, user.id


# --- ICP fixtures: (name, build_filters kwargs, good_hit, bad_hits) ---

ICP_CASES = [
    (
        "uber_cl_restaurants_founder",
        dict(
            location="Chile",
            industries=["Restaurants"],
            seniorities=["Founder/Owner"],
            departments=["Operations"],
        ),
        {
            "country": "Chile",
            "role": "Dueño",
            "seniority": "Founder/Owner",
            "industry": "Restaurants",
            "company_name": "Restó Sur",
            "departments": [],
        },
        [
            {
                "country": "Chile",
                "role": "Gerente Logística",
                "seniority": "Manager",
                "industry": "Construction",
                "company_name": "Hormigones SA",
                "departments": ["Logistics"],
            },
            {
                "country": "Argentina",
                "role": "Founder",
                "seniority": "Founder/Owner",
                "industry": "Restaurants",
                "company_name": "Pizzería BA",
            },
        ],
    ),
    (
        "ar_mfg_hr_manager",
        dict(
            location="Argentina",
            industries=["General Manufacturing"],
            seniorities=["Manager"],
            departments=["Human Resources"],
        ),
        {
            "country": "Argentina",
            "role": "HR Manager",
            "seniority": "Manager",
            "industry": "Food and Beverage Manufacturing",
            "departments": ["Human Resources"],
            "company_name": "Fábrica Norte",
        },
        [
            {
                "country": "Argentina",
                "role": "HR Manager",
                "seniority": "Manager",
                "industry": "IT Services and IT Consulting",
                "departments": ["Human Resources"],
                "company_name": "SoftConsult",
            },
            {
                "country": "Argentina",
                "role": "HR Manager",
                "seniority": "Entry",
                "industry": "General Manufacturing",
                "departments": ["Human Resources"],
            },
        ],
    ),
    (
        "mx_saas_csuite_sales",
        dict(
            location="Mexico",
            industries=["Software Development"],
            seniorities=["C-Suite"],
            departments=["Sales"],
        ),
        {
            "country": "Mexico",
            "role": "Chief Revenue Officer",
            "seniority": "C-Suite",
            "industry": "Software Development",
            "departments": ["Sales"],
            "company_name": "NubeMX",
        },
        [
            {
                "country": "Mexico",
                "role": "Director de ventas",
                "seniority": "Director",
                "industry": "Advertising Services",
                "departments": ["Sales"],
            },
            {
                "country": "Mexico",
                "role": "Account Executive",
                "seniority": "Entry",
                "industry": "Software Development",
                "departments": ["Sales"],
                "company_name": "SaaS MX Junior",
            },
        ],
    ),
    (
        "es_finance_director_enterprise",
        dict(
            location="España",
            industries=["Financial Services"],
            seniorities=["Director"],
            departments=["Finance"],
            headcounts=["1001-2000", "2001-5000", "5001-10000", "10001+"],
        ),
        {
            "country": "Spain",
            "role": "Finance Director",
            "seniority": "Director",
            "industry": "Financial Services",
            "departments": ["Finance"],
            "company_size": "2001-5000",
            "company_name": "Banco Grande",
        },
        [
            {
                "country": "Spain",
                "role": "Finance Director",
                "seniority": "Director",
                "industry": "Financial Services",
                "departments": ["Finance"],
                "company_size": "11-20",
                "company_name": "Fintech Pyme",
            },
            {
                "country": "Spain",
                "role": "CFO",
                "seniority": "C-Suite",
                "industry": "Renewable Energy",
                "departments": ["Finance"],
                "company_size": "2001-5000",
            },
        ],
    ),
    (
        "ar_saas_head_marketing_smb",
        dict(
            location="Argentina",
            industries=["Software Development"],
            seniorities=["Head"],
            departments=["Marketing"],
            headcounts=["11-20", "21-50"],
        ),
        {
            "country": "Argentina",
            "role": "Head of Marketing",
            "seniority": "Head",
            "industry": "Software Development",
            "departments": ["Marketing"],
            "company_size": "21-50",
            "company_name": "SaaS BA",
        },
        [
            {
                "country": "Argentina",
                "role": "Head of Marketing",
                "seniority": "Head",
                "industry": "Software Development",
                "departments": ["Marketing"],
                "company_size": "2001-5000",
            },
            {
                "country": "Argentina",
                "role": "Marketing Specialist",
                "seniority": "Entry",
                "industry": "Software Development",
                "departments": ["Marketing"],
                "company_size": "21-50",
            },
        ],
    ),
    (
        "co_ecommerce_manager_sales",
        dict(
            location="Colombia",
            industries=["Online and Mail Order Retail"],
            seniorities=["Manager"],
            departments=["Sales"],
        ),
        {
            "country": "Colombia",
            "role": "Sales Manager",
            "seniority": "Manager",
            "industry": "Online and Mail Order Retail",
            "departments": ["Sales"],
            "company_name": "Tienda CO",
        },
        [
            {
                "country": "Brazil",
                "role": "Sales Manager",
                "seniority": "Manager",
                "industry": "Online and Mail Order Retail",
                "departments": ["Sales"],
            },
            {
                "country": "Colombia",
                "role": "Sales Manager",
                "seniority": "Manager",
                "industry": "Gambling Facilities and Casinos",
                "departments": ["Sales"],
            },
        ],
    ),
    (
        "joan_saas_es_reject_vet",
        dict(
            location="España",
            industries=["Software Development"],
            seniorities=["Founder/Owner"],
            departments=["C-Suite"],
        ),
        {
            "country": "Spain",
            "role": "CEO & Co-Founder",
            "seniority": "Founder/Owner",
            "industry": "Software Development",
            "company_name": "Codee",
            "departments": ["C-Suite"],
        },
        [
            {
                "country": "Spain",
                "role": "CEO",
                "seniority": "Founder/Owner",
                "industry": "Software Development",
                "company_name": "Clínica Veterinaria Sur",
            },
        ],
    ),
    (
        "pe_logistics_director_ops",
        dict(
            location="Peru",
            industries=["Transportation, Logistics, Supply Chain and Storage"],
            seniorities=["Director"],
            departments=["Operations"],
        ),
        {
            "country": "Peru",
            "role": "Operations Director",
            "seniority": "Director",
            "industry": "Transportation, Logistics, Supply Chain and Storage",
            "departments": ["Operations"],
            "company_name": "LogiPE",
        },
        [
            {
                "country": "Peru",
                "role": "Operations Director",
                "seniority": "Director",
                "industry": "Restaurants",
                "departments": ["Operations"],
            },
        ],
    ),
]


@pytest.mark.parametrize("case_name,kw,good,bads", ICP_CASES, ids=[c[0] for c in ICP_CASES])
def test_icp_good_passes_bad_rejected(case_name, kw, good, bads):
    f = svc.build_filters(**kw)
    assert svc.hit_matches_filters(good, f), f"{case_name}: good debe pasar"
    for i, bad in enumerate(bads):
        assert not svc.hit_matches_filters(bad, f), f"{case_name}: bad[{i}] debe fallar"


@pytest.mark.parametrize("case_name,kw,_good,_bads", ICP_CASES, ids=[c[0] for c in ICP_CASES])
def test_icp_scopes_keep_industry_when_requested(case_name, kw, _good, _bads):
    f = svc.build_filters(**kw)
    if not f.get("company_industry"):
        return
    scopes = svc.volume_scope_filters(f)
    assert scopes
    assert all("company_industry" in s for s in scopes)
    total_scope = svc.total_scope_for_count(f, scopes)
    assert "company_industry" in total_scope


def test_search_regression_matrix_contact_order_and_total(monkeypatch):
    """Una pasada search() por ICP clave: off-ICP fuera, total ICP, 1ª hoja contactables primero."""

    cases = [
        ICP_CASES[0],  # Uber CL
        ICP_CASES[1],  # AR mfg HR
        ICP_CASES[3],  # ES finance size
        ICP_CASES[6],  # Joan vet
    ]

    for case_name, kw, good, bads in cases:
        filters = svc.build_filters(**kw)
        universe = 280 if case_name.startswith("uber") else 900

        def fake(filters_arg, page, _good=good, _bads=bads, _universe=universe):
            details = filters_arg.get("person_contact_details") or {}
            # Mezcla: 1 completo, 1 solo mail, 1 off-ICP, 1 solo LI
            complete = {
                **_good,
                "person_id": f"c_{page}",
                "name": "Complete",
                "has_email": True,
                "has_mobile": True,
                "email_verified": True,
                "mobile_verified": True,
            }
            mail_only = {
                **_good,
                "person_id": f"m_{page}",
                "name": "MailOnly",
                "has_email": True,
                "has_mobile": False,
                "email_verified": True,
                "mobile_verified": False,
            }
            li_only = {
                **_good,
                "person_id": f"l_{page}",
                "name": "LiOnly",
                "has_email": False,
                "has_mobile": False,
            }
            bad = {
                **_bads[0],
                "person_id": f"b_{page}",
                "name": "OffIcp",
                "has_email": True,
                "has_mobile": True,
                "email_verified": True,
                "mobile_verified": True,
            }
            if not details:
                rows = [complete, mail_only, li_only, bad]
                return {"results": rows, "total_count": _universe, "total_pages": 12}
            if details.get("email") == ["VERIFIED"] and details.get("mobile") == ["VERIFIED"]:
                return {"results": [complete, bad], "total_count": 20, "total_pages": 1}
            if details.get("mobile") == ["VERIFIED"]:
                return {"results": [], "total_count": 5, "total_pages": 1}
            if details.get("email") == ["VERIFIED"]:
                return {"results": [mail_only], "total_count": 30, "total_pages": 2}
            return {"results": [], "total_count": 0, "total_pages": 0}

        monkeypatch.setattr(svc, "_fetch_page", fake)
        db = _session()
        cid, uid = _setup(db)
        out = svc.search(db, company_id=cid, user_id=uid, filters=filters, page=1)
        ids = [r["person_id"] for r in out["results"]]
        assert not any(i.startswith("b_") for i in ids), f"{case_name}: off-ICP en resultados"
        assert out["total_count"] >= universe - 1, f"{case_name}: total={out['total_count']}"
        assert out["total_pages"] >= 2, f"{case_name}: pages={out['total_pages']}"
        # Completo antes que mail-only / LI-only si están
        if "c_1" in ids and "m_1" in ids:
            assert ids.index("c_1") < ids.index("m_1"), case_name
        if "c_1" in ids and "l_1" in ids:
            assert ids.index("c_1") < ids.index("l_1"), case_name


def test_filter_change_clears_identity_of_scopes():
    """Dos ICPs distintos → scopes/totals keys distintos (no mezclar cache mental)."""
    a = svc.build_filters(
        location="Chile",
        industries=["Restaurants"],
        seniorities=["Founder/Owner"],
        departments=["Operations"],
    )
    b = svc.build_filters(
        location="Argentina",
        industries=["General Manufacturing"],
        seniorities=["Manager"],
        departments=["Human Resources"],
    )
    assert svc._cache_key(1, a, "total_icp_v2") != svc._cache_key(1, b, "total_icp_v2")
    sa = svc.volume_scope_filters(a)
    sb = svc.volume_scope_filters(b)
    assert sa[0] != sb[0]


def test_empty_hint_pozo_vacio_vs_filtrado():
    f = svc.build_filters(
        location="Chile",
        industries=["Restaurants"],
        seniorities=["Founder/Owner"],
        departments=["Operations"],
        headcounts=["11-20"],
    )
    empty_pool = svc.empty_search_hint(f, raw_seen=0, prospeo_total=0, kept_n=0)
    assert empty_pool and "Pozo vacío" in empty_pool
    assert "tamaño" in empty_pool
    filtered = svc.empty_search_hint(f, raw_seen=40, prospeo_total=200, kept_n=0)
    assert filtered and "ninguno pasó el ICP" in filtered
    assert svc.empty_search_hint(f, raw_seen=10, prospeo_total=50, kept_n=3) is None


def test_size_country_dept_hard_in_core_and_match():
    f = svc.build_filters(
        location="España",
        industries=["Financial Services"],
        seniorities=["Director"],
        departments=["Finance"],
        headcounts=["1001-2000", "2001-5000"],
    )
    core = svc.core_filters_for_prospeo(f)
    assert "company_headcount_range" in core
    assert "person_location_search" in core
    assert "person_department" in core
    ok = {
        "country": "Spain",
        "role": "Finance Director",
        "seniority": "Director",
        "industry": "Financial Services",
        "departments": ["Finance"],
        "company_size": "2001-5000",
    }
    assert svc.hit_matches_filters(ok, f)
    assert not svc.size_hard_ok({**ok, "company_size": "11-20"}, f)
    assert not svc.location_hard_ok({**ok, "country": "Mexico"}, f)
    assert not svc.department_hard_ok(
        {**ok, "departments": ["Sales"], "role": "Account Executive", "seniority": "Director"},
        f,
    )


def test_search_returns_empty_hint_when_zero(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_fetch_page",
        lambda f, p: {"results": [], "total_count": 0, "total_pages": 0},
    )
    db = _session()
    cid, uid = _setup(db)
    filters = svc.build_filters(
        location="Chile",
        industries=["Restaurants"],
        seniorities=["Founder/Owner"],
        departments=["Operations"],
    )
    out = svc.search(db, company_id=cid, user_id=uid, filters=filters, page=1)
    assert out["results"] == []
    assert out.get("empty_hint")
    assert "Pozo vacío" in out["empty_hint"]
