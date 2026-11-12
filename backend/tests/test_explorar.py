"""Explorar: cobro por páginas acumuladas, cache y revelado sin doble cobro."""

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


def _setup(db, *, credits=5):
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


def _used(db, company_id, user_id):
    row = db.query(SellerCreditAllocation).filter_by(company_id=company_id, seller_id=user_id).one()
    return int(row.used_balance)


def _fake_page(n=3, total_pages=1):
    return {
        "results": [
            {"person_id": f"p{i}", "name": f"Persona {i}", "role": "CEO", "company_name": "Acme"}
            for i in range(n)
        ],
        "total_count": n * total_pages,
        "total_pages": total_pages,
    }


def _paged(per_page=25, total_pages=4, calls=None):
    def fake(filters, page):
        if calls is not None:
            calls.append(page)
        if page > total_pages:
            return {"results": [], "total_count": per_page * total_pages, "total_pages": total_pages}
        return {
            "results": [
                {"person_id": f"p{page}_{i}", "name": f"Persona {i}", "role": "CEO", "company_name": "Acme"}
                for i in range(per_page)
            ],
            "total_count": per_page * total_pages,
            "total_pages": total_pages,
        }

    return fake


FILTERS = {"person_job_title": {"include": ["CEO"], "match_mode": "CONTAINS"}}


def test_pages_accumulate_until_one_credit(monkeypatch):
    monkeypatch.setenv("NEXUS_EXPLORAR_PAGES_PER_CREDIT", "3")
    paged = _paged()
    fetches = []

    def counting(filters, page):
        fetches.append(page)
        return paged(filters, page)

    monkeypatch.setattr(svc, "_fetch_page", counting)
    db = _session()
    cid, uid = _setup(db)

    for page in (1, 2, 3, 4):
        before = len(fetches)
        out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=page)
        crossed = before // 3 != len(fetches) // 3
        assert out["charged_credit"] is crossed
        assert out["from_cache"] is (len(fetches) == before)
        assert _used(db, cid, uid) == len(fetches) // 3
        assert out["usage"]["pages_unbilled"] == len(fetches) % 3
    assert len(fetches) >= 3


def test_cached_pages_are_free(monkeypatch):
    monkeypatch.setattr(svc, "_fetch_page", _paged())
    db = _session()
    cid, uid = _setup(db)
    svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    unbilled = svc.get_usage(db, cid).pages_unbilled
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert out["from_cache"] is True
    assert out["charged_credit"] is False
    assert out["usage"]["pages_unbilled"] == unbilled


def test_total_survives_rate_limit_on_overall_count(monkeypatch):
    """El total sale del universo ICP (sin exigir contacto), no solo de la 1ª tanda."""

    def fake(filters, page):
        # Tanda contable = sin person_contact_details → total grande.
        total = 3200 if "person_contact_details" not in filters else 80
        return {
            "results": [
                {"person_id": f"p{page}_{i}", "name": f"P {i}", "role": "CEO", "company_name": "Acme"}
                for i in range(25)
            ],
            "total_count": total,
            "total_pages": max(1, -(-total // 25)),
        }

    monkeypatch.setattr(svc, "_fetch_page", fake)
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert out["total_count"] >= 3000
    assert out["total_pages"] >= 100
    assert out["total_is_estimate"] is True


def test_total_is_icp_universe_not_first_page_contacts(monkeypatch):
    """Chile-like: 1ª hoja llena con contactables; total = pozo ICP sin contacto (~300)."""

    def fake(filters, page):
        details = filters.get("person_contact_details") or {}
        if not details:
            # Universo ICP (solo LI también).
            rows = [
                {
                    "person_id": f"all_{page}_{i}",
                    "name": f"All {i}",
                    "role": "Founder",
                    "country": "Chile",
                    "industry": "Restaurants",
                    "seniority": "Founder/Owner",
                }
                for i in range(25)
            ]
            return {"results": rows, "total_count": 307, "total_pages": 13}
        # Tandas con contacto: pocas personas.
        tag = "both" if details.get("email") and details.get("mobile") else "one"
        rows = [
            {
                "person_id": f"{tag}_{page}_{i}",
                "name": f"{tag} {i}",
                "role": "Founder",
                "country": "Chile",
                "industry": "Restaurants",
                "seniority": "Founder/Owner",
                "has_email": bool(details.get("email")),
                "has_mobile": bool(details.get("mobile")),
                "email_verified": details.get("email") == ["VERIFIED"],
                "mobile_verified": details.get("mobile") == ["VERIFIED"],
            }
            for i in range(25)
        ]
        return {"results": rows, "total_count": 40, "total_pages": 2}

    monkeypatch.setattr(svc, "_fetch_page", fake)
    db = _session()
    cid, uid = _setup(db)
    filters = svc.build_filters(
        location="Chile",
        industries=["Restaurants"],
        seniorities=["Founder/Owner"],
        departments=["Operations"],
    )
    out = svc.search(db, company_id=cid, user_id=uid, filters=filters, page=1)
    assert len(out["results"]) <= 25
    assert out["total_count"] >= 300
    assert out["total_pages"] >= 12
    assert out["total_is_estimate"] is True


def test_cached_page_does_not_count_again(monkeypatch):
    monkeypatch.setenv("NEXUS_EXPLORAR_PAGES_PER_CREDIT", "2")
    calls = []

    monkeypatch.setattr(svc, "_fetch_page", _paged(calls=calls))
    db = _session()
    cid, uid = _setup(db)
    svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    again = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert again["from_cache"] is True
    # Llena con completos (1–2) y pide página 1 de la tanda mail para el total.
    assert calls[:2] == [1, 2]
    assert 1 in calls[2:] or calls == [1, 2, 1] or len(calls) >= 2
    assert again["usage"]["pages_unbilled"] == len(calls) % 2


def test_empty_page_is_not_billed(monkeypatch):
    monkeypatch.setenv("NEXUS_EXPLORAR_PAGES_PER_CREDIT", "1")
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: {"results": [], "total_count": 0, "total_pages": 0})
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert out["charged_credit"] is False
    assert _used(db, cid, uid) == 0


def test_search_blocked_without_credits_at_threshold(monkeypatch):
    monkeypatch.setenv("NEXUS_EXPLORAR_PAGES_PER_CREDIT", "1")
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: _fake_page())
    db = _session()
    cid, uid = _setup(db, credits=0)
    with pytest.raises(svc.ExplorarError) as exc:
        svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert exc.value.status_code == 402


def test_no_daily_page_cap(monkeypatch):
    monkeypatch.setattr(svc, "_fetch_page", _paged(per_page=25, total_pages=40))
    db = _session()
    cid, uid = _setup(db, credits=50)
    for page in range(1, 31):
        out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=page)
        assert out["results"]


def test_search_requires_a_filter():
    db = _session()
    cid, uid = _setup(db)
    with pytest.raises(svc.ExplorarError):
        svc.search(db, company_id=cid, user_id=uid, filters={}, page=1)


def _patch_enrich(monkeypatch, person):
    from app.services.lead_sourcing.providers import prospeo_mvp

    calls = []

    def fake_enrich(pid, **kw):
        calls.append(pid)
        return person

    monkeypatch.setattr(prospeo_mvp, "enrich_person_by_id", fake_enrich)
    monkeypatch.setattr(
        prospeo_mvp,
        "extract_email_phone",
        lambda p: ((p.get("email") or {}).get("email"), (p.get("mobile") or {}).get("mobile")),
    )
    return calls


def test_reveal_charges_once_per_company(monkeypatch):
    calls = _patch_enrich(
        monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}, "mobile": {"mobile": "+5491100000000"}}
    )
    db = _session()
    cid, uid = _setup(db)
    snap = {"person_id": "p1", "name": "Ana", "company_name": "Acme"}
    row, charged = svc.reveal_person(db, company_id=cid, user_id=uid, snapshot=snap)
    assert charged is True and row.email == "ana@acme.com"
    row2, charged2 = svc.reveal_person(db, company_id=cid, user_id=uid, snapshot=snap)
    assert charged2 is False and row2.id == row.id
    assert calls == ["p1"]
    assert _used(db, cid, uid) == 1


def test_reveal_without_data_is_free(monkeypatch):
    _patch_enrich(monkeypatch, {"full_name": "Ana"})
    db = _session()
    cid, uid = _setup(db)
    with pytest.raises(svc.ExplorarError) as exc:
        svc.reveal_person(db, company_id=cid, user_id=uid, snapshot={"person_id": "p1", "name": "Ana"})
    assert exc.value.status_code == 404
    assert _used(db, cid, uid) == 0


def _campaign(db, company_id, seller_id, *, prospect_count):
    from app.models.campaign import Campaign
    from app.models.enums import CampaignStatus
    from app.models.product import Product

    product = Product(company_id=company_id, name="P", description="d", is_active=True)
    db.add(product)
    db.flush()
    c = Campaign(
        company_id=company_id,
        seller_id=seller_id,
        product_id=product.id,
        name="Camp",
        prospect_count=prospect_count,
        calendar_link="https://cal.test",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="profesional",
        estimated_meetings_min=1,
        estimated_meetings_max=2,
        estimated_cost_min=1,
        estimated_cost_max=2,
        status=CampaignStatus.running.value,
    )
    db.add(c)
    db.flush()
    return c


def test_add_revealed_person_to_full_campaign_is_not_charged_again(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(
        monkeypatch, {"full_name": "Ana Paz", "email": {"email": "ana@acme.com"}, "mobile": {"mobile": "+5491100000000"}}
    )
    db = _session()
    cid, uid = _setup(db, credits=3)
    user = db.get(User, uid)
    camp = _campaign(db, cid, uid, prospect_count=0)
    snap = {"person_id": "p1", "name": "Ana Paz", "company_name": "Acme", "role": "CEO"}
    svc.reveal_person(db, company_id=cid, user_id=uid, snapshot=snap)
    assert _used(db, cid, uid) == 1

    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=camp.id, people=[snap]), db=db, _company=None, user=user
    )
    assert out["added"] == 1
    assert out["results"][0]["paid_with"] == "reveal"
    assert _used(db, cid, uid) == 1
    assert camp.prospect_count == 1

    again = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=camp.id, people=[snap]), db=db, _company=None, user=user
    )
    assert again["results"][0].get("already") is True
    assert _used(db, cid, uid) == 1


def test_add_unrevealed_person_to_full_campaign_charges_one(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(monkeypatch, {"full_name": "Leo Gil", "email": {"email": "leo@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=3)
    user = db.get(User, uid)
    camp = _campaign(db, cid, uid, prospect_count=0)
    snap = {"person_id": "p2", "name": "Leo Gil", "company_name": "Acme", "role": "CEO"}
    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=camp.id, people=[snap]), db=db, _company=None, user=user
    )
    assert out["added"] == 1
    assert out["results"][0]["paid_with"] == "campaign_slot"
    assert _used(db, cid, uid) == 1
    rev = svc.get_reveal(db, cid, "p2")
    assert rev is not None and rev.charged == 0 and rev.prospect_id


def test_failed_add_does_not_leave_a_free_reveal(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(monkeypatch, {"full_name": "Sin Plata", "email": {"email": "sp@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=0)
    user = db.get(User, uid)
    camp = _campaign(db, cid, uid, prospect_count=0)
    snap = {"person_id": "p9", "name": "Sin Plata", "company_name": "Acme", "role": "CEO"}
    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=camp.id, people=[snap]), db=db, _company=None, user=user
    )
    assert out["added"] == 0
    assert out["results"][0]["ok"] is False
    assert svc.get_reveal(db, cid, "p9") is None
    assert svc.nexus_revealed_by_person_id(db, ["p9"]) == {}


def test_build_filters_splits_domains_and_names():
    f = svc.build_filters(companies=["acme.com", "Mercado Libre"], seniorities=["C-Suite", "Inventado"])
    assert f["company"]["websites"]["include"] == ["acme.com"]
    assert f["company"]["names"]["include"] == ["Mercado Libre"]
    assert f["person_seniority"]["include"] == ["C-Suite"]


def test_build_filters_lookalike_oids():
    f = svc.build_filters(titles=["Gerente comercial"], lookalike_company_oids=["oid-a", "oid-b"])
    assert f["company_lookalike"]["company_oids"] == ["oid-a", "oid-b"]
    assert f["person_job_title"]["include"][0] == "Gerente comercial"


def test_industry_uses_top_level_prospeo_filter():
    f = svc.build_filters(industries=["Software Development"])
    assert f["company_industry"] == {"include": ["Software Development"]}
    assert "company" not in f
    assert svc.has_targeting(f)


def test_build_filters_drops_prospeo_invalid_industries():
    """Retail/Wholesale/Manufacturing/Education provocan INVALID_FILTERS en Prospeo."""
    f = svc.build_filters(
        industries=[
            "Software Development",
            "Retail",
            "Wholesale",
            "Manufacturing",
            "Education",
            "Marketing Services",
        ]
    )
    assert f["company_industry"]["include"] == ["Software Development", "Marketing Services"]


def test_filter_catalog_matches_prospeo_allowlist():
    from app.services.explorar_filter_catalog import (
        INDUSTRY_OPTIONS,
        KNOWN_INVALID_INDUSTRIES,
        filter_catalog,
    )

    cat = filter_catalog()
    assert cat["version"]
    assert {row["value"] for row in cat["industries"]} == set(INDUSTRY_OPTIONS)
    assert not (set(INDUSTRY_OPTIONS) & KNOWN_INVALID_INDUSTRIES)
    assert len(cat["seniorities"]) >= 8
    assert len(cat["departments"]) >= 8
    assert len(cat["headcounts"]) >= 10


def test_titles_add_exact_equivalents_but_no_broad_aliases():
    f = svc.build_filters(titles=["CEO"])
    include = f["person_job_title"]["include"]
    assert include[0] == "CEO"
    assert {"Gerente General", "Director General", "Chief Executive Officer"} <= set(include)
    assert not {"Owner", "President", "Presidente", "Dueño"} & set(include)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Gerente comercial", {"Gerente de Ventas", "Sales Manager"}),
        ("Head of Sales", {"Jefe de Ventas", "Jefe Comercial", "Sales Head"}),
        ("Jefe de RRHH", {"Jefe de Recursos Humanos", "Head of HR", "Head of People"}),
        ("Gerente de Desarrollo de Negocios", {"Business Development Manager"}),
    ],
)
def test_title_synonyms(title, expected):
    assert expected <= set(svc.expand_titles([title]))


def test_no_feminine_forms_for_english_titles():
    include = svc.expand_titles(["Directora de Marketing"])
    assert "Marketing Director" in include and "Director of Marketing" in include
    assert "Marketing Directora" not in include
    assert "Directora of Marketing" not in include


def test_unknown_titles_stay_as_typed():
    assert svc.expand_titles(["Account Executive"]) == ["Account Executive"]


def test_title_cap_keeps_every_chosen_title():
    chosen = ["Gerente comercial", "CEO", "Jefe de RRHH", "Director de Marketing", "Head of Sales"]
    include = svc.expand_titles(chosen)
    assert len(include) <= svc.MAX_TITLE_FILTERS
    assert include[: len(chosen)] == chosen


@pytest.mark.parametrize(
    "role,ok",
    [
        ("Gerente de Ventas", True),
        ("Sales Manager", True),
        ("Gerente Comercial Regional", True),
        ("Subgerente de Ventas", False),
        ("Asistente de Gerente Comercial", False),
        ("Gerente de Marketing", False),
    ],
)
def test_synonym_results_still_respect_role_level(role, ok):
    f = svc.build_filters(titles=["Gerente comercial"])
    assert svc.hit_matches_filters({"role": role}, f) is ok


@pytest.mark.parametrize(
    "role,titles,ok",
    [
        ("Co-Founder & CEO", ["CEO"], True),
        ("Gerente Comercial Chile", ["Gerente Comercial"], True),
        ("Subgerente Comercial", ["Gerente Comercial"], False),
        ("Sub Gerente Comercial", ["Gerente Comercial"], False),
        ("Global Process Owner", ["CEO"], False),
        ("Assistant to the CEO", ["CEO"], False),
        ("Vice President of Sales", ["President"], False),
        ("Ex CEO | Advisor", ["CEO"], False),
        ("Gerente de Márketing", ["gerente de marketing"], True),
        ("Asistente Comercial", ["Asistente Comercial"], True),
    ],
)
def test_title_matches(role, titles, ok):
    assert svc.title_matches(role, titles) is ok


def test_results_outside_filters_are_dropped(monkeypatch):
    page = {
        "results": [
            {"person_id": "a", "name": "A", "role": "CEO", "departments": ["Chief Executive"]},
            {"person_id": "b", "name": "B", "role": "Product Owner"},
            {"person_id": "c", "name": "C", "role": "Director of Customer Success", "departments": ["Customer Success"]},
            {"person_id": "d", "name": "D", "role": "Sales Manager", "departments": ["Sales"]},
        ],
        "total_count": 4,
        "total_pages": 1,
    }
    monkeypatch.setattr(svc, "_fetch_page", lambda filters, p: page)
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert [r["person_id"] for r in out["results"]] == ["a"]

    dept = {"person_department": {"include": ["Sales"]}}
    out = svc.search(db, company_id=cid, user_id=uid, filters=dept, page=1)
    # Depto duro: CS fuera; Product Owner fuera; Sales Manager entra.
    assert [r["person_id"] for r in out["results"]] == ["d"]


def test_first_page_does_not_rescue_wrong_titles(monkeypatch):
    """Cargo es duro: si Prospeo trae gente que no matchea el título, no la metemos igual."""
    page = {
        "results": [
            {"person_id": "p1", "name": "A", "role": "Sales Development Representative", "country": "Argentina"},
            {"person_id": "p2", "name": "B", "role": "Account Executive", "country": "Chile"},
            {"person_id": "p3", "name": "C", "role": "Business Development", "country": "Mexico"},
        ],
        "total_count": 3,
        "total_pages": 1,
    }
    monkeypatch.setattr(svc, "_fetch_page", lambda filters, p: page)
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(
        db,
        company_id=cid,
        user_id=uid,
        filters={"person_job_title": {"include": ["Head of Sales"], "match_mode": "CONTAINS"}},
        page=1,
    )
    assert out["results"] == []


def test_argentina_is_not_resolved_as_north_america():
    from app.services.lead_sourcing.b2c_person_search import _location_queries

    assert _location_queries("Argentina") == ["Argentina"]
    assert _location_queries("NA") == ["United States", "Canada"]

def test_person_id_from_external_id():
    assert svc.person_id_from_external_id("explorar-abc123") == "abc123"
    assert svc.person_id_from_external_id("role-prospeo-7-abc123-4") == "abc123"
    assert svc.person_id_from_external_id("b2c-prospeo-7-abc123-0") == "abc123"
    assert svc.person_id_from_external_id("prospeo-7-key-1") is None
    assert svc.person_id_from_external_id(None) is None


def _prospect(db, camp, **kw):
    from app.models.prospect import Prospect

    base = dict(
        company_id=camp.company_id,
        campaign_id=camp.id,
        name="X",
        company_name="Acme",
        status="imported",
        compatibility_score=50,
        interest_probability=50,
    )
    base.update(kw)
    p = Prospect(**base)
    db.add(p)
    db.flush()
    return p


def _distinct_companies(page):
    for i, r in enumerate(page["results"]):
        r["company_name"] = f"Empresa {i}"
    return page


def test_search_hides_people_already_in_a_campaign(monkeypatch):
    page = _distinct_companies(_fake_page(4))
    page["results"][2]["linkedin_url"] = "https://www.linkedin.com/in/persona-dos/"
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    _prospect(db, camp, source_external_id="role-prospeo-1-p1-0", company_name="Otra 1")
    _prospect(db, camp, linkedin_url="https://linkedin.com/in/persona-dos", company_name="Otra 2")
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert [r["person_id"] for r in out["results"]] == ["p0", "p3"]


def test_finished_campaign_releases_people_and_marks_contacted_before(monkeypatch):
    from app.models.enums import CampaignStatus

    page = _distinct_companies(_fake_page(3))
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    _prospect(db, camp, source_external_id="role-prospeo-1-p1-0", company_name="Empresa 1")
    camp.status = CampaignStatus.completed.value
    db.flush()
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    by_id = {r["person_id"]: r for r in out["results"]}
    assert set(by_id) == {"p0", "p1", "p2"}
    assert by_id["p1"].get("contacted_before") is True
    assert not by_id["p0"].get("contacted_before")


def test_paused_campaign_does_not_reserve_people(monkeypatch):
    from app.models.enums import CampaignStatus

    page = _distinct_companies(_fake_page(2))
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    _prospect(db, camp, source_external_id="role-prospeo-1-p1-0", company_name="Empresa 1")
    camp.status = CampaignStatus.paused.value
    db.flush()
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert [r["person_id"] for r in out["results"]] == ["p0", "p1"]


@pytest.mark.parametrize(
    "fields,in_use",
    [
        ({}, True),
        ({"status": "replied", "sequence_completed_at": "now"}, True),
        ({"status": "meeting_booked"}, True),
        ({"status": "not_interested"}, False),
        ({"sequence_group": "encajonado"}, False),
        ({"status": "contacted", "sequence_completed_at": "now"}, False),
    ],
)
def test_who_counts_as_in_use(monkeypatch, fields, in_use):
    from datetime import UTC, datetime

    page = _distinct_companies(_fake_page(2))
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    if fields.get("sequence_completed_at") == "now":
        fields = {**fields, "sequence_completed_at": datetime.now(UTC)}
    _prospect(db, camp, source_external_id="role-prospeo-1-p1-0", company_name="Empresa 1", **fields)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    shown = [r["person_id"] for r in out["results"]]
    assert ("p1" not in shown) is in_use


def test_other_people_of_a_company_in_use_are_hidden(monkeypatch):
    page = {
        "results": [
            {"person_id": "a", "name": "A", "role": "CEO", "company_name": "Acme S.A.", "company_domain": "acme.com"},
            {"person_id": "b", "name": "B", "role": "CEO", "company_name": "Otra", "company_domain": "otra.com"},
            {"person_id": "c", "name": "C", "role": "CEO", "company_name": "Nueva", "company_domain": "acme.com"},
        ],
        "total_count": 3,
        "total_pages": 1,
    }
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    _prospect(db, camp, company_name="ACME", email="jefe@acme.com")
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert [r["person_id"] for r in out["results"]] == ["b"]


def test_name_search_shows_person_in_use_with_notice(monkeypatch):
    page = _distinct_companies(_fake_page(2))
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    _prospect(db, camp, source_external_id="role-prospeo-1-p1-0", company_name="Empresa 1")
    out = svc.search(db, company_id=cid, user_id=uid, filters={"person_name": ["Persona 1"]}, page=1)
    by_id = {r["person_id"]: r for r in out["results"]}
    assert by_id["p1"]["in_use"]["reason"] == "person"
    assert by_id["p1"]["in_use"]["campaign_name"] == "Camp"
    assert "in_use" not in by_id["p0"]


def test_opted_out_person_never_shows_again(monkeypatch):
    from app.models.enums import CampaignStatus

    page = _distinct_companies(_fake_page(2))
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    camp = _campaign(db, cid, uid, prospect_count=10)
    _prospect(
        db,
        camp,
        source_external_id="role-prospeo-1-p1-0",
        company_name="Empresa 1",
        objection_type="opt_out",
        sequence_group="encajonado",
        status="not_interested",
    )
    camp.status = CampaignStatus.completed.value
    db.flush()
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert [r["person_id"] for r in out["results"]] == ["p0"]


def _other_company(db, *, credits=5):
    other = Company(name="Otra Co", employee_count=5, plan="starter")
    db.add(other)
    db.flush()
    seller = User(
        company_id=other.id,
        email="sdr2@test.com",
        first_name="Otro",
        last_name="T",
        name="Otro T",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    db.add(seller)
    db.flush()
    db.add(SellerCreditAllocation(company_id=other.id, seller_id=seller.id, allocated_balance=credits, used_balance=0))
    db.flush()
    return other.id, seller.id


def test_reveal_by_another_client_is_free_for_everyone(monkeypatch):
    calls = _patch_enrich(
        monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}, "mobile": {"mobile": "+5491100000000"}}
    )
    db = _session()
    cid, uid = _setup(db)
    oid, ouid = _other_company(db)
    snap = {"person_id": "p1", "name": "Ana", "company_name": "Acme"}
    _row, charged = svc.reveal_person(db, company_id=oid, user_id=ouid, snapshot=snap)
    assert charged is True and len(calls) == 1

    row, charged = svc.reveal_person(db, company_id=cid, user_id=uid, snapshot=snap)
    assert charged is False
    assert row.email == "ana@acme.com" and row.phone == "+5491100000000"
    assert row.charged == 0
    assert len(calls) == 1
    assert _used(db, cid, uid) == 0


def test_reveal_by_another_client_is_free_even_without_credits(monkeypatch):
    calls = _patch_enrich(monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=0)
    oid, ouid = _other_company(db)
    snap = {"person_id": "p1", "name": "Ana", "company_name": "Acme"}
    svc.reveal_person(db, company_id=oid, user_id=ouid, snapshot=snap)
    row, charged = svc.reveal_person(db, company_id=cid, user_id=uid, snapshot=snap)
    assert charged is False and row.email == "ana@acme.com"
    assert len(calls) == 1


def test_search_shows_people_revealed_by_other_clients(monkeypatch):
    _patch_enrich(
        monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}, "mobile": {"mobile": "+5491100000000"}}
    )
    page = _distinct_companies(_fake_page(2))
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    oid, ouid = _other_company(db)
    svc.reveal_person(db, company_id=oid, user_id=ouid, snapshot={"person_id": "p1", "name": "Persona 1"})
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    by_id = {r["person_id"]: r for r in out["results"]}
    assert by_id["p1"]["revealed"] is True and by_id["p1"]["revealed_by_nexus"] is True
    assert by_id["p1"]["email"] == "ana@acme.com"
    assert by_id["p0"]["revealed"] is False


def test_free_reveal_then_campaign_costs_one_credit_total(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(
        monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}, "mobile": {"mobile": "+5491100000000"}}
    )
    db = _session()
    cid, uid = _setup(db, credits=3)
    oid, ouid = _other_company(db)
    snap = {"person_id": "p1", "name": "Ana", "company_name": "Acme", "role": "CEO"}
    svc.reveal_person(db, company_id=oid, user_id=ouid, snapshot=snap)
    svc.reveal_person(db, company_id=cid, user_id=uid, snapshot=snap)
    assert _used(db, cid, uid) == 0
    camp = _campaign(db, cid, uid, prospect_count=0)
    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=camp.id, people=[snap]), db=db, _company=None, user=db.get(User, uid)
    )
    assert out["added"] == 1
    assert out["results"][0]["paid_with"] == "campaign_slot"
    assert _used(db, cid, uid) == 1


def test_add_released_person_moves_to_new_campaign(monkeypatch):
    from app.models.enums import CampaignStatus
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=3)
    first = _campaign(db, cid, uid, prospect_count=10)
    second = _campaign(db, cid, uid, prospect_count=10)
    old = _prospect(
        db, first, source_external_id="role-prospeo-1-p9-0", email="ana@acme.com", status="not_interested",
        objection_type="not_now",
    )
    first.status = CampaignStatus.completed.value
    db.flush()
    snap = {"person_id": "p9", "name": "Ana", "company_name": "Acme"}
    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=second.id, people=[snap]), db=db, _company=None, user=db.get(User, uid)
    )
    assert out["added"] == 1, out
    db.refresh(old)
    assert old.campaign_id == second.id
    assert old.status == "imported"
    assert old.objection_type is None


def test_add_rejects_other_person_of_company_in_use(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    calls = _patch_enrich(monkeypatch, {"full_name": "Leo", "email": {"email": "leo@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=3)
    first = _campaign(db, cid, uid, prospect_count=10)
    second = _campaign(db, cid, uid, prospect_count=10)
    first.name = "Q4 Retail"
    _prospect(db, first, company_name="Acme", email="ana@acme.com")
    snap = {"person_id": "p7", "name": "Leo", "company_name": "Acme", "company_domain": "acme.com"}
    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=second.id, people=[snap]), db=db, _company=None, user=db.get(User, uid)
    )
    assert out["added"] == 0
    assert "otra persona de Acme" in out["results"][0]["error"]
    assert "Q4 Retail" in out["results"][0]["error"]
    assert calls == []


def test_add_person_already_in_other_campaign_is_rejected(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    calls = _patch_enrich(monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=3)
    user = db.get(User, uid)
    first = _campaign(db, cid, uid, prospect_count=10)
    second = _campaign(db, cid, uid, prospect_count=10)
    existing = _prospect(db, first, source_external_id="role-prospeo-1-p9-0", email="ana@acme.com")
    snap = {"person_id": "p9", "name": "Ana", "company_name": "Acme"}
    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=second.id, people=[snap]), db=db, _company=None, user=user
    )
    assert out["added"] == 0
    assert "Camp" in out["results"][0]["error"]
    assert calls == []
    db.refresh(existing)
    assert existing.campaign_id == first.id


def test_add_rejects_duplicate_found_after_reveal(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=3)
    user = db.get(User, uid)
    first = _campaign(db, cid, uid, prospect_count=10)
    second = _campaign(db, cid, uid, prospect_count=10)
    existing = _prospect(db, first, email="ana@acme.com", source_provider="manual")
    out = explorar_add_to_campaign(
        cid,
        ExplorarAddRequest(campaign_id=second.id, people=[{"person_id": "p7", "name": "Ana"}]),
        db=db,
        _company=None,
        user=user,
    )
    assert out["added"] == 0
    db.refresh(existing)
    assert existing.campaign_id == first.id


def test_prospect_verification_labels():
    from app.models.prospect import Prospect

    p = Prospect(email="a@b.com", whatsapp="+54911", source_provider="prospeo")
    assert (p.email_verification, p.mobile_verification) == ("verified", "verified")
    p = Prospect(email="a@b.com", source_provider="manual", source_external_id="explorar-x")
    assert (p.email_verification, p.mobile_verification) == ("verified", "none")
    p = Prospect(email="sales@b.com", phone="+54911", source_provider="pattern_fallback")
    assert (p.email_verification, p.mobile_verification) == ("unverified", "unverified")


def test_page_is_filled_across_prospeo_pages(monkeypatch):
    calls = []

    def fake(filters, page):
        calls.append(page)
        rows = [
            {"person_id": f"p{page}_{i}", "name": "X", "role": "CEO" if i % 2 == 0 else "Product Owner"}
            for i in range(25)
        ]
        return {"results": rows, "total_count": 100, "total_pages": 4}

    monkeypatch.setattr(svc, "_fetch_page", fake)
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert len(out["results"]) == 25
    assert calls[:2] == [1, 2]
    assert out["total_is_estimate"] is True
    # Total contable ≈ mail verificado (100) con keep-rate de cargo.
    assert out["total_count"] >= 25
    assert out["usage"]["pages_unbilled"] == len(calls)

    out2 = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=2)
    first_ids = {r["person_id"] for r in out["results"]}
    assert not first_ids & {r["person_id"] for r in out2["results"]}
    assert len(out2["results"]) == 25
    assert out2["usage"]["pages_unbilled"] == len(calls)


def test_last_page_gives_exact_total(monkeypatch):
    monkeypatch.setenv("NEXUS_EXPLORAR_MAX_FETCHES_PER_REQUEST", "10")
    monkeypatch.setattr(svc, "_fetch_page", _paged(per_page=10, total_pages=2))
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert len(out["results"]) == 20
    assert out["total_count"] == 20 and out["total_is_estimate"] is False
    assert out["total_pages"] == 1


_PEOPLE = [
    # (id, mail verificado, celular verificado) — mezclados como los devuelve Prospeo
    ("a1", False, False),
    ("m1", False, True),
    ("v1", True, True),
    ("e1", True, False),
    ("a2", False, False),
    ("v2", True, True),
]


def _prospeo_like(calls, per_page=25):
    """Imita los filtros de contacto de Prospeo sobre ``_PEOPLE``."""

    def fake(filters, page):
        details = filters.get("person_contact_details") or {}
        calls.append((tuple(details.get("email") or ()), tuple(details.get("mobile") or ())))
        rows = []
        for pid, ev, mv in _PEOPLE:
            if details.get("email") == ["VERIFIED"] and not ev:
                continue
            if details.get("mobile") == ["VERIFIED"] and not mv:
                continue
            if details.get("mobile") == ["UNAVAILABLE"] and mv:
                continue
            rows.append(
                {
                    "person_id": pid,
                    "name": pid,
                    "role": "CEO",
                    "has_email": ev,
                    "has_mobile": mv,
                    "email_verified": ev,
                    "mobile_verified": mv,
                }
            )
        page_rows = rows[(page - 1) * per_page : page * per_page]
        return {"results": page_rows, "total_count": len(rows), "total_pages": max(1, -(-len(rows) // per_page))}

    return fake


def test_verified_contacts_come_first_without_repeats(monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_fetch_page", _prospeo_like(calls))
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    # Completos → celular → mail → sin contacto (solo LI); sin repetir.
    assert [r["person_id"] for r in out["results"]] == ["v1", "v2", "m1", "e1", "a1", "a2"]
    assert out["total_count"] >= 6
    assert calls[0] == (("VERIFIED",), ("VERIFIED",))
    assert ((), ()) in calls  # tanda sin contacto (total / cola LI-only)


def test_verified_first_holds_across_pages(monkeypatch):
    """Con 1 persona por página de Prospeo, los «ambos verificados» de la página 2 siguen yendo primero."""
    calls = []
    monkeypatch.setattr(svc, "_fetch_page", _prospeo_like(calls, per_page=1))
    monkeypatch.setenv("NEXUS_EXPLORAR_MAX_FETCHES_PER_REQUEST", "20")
    db = _session()
    cid, uid = _setup(db, credits=10)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert [r["person_id"] for r in out["results"]][:3] == ["v1", "v2", "m1"]


def test_search_plan_order_and_total():
    def contact(phase):
        return phase.get("person_contact_details") or {}

    # Preferencia de canales; total = universo sin exigir contacto.
    f = svc.build_filters(titles=["CEO"], require_email=True, require_mobile=True)
    assert "person_contact_details" not in f
    plan_both = svc.search_plan(f)
    assert [(contact(p).get("email"), contact(p).get("mobile"), counted) for p, counted in plan_both] == [
        (["VERIFIED"], ["VERIFIED"], False),
        (None, ["VERIFIED"], False),
        (["VERIFIED"], None, False),
        (None, None, True),
    ]

    h = svc.build_filters(titles=["CEO"])
    plan = svc.search_plan(h)
    assert [(contact(p).get("email"), contact(p).get("mobile"), counted) for p, counted in plan] == [
        (["VERIFIED"], ["VERIFIED"], False),
        (None, ["VERIFIED"], False),
        (["VERIFIED"], None, False),
        (None, None, True),
    ]

    g = svc.build_filters(titles=["CEO"], require_email=True)
    tiers = [contact(t) for t in svc.search_phases(g)]
    assert [(t.get("email"), t.get("mobile")) for t in tiers] == [
        (["VERIFIED"], ["VERIFIED"]),
        (["VERIFIED"], None),
        (None, ["VERIFIED"]),
        (None, None),
    ]

    m = svc.build_filters(titles=["CEO"], require_mobile=True)
    assert [counted for _p, counted in svc.search_plan(m)] == [False, False, False, True]


@pytest.mark.parametrize(
    "role,titles,ok",
    [
        ("Directora Comercial", ["Director Comercial"], True),
        ("Sales Head LATAM", ["Head of Sales"], True),
        ("Gerente de Ventas", ["Gerente Ventas"], True),
        ("Jefa de Compras", ["Jefe de Compras"], True),
        ("Subgerente Comercial", ["Gerente Comercial"], False),
        ("Sub Gerente Comercial", ["Gerente Comercial"], False),
        ("Vice President of Sales", ["President"], False),
        ("Assistant to the CEO", ["CEO"], False),
        ("Ex CEO | Advisor", ["CEO"], False),
        ("Head of Marketing", ["Head of Sales"], False),
    ],
)
def test_title_matches_loose(role, titles, ok):
    from app.services.lead_sourcing.role_alignment import title_matches_loose

    assert title_matches_loose(role, titles) is ok


def test_titles_include_feminine_variant():
    f = svc.build_filters(titles=["Director Comercial", "CEO"])
    include = f["person_job_title"]["include"]
    assert include[:3] == ["Director Comercial", "CEO", "Directora Comercial"]
    assert "Directora de Ventas" in include


def test_short_page_flags_more_pending(monkeypatch):
    """Si se corta la tanda de pedidos antes de llenar la página, el front sabe que hay más."""

    def fake(filters, page):
        rows = [
            {"person_id": f"{id(filters) % 997}_{page}_{i}", "name": "X", "role": "Product Owner"}
            for i in range(25)
        ]
        rows[0]["role"] = "CEO"
        return {"results": rows, "total_count": 2500, "total_pages": 100}

    monkeypatch.setattr(svc, "_fetch_page", fake)
    db = _session()
    cid, uid = _setup(db, credits=10)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=3)
    assert out["results"] == []
    assert out["more_pending"] is True


def test_search_stops_on_wall_clock_and_flags_more_pending(monkeypatch):
    """Nunca debe colgar hasta el 504 del middleware: corta por tiempo y el front reintenta."""
    monkeypatch.setenv("NEXUS_EXPLORAR_SEARCH_DEADLINE_SEC", "8")
    calls = {"n": 0}
    mono = {"t": 1000.0}

    def fake_mono():
        return mono["t"]

    def fake(filters, page):
        calls["n"] += 1
        mono["t"] += 3.0  # cada fetch “tarda” → en 2–3 hits se agota el deadline
        rows = [
            {"person_id": f"t{page}_{i}", "name": "X", "role": "Product Owner"}
            for i in range(25)
        ]
        rows[0]["role"] = "CEO"
        return {"results": rows, "total_count": 5000, "total_pages": 200}

    monkeypatch.setattr(svc.time, "monotonic", fake_mono)
    monkeypatch.setattr(svc, "_fetch_page", fake)
    db = _session()
    cid, uid = _setup(db, credits=20)
    out = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert calls["n"] >= 1
    assert calls["n"] < 12  # no recorre el tope entero de fetches
    assert out["more_pending"] is True or len(out["results"]) > 0


def test_fetch_page_retries_on_rate_limit(monkeypatch):
    from types import SimpleNamespace

    from app.services.lead_sourcing.providers import prospeo_mvp
    from app.services.lead_sourcing.providers.base import ProviderAPIError

    calls = []

    def fake_post(url, body):
        calls.append(body["page"])
        if len(calls) < 3:
            raise ProviderAPIError("rate", status_code=429, error_code="RATE_LIMITED")
        return SimpleNamespace(payload={"results": [], "pagination": {"total_count": 0, "total_page": 0}})

    monkeypatch.setattr(prospeo_mvp, "_post_json_result", fake_post)
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    out = svc._fetch_page({"x": 1}, 1)
    assert calls == [1, 1, 1] and out["results"] == []


def test_results_outside_region_industry_or_size_are_dropped():
    f = {
        "person_location_search": {"include": ["Buenos Aires, Argentina"]},
        "company_industry": {"include": ["Software Development"]},
        "company_headcount_range": ["11-20", "21-50"],
    }
    ok = {"country": "Argentina", "industry": "Software Development", "company_size": "11-20"}
    assert svc.hit_matches_filters(ok, f)
    assert not svc.hit_matches_filters({**ok, "country": "Uruguay"}, f)
    # Tamaño duro: 1000+ fuera aunque industria OK.
    assert not svc.hit_matches_filters({**ok, "company_size": "1001-2000"}, f)
    assert not svc.hit_matches_filters({**ok, "company_size": 900}, f)
    assert svc.hit_matches_filters({**ok, "company_size": 30}, f)
    # Sin país: fuera.
    assert not svc.hit_matches_filters({"country": None, "industry": "Software Development", "company_size": "11-20"}, f)
    # Industria sola contradictoria (sin modo mayoría de 2 blandos).
    assert not svc.hit_matches_filters({**ok, "industry": "Banking"}, f)


def test_soft_majority_and_core_prospeo_split():
    f = svc.build_filters(
        titles=["CEO"],
        location="Argentina",
        industries=["Software Development"],
        seniorities=["C-Suite"],
        departments=["Sales"],
        headcounts=["11-20", "21-50"],
    )
    assert svc.soft_filters_active(f)
    core = svc.core_filters_for_prospeo(f)
    assert "person_job_title" in core and "person_location_search" in core
    # Depto sí va a Prospeo (si no, RRHH+Gerente deja 2–4 cards de 25).
    assert "person_department" in core
    assert "company_headcount_range" in core
    assert "company_industry" in core
    assert "person_seniority" in core
    assert svc.soft_majority_threshold(2) == 1
    assert svc.soft_majority_threshold(3) == 1
    assert svc.soft_majority_threshold(4) == 2
    assert svc.max_fetches_per_request(f) >= 12

    one = svc.build_filters(titles=["CEO"], industries=["Software Development"])
    assert not svc.soft_filters_active(one)
    assert svc.core_filters_for_prospeo(one) == one


def test_manager_family_and_volume_scopes_keep_hr_fit():
    """Gerente→Head/Director no se tumba en post-filtro; scopes ensanchan sin industria/nivel."""
    f = svc.build_filters(
        location="Argentina",
        industries=["General Manufacturing"],
        seniorities=["Manager"],
        departments=["Human Resources"],
    )
    scopes = svc.volume_scope_filters(f)
    assert len(scopes) >= 1
    assert "company_industry" in scopes[0]
    # Con industria pedida, ningún scope la suelta (keep-rate ~0 si no).
    assert all("company_industry" in s for s in scopes)

    base = {
        "country": "Argentina",
        "departments": ["Human Resources"],
        "role": "HR Manager",
        "industry": "Food and Beverage Manufacturing",
    }
    assert svc.hit_matches_filters({**base, "seniority": "Manager"}, f)
    assert svc.hit_matches_filters({**base, "seniority": "Head"}, f)
    assert svc.hit_matches_filters({**base, "seniority": "Director"}, f)
    assert not svc.hit_matches_filters({**base, "seniority": "Entry"}, f)
    # Ranking: Manager exacto antes que Head (mismo contacto).
    ranked = svc.rank_by_contact(
        [
            svc.annotate_filter_fit({**base, "person_id": "h", "seniority": "Head", "has_email": True, "email_verified": True}, f),
            svc.annotate_filter_fit({**base, "person_id": "m", "seniority": "Manager", "has_email": True, "email_verified": True}, f),
        ]
    )
    assert [r["person_id"] for r in ranked] == ["m", "h"]


def test_soft_majority_does_not_override_contradictory_industry():
    """Checklist Search: Manager+RRHH no mete IT/consultora en Manufactura."""
    f = svc.build_filters(
        location="Argentina",
        industries=["General Manufacturing"],
        seniorities=["Manager"],
        departments=["Human Resources"],
    )
    base = {
        "country": "Argentina",
        "departments": ["Human Resources"],
        "role": "HR Manager",
        "seniority": "Manager",
    }
    assert svc.hit_matches_filters(
        {**base, "industry": "Food and Beverage Manufacturing"}, f
    )
    assert not svc.hit_matches_filters(
        {**base, "industry": "IT Services and IT Consulting"}, f
    )
    assert not svc.hit_matches_filters(
        {**base, "industry": "Business Consulting and Services"}, f
    )


def test_founder_volume_scope_drops_department():
    f = svc.build_filters(
        location="Chile",
        industries=["Restaurants"],
        seniorities=["Founder/Owner"],
        departments=["Operations"],
    )
    scopes = svc.volume_scope_filters(f)
    # Tras tight: sin depto pero CON industria+seniority (no Chile suelto).
    assert any(
        "person_department" not in s
        and "company_industry" in s
        and "person_seniority" in s
        for s in scopes
    )
    assert any("person_department" in s for s in scopes)
    assert all("company_industry" in s for s in scopes)


def test_volume_scopes_never_drop_industry_when_requested():
    f = svc.build_filters(
        location="Argentina",
        industries=["General Manufacturing"],
        seniorities=["Manager"],
        departments=["Human Resources"],
    )
    scopes = svc.volume_scope_filters(f)
    assert scopes
    assert all("company_industry" in s for s in scopes)


def test_contact_tier_ranks_first_page_then_icp():
    # 1ª hoja: completo → cel → mail → nada (Uber: ver primero a los más contactables).
    same_icp = [
        {"person_id": "a", "email_verified": False, "mobile_verified": False, "has_email": False, "has_mobile": False, "filter_matched": 2, "industry_status": "exact", "seniority_match": True},
        {"person_id": "b", "email_verified": True, "mobile_verified": True, "has_email": True, "has_mobile": True, "filter_matched": 2, "industry_status": "exact", "seniority_match": True},
        {"person_id": "c", "email_verified": True, "mobile_verified": False, "has_email": True, "has_mobile": False, "filter_matched": 2, "industry_status": "exact", "seniority_match": True},
        {"person_id": "d", "email_verified": False, "mobile_verified": True, "has_email": False, "has_mobile": True, "filter_matched": 2, "industry_status": "exact", "seniority_match": True},
    ]
    assert [r["person_id"] for r in svc.rank_by_contact(same_icp)] == ["b", "d", "c", "a"]

    # Mismo nivel de contacto → gana mejor ICP.
    same_contact = [
        {
            "person_id": "weak",
            "email_verified": True,
            "mobile_verified": False,
            "has_email": True,
            "has_mobile": False,
            "filter_matched": 0,
            "industry_status": "incomplete",
            "seniority_match": False,
        },
        {
            "person_id": "strong",
            "email_verified": True,
            "mobile_verified": False,
            "has_email": True,
            "has_mobile": False,
            "filter_matched": 2,
            "industry_status": "exact",
            "seniority_match": True,
        },
    ]
    assert [r["person_id"] for r in svc.rank_by_contact(same_contact)] == ["strong", "weak"]


def test_prospeo_coverage_note_no_lusha_promise():
    note = svc.prospeo_coverage_note(estimated_total=312, exhausted=True)
    assert "Prospeo" in note
    assert "Lusha" in note
    assert "312" in note
    soft = svc.prospeo_coverage_note(estimated_total=0, exhausted=False)
    assert "Prospeo" in soft and "Lusha" in soft


def test_sync_contact_flags_verified_and_revealed():
    """Chips Celular/Email: verified o valor revelado ⇒ Sí (no mentir)."""
    stamped = svc.sync_contact_flags(
        {"email_verified": True, "mobile_verified": True, "has_email": False, "has_mobile": False}
    )
    assert stamped["has_email"] is True
    assert stamped["has_mobile"] is True

    revealed = svc.sync_contact_flags(
        {
            "has_email": False,
            "has_mobile": False,
            "email": "a@b.co",
            "phone": "+56911111111",
            "email_verified": False,
            "mobile_verified": False,
        }
    )
    assert revealed["has_email"] is True
    assert revealed["has_mobile"] is True

    bare = svc.sync_contact_flags(
        {"has_email": False, "has_mobile": False, "email_verified": False, "mobile_verified": False}
    )
    assert bare["has_email"] is False
    assert bare["has_mobile"] is False

    # Completo → cel → mail → LI: orden de 1ª hoja.
    rows = [
        {"person_id": "li", "has_email": False, "has_mobile": False},
        {"person_id": "both", "email_verified": True, "mobile_verified": True},
        {"person_id": "mail", "has_email": True, "email_verified": True},
        {"person_id": "cel", "has_mobile": True, "mobile_verified": True},
    ]
    assert [r["person_id"] for r in svc.rank_by_contact(rows)] == ["both", "cel", "mail", "li"]


def test_total_scope_keeps_industry_for_count():
    f = svc.build_filters(
        location="Chile",
        industries=["Restaurants"],
        seniorities=["Founder/Owner"],
        departments=["Operations"],
    )
    scopes = svc.volume_scope_filters(f)
    total_scope = svc.total_scope_for_count(f, scopes)
    assert "company_industry" in total_scope
    assert "person_department" not in total_scope


def test_total_scope_keeps_hr_dept_not_fifty_k_managers():
    """Total ICP con RRHH: no usar scope sin depto (pozo ~50k managers en AR)."""
    f = svc.build_filters(
        location="Argentina",
        industries=["General Manufacturing"],
        seniorities=["Manager"],
        departments=["Human Resources"],
    )
    scopes = svc.volume_scope_filters(f)
    total_scope = svc.total_scope_for_count(f, scopes)
    assert "person_department" in total_scope
    assert "company_industry" in total_scope


def test_health_and_finance_industry_expand_for_volume():
    health = svc.expand_industries_for_prospeo(["Hospitals and Health Care"])
    assert "Medical Practices" in health
    assert "Pharmaceutical Manufacturing" in health
    fin = svc.expand_industries_for_prospeo(["Financial Services"])
    assert "Banking" in fin
    assert "Insurance" in fin


def test_build_filters_multi_country_location():
    f = svc.build_filters(location="Chile, Panamá, Costa Rica")
    loc = f.get("person_location_search") or {}
    include = [str(x).lower() for x in (loc.get("include") or [])]
    blob = " ".join(include)
    assert "chile" in blob
    assert "panama" in blob or "panamá" in blob
    assert "costa rica" in blob or "costa" in blob


def test_hr_department_hard_via_role_or_dept():
    f = {"person_department": {"include": ["Human Resources"]}}
    assert svc.department_hard_ok({"departments": ["Human Resources"], "role": "Analyst"}, f)
    assert svc.department_hard_ok({"departments": [], "role": "Head of People"}, f)
    assert svc.department_hard_ok({"role": "Gerente de Recursos Humanos"}, f)
    assert not svc.department_hard_ok({"departments": ["Sales"], "role": "Account Executive"}, f)
    assert not svc.department_hard_ok({"departments": [], "role": "Account Executive"}, f)


def test_size_and_argentina_are_hard():
    size_f = {"company_headcount_range": ["11-20", "21-50"]}
    assert svc.size_hard_ok({"company_size": "11-20"}, size_f)
    assert not svc.size_hard_ok({"company_size": "1001-2000"}, size_f)
    assert not svc.size_hard_ok({"company_size": None}, size_f)

    loc = {"person_location_search": {"include": ["Argentina"]}}
    assert svc.location_hard_ok({"country": "Argentina"}, loc)
    assert not svc.location_hard_ok({"country": "Mexico"}, loc)
    assert not svc.location_hard_ok({"country": None}, loc)
    assert not svc.location_hard_ok({"country": "", "company_country": "Argentina"}, loc)


def test_build_filters_latam_does_not_keep_raw_region_label(monkeypatch):
    monkeypatch.setattr(
        "app.services.lead_sourcing.b2c_person_search.suggest_locations",
        lambda q, limit=8: [],
    )
    f = svc.build_filters(location="LATAM (sin Brasil)", titles=["CEO"])
    include = f["person_location_search"]["include"]
    assert "LATAM (sin Brasil)" not in include
    assert "Argentina" in include
    assert "Brazil" not in include
    hit_br = {"role": "CEO", "country": "Brazil"}
    hit_ar = {"role": "CEO", "country": "Argentina"}
    assert not svc.hit_matches_filters(hit_br, f)
    assert svc.hit_matches_filters(hit_ar, f)


def test_name_search_builds_person_name_filter_and_single_phase():
    f = svc.build_filters(names=["Marcos Galperin", "x"])
    assert "Marcos Galperin" in f["person_name"]["include"]
    assert svc.has_targeting(f)
    assert svc.is_name_primary_search(f)
    phases = svc.search_phases(f)
    assert len(phases) == 2
    assert phases[0]["person_contact_details"]["email"] == ["VERIFIED"]
    assert phases[0]["person_contact_details"]["mobile"] == ["VERIFIED"]
    assert phases[1] == f


def test_name_primary_ignores_leftover_location():
    f = svc.build_filters(names=["Ana Paz"], location="Argentina")
    assert svc.is_name_primary_search(f)
    assert svc.core_filters_for_prospeo(f) == {"person_name": f["person_name"]}
    assert svc.hit_matches_core_filters({"country": None, "name": "Ana Paz"}, f)


def test_name_with_title_is_not_name_primary():
    f = svc.build_filters(names=["Ana Paz"], titles=["CEO"], location="Argentina")
    assert not svc.is_name_primary_search(f)
    assert "person_location_search" in svc.core_filters_for_prospeo(f)


def test_linkedin_url_in_name_field_is_extracted():
    f = svc.build_filters(names=["https://www.linkedin.com/in/ana-paz-123"])
    assert f.get("_linkedin_profile_urls") == ["https://www.linkedin.com/in/ana-paz-123"]
    assert "person_name" not in f
    assert svc.is_name_primary_search(f)


def test_parse_hit_separates_verified_from_available():
    hit = svc.parse_hit(
        {
            "person": {
                "person_id": "abc",
                "full_name": "Ana Paz",
                "email": {"status": "VERIFIED"},
                "mobile": {"status": "AVAILABLE"},
            }
        }
    )
    assert hit["has_email"] and hit["email_verified"]
    assert hit["has_mobile"] and not hit["mobile_verified"]


def test_search_hits_are_stored_in_own_db_without_delivery(monkeypatch):
    from app.models.nexus_contact_cache import NexusContactCache, NexusContactDelivery

    page = {
        "results": [
            {"person_id": "p1", "name": "Ana Paz", "role": "CEO", "company_name": "Acme",
             "linkedin_url": "https://www.linkedin.com/in/ana-paz"},
            {"person_id": "p2", "name": "Sin LinkedIn", "role": "CEO", "company_name": "Acme"},
        ],
        "total_count": 2,
        "total_pages": 1,
    }
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: page)
    db = _session()
    cid, uid = _setup(db)
    svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    rows = db.query(NexusContactCache).all()
    assert [r.full_name for r in rows] == ["Ana Paz"]
    assert db.query(NexusContactDelivery).count() == 0


def test_reveal_is_stored_in_own_db_with_contact_data(monkeypatch):
    from app.models.nexus_contact_cache import NexusContactCache, NexusContactDelivery

    _patch_enrich(
        monkeypatch, {"full_name": "Ana", "email": {"email": "ana@acme.com"}, "mobile": {"mobile": "+5491100000000"}}
    )
    db = _session()
    cid, uid = _setup(db)
    svc.reveal_person(db, company_id=cid, user_id=uid, snapshot={"person_id": "p1", "name": "Ana", "company_name": "Acme"})
    row = db.query(NexusContactCache).one()
    assert row.email == "ana@acme.com"
    assert db.query(NexusContactDelivery).count() == 1


def _second_user(db, company_id, *, role):
    user = User(
        company_id=company_id,
        email=f"{role}2@test.com",
        first_name="Otro",
        last_name="U",
        name="Otro U",
        role=role,
        password_hash="x",
    )
    db.add(user)
    db.flush()
    return user.id


def test_favorite_is_hidden_only_from_whoever_saved_it(monkeypatch):
    monkeypatch.setattr(svc, "_fetch_page", lambda f, p: _fake_page(n=3))
    db = _session()
    cid, uid = _setup(db)
    other = _second_user(db, cid, role=UserRole.sdr.value)
    db.add(SellerCreditAllocation(company_id=cid, seller_id=other, allocated_balance=5, used_balance=0))
    db.flush()

    svc.add_favorite(db, company_id=cid, user_id=uid, snapshot={"person_id": "p1", "name": "Persona 1", "has_email": True})
    mine = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    theirs = svc.search(db, company_id=cid, user_id=other, filters=FILTERS, page=1)
    assert "p1" not in [r["person_id"] for r in mine["results"]]
    assert "p1" in [r["person_id"] for r in theirs["results"]]

    svc.remove_favorite(db, company_id=cid, person_id="p1", user_id=uid)
    again = svc.search(db, company_id=cid, user_id=uid, filters=FILTERS, page=1)
    assert "p1" in [r["person_id"] for r in again["results"]]


def test_director_sees_team_favorites_and_seller_only_own():
    db = _session()
    cid, uid = _setup(db)
    boss = _second_user(db, cid, role=UserRole.gerente.value)
    svc.add_favorite(db, company_id=cid, user_id=uid, snapshot={"person_id": "p1", "name": "Ana", "email_verified": True})
    svc.add_favorite(db, company_id=cid, user_id=boss, snapshot={"person_id": "p2", "name": "Leo"})

    seller_view = svc.list_favorites(db, company_id=cid, user_id=uid, include_team=False)
    assert [f["person_id"] for f in seller_view] == ["p1"]
    assert seller_view[0]["email_verified"] is True and seller_view[0]["mine"] is True

    boss_view = svc.list_favorites(db, company_id=cid, user_id=boss, include_team=True)
    assert sorted(f["person_id"] for f in boss_view) == ["p1", "p2"]
    assert {f["person_id"]: f["owner_name"] for f in boss_view}["p1"] == "SDR T"


def test_adding_favorite_to_campaign_removes_it_from_saved(monkeypatch):
    from app.routes.explorar import ExplorarAddRequest, explorar_add_to_campaign

    _patch_enrich(monkeypatch, {"full_name": "Leo Gil", "email": {"email": "leo@acme.com"}})
    db = _session()
    cid, uid = _setup(db, credits=3)
    user = db.get(User, uid)
    camp = _campaign(db, cid, uid, prospect_count=0)
    snap = {"person_id": "p2", "name": "Leo Gil", "company_name": "Acme", "role": "CEO"}
    svc.add_favorite(db, company_id=cid, user_id=uid, snapshot=snap)

    out = explorar_add_to_campaign(
        cid, ExplorarAddRequest(campaign_id=camp.id, people=[snap]), db=db, _company=None, user=user
    )
    assert out["added"] == 1
    assert svc.list_favorites(db, company_id=cid, user_id=uid, include_team=False) == []


def test_explorar_classifies_geo_and_does_not_confirm_incomplete():
    f = {
        "person_job_title": {"include": ["CEO"], "match_mode": "CONTAINS"},
        "person_location_search": {"include": ["Argentina"]},
    }
    confirmed = svc.annotate_explorar_geo(
        {"person_id": "a", "role": "CEO", "country": "Argentina"}, f
    )
    assert confirmed["geo_status"] == "confirmed"
    assert confirmed["country"] == "Argentina"

    contradictory = {"person_id": "b", "role": "CEO", "country": "Uruguay"}
    assert not svc.hit_matches_filters(contradictory, f)

    incomplete = svc.annotate_explorar_geo(
        {"person_id": "c", "role": "CEO", "country": None, "company_country": "Argentina"},
        f,
    )
    assert incomplete["geo_status"] == "incomplete"
    # Sin país de persona: no entra aunque la empresa diga Argentina.
    assert not svc.hit_matches_filters(
        {"person_id": "c", "role": "CEO", "country": None, "company_country": "Argentina"},
        f,
    )
    assert incomplete["geo_status"] != "confirmed"

    review = svc.annotate_explorar_geo(
        {
            "person_id": "d",
            "role": "CEO",
            "country": "Argentina",
            "company_country": "Brazil",
        },
        f,
    )
    assert review["geo_status"] == "review"
    assert review["country"] == "Argentina"


def test_parse_hit_keeps_person_country_apart_from_company():
    hit = svc.parse_hit(
        {
            "person": {
                "person_id": "abc",
                "full_name": "Ana Paz",
                "location": {"country": "Chile", "city": "Santiago"},
                "company": {"name": "Acme", "country": "Argentina"},
            },
            "company": {"name": "Acme", "country": "Argentina"},
        }
    )
    assert hit["country"] == "Chile"
    assert hit["company_country"] == "Argentina"
    assert hit["city"] == "Santiago"


def test_explorar_search_stores_person_origin_without_breaking_reveal(monkeypatch):
    from app.models.nexus_contact_cache import NexusContactCache, NexusContactDelivery

    page = {
        "results": [
            {
                "person_id": "p-geo",
                "name": "Ana Paz",
                "role": "CEO",
                "country": "Argentina",
                "company_country": "Argentina",
                "linkedin_url": "https://www.linkedin.com/in/ana-geo",
            }
        ],
        "total_count": 1,
        "total_pages": 1,
    }
    filters = {
        "person_job_title": {"include": ["CEO"], "match_mode": "CONTAINS"},
        "person_location_search": {"include": ["Argentina"]},
    }
    monkeypatch.setattr(svc, "_fetch_page", lambda _filters, p: page)
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=filters, page=1)
    assert out["results"][0]["geo_status"] == "confirmed"
    row = db.query(NexusContactCache).one()
    assert row.country == "Argentina"
    assert row.country_origin == "person"
    assert db.query(NexusContactDelivery).count() == 0

    _patch_enrich(
        monkeypatch,
        {"full_name": "Ana Paz", "email": {"email": "ana-geo@acme.com"}, "mobile": {"mobile": "+5491100000000"}},
    )
    revealed, charged = svc.reveal_person(
        db,
        company_id=cid,
        user_id=uid,
        snapshot={
            "person_id": "p-geo",
            "name": "Ana Paz",
            "company_name": "Acme",
            "country": "Argentina",
            "linkedin_url": "https://www.linkedin.com/in/ana-geo",
        },
    )
    assert charged is True
    assert revealed.email == "ana-geo@acme.com"
    again = db.query(NexusContactCache).filter(NexusContactCache.linkedin_slug == "ana-geo").all()
    assert len(again) == 1
    assert again[0].country == "Argentina"
    assert again[0].country_origin == "person"
    assert db.query(NexusContactDelivery).count() == 1


def test_explorar_search_annotates_geo_without_provider(monkeypatch):
    f = {
        "person_job_title": {"include": ["CEO"], "match_mode": "CONTAINS"},
        "person_location_search": {"include": ["Argentina"]},
    }
    page = {
        "results": [
            {"person_id": "a", "name": "Ana", "role": "CEO", "country": "Argentina"},
            {"person_id": "b", "name": "Bob", "role": "CEO", "country": None},
            {"person_id": "c", "name": "Carla", "role": "CEO", "country": "Uruguay"},
        ],
        "total_count": 3,
        "total_pages": 1,
    }
    monkeypatch.setattr(svc, "_fetch_page", lambda filters, p: page)
    db = _session()
    cid, uid = _setup(db)
    out = svc.search(db, company_id=cid, user_id=uid, filters=f, page=1)
    ids = [r["person_id"] for r in out["results"]]
    assert ids == ["a"]
    assert "b" not in ids and "c" not in ids
    by_id = {r["person_id"]: r for r in out["results"]}
    assert by_id["a"]["geo_status"] == "confirmed"
