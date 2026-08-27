"""Paso 3: lazy móvil — import sin teléfono; enrich post-import / al activar."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.lead_sourcing.b2c_person_search import person_dict_to_lead
from app.services.lead_sourcing.role_person_search import person_dict_to_role_lead


def test_role_lead_importable_without_mobile_when_email_ok():
    """Antes mobile_rejected descartaba; ahora mail basta para armar lead."""
    person = {
        "person_id": "p1",
        "full_name": "Ana CEO",
        "current_job_title": "CEO",
        "email": {"email": "ana@acme.test", "revealed": True},
        "company": {"name": "Acme Inmobiliaria", "domain": "acme.test"},
        "linkedin_url": "https://www.linkedin.com/in/ana-ceo",
    }
    campaign = SimpleNamespace(
        id=1,
        target_role="CEO",
        target_industry="Inmobiliaria",
        target_country="Argentina",
        target_company_size="51-200",
        company_id=1,
    )
    lead = person_dict_to_role_lead(person, campaign=campaign, idx=0)
    assert lead is not None
    assert lead.email == "ana@acme.test"
    assert not (lead.phone or lead.whatsapp)


def test_b2c_lead_importable_without_mobile():
    person = {
        "person_id": "p2",
        "full_name": "Luis Coach",
        "email": {"email": "luis@mail.test", "revealed": True},
        "linkedin_url": "https://www.linkedin.com/in/luis-coach",
    }
    lead = person_dict_to_lead(
        person,
        campaign_id=1,
        idx=0,
        country_hint="Argentina",
        interests=["fitness"],
        locations=["Argentina"],
    )
    assert lead is not None
    assert not (getattr(lead, "phone", None) or getattr(lead, "whatsapp", None))


def test_helper_still_enriches_mobile_when_explicitly_asked():
    """Channel enrich post-import sigue pudiendo pedir móvil."""
    from app.services.lead_sourcing.role_person_search import _maybe_enrich_if_needed

    person = {
        "person_id": "abc",
        "email": {"email": "a@b.com", "revealed": True},
        "company": {"name": "Acme"},
        "mobile": {"mobile": "+54 9 11 ****-****"},
    }
    with patch(
        "app.services.lead_sourcing.role_person_search.enrich_person_by_id",
        return_value={"mobile": {"mobile": "+5491112345678", "revealed": True}},
    ) as mock_enrich:
        _maybe_enrich_if_needed(person, require_mobile=True)
    mock_enrich.assert_called_once_with("abc", require_mobile=True)
