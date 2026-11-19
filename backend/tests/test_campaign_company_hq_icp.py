"""ICP Sede/HQ: campo opcional aparte de ubicación del contacto (sin Prospeo)."""

from __future__ import annotations

from app.schemas.campaign import CampaignCreate, CampaignRead, CampaignUpdate


def test_create_schema_accepts_optional_company_hq():
    payload = CampaignCreate(
        name="HQ test",
        seller_id=1,
        product_id=1,
        target_country="Argentina",
        target_company_hq="Argentina",
        target_role="CEO",
        prospect_count=10,
        timezone="America/Argentina/Buenos_Aires",
        available_hours="Lun-Vie 10-18",
        tone="Profesional",
    )
    assert payload.target_country == "Argentina"
    assert payload.target_company_hq == "Argentina"


def test_create_schema_hq_optional_none():
    payload = CampaignCreate(
        name="Sin HQ",
        seller_id=1,
        product_id=1,
        target_country="Argentina",
        target_role="CEO",
        prospect_count=10,
        timezone="America/Argentina/Buenos_Aires",
        available_hours="Lun-Vie 10-18",
        tone="Profesional",
    )
    assert payload.target_company_hq is None


def test_update_schema_can_set_hq():
    payload = CampaignUpdate(target_company_hq="Chile")
    assert payload.target_company_hq == "Chile"


def test_read_schema_includes_hq_field():
    fields = CampaignRead.model_fields
    assert "target_company_hq" in fields
    assert "target_country" in fields
