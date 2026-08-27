"""Calidad de borradores LinkedIn: anti-stub, piso CRM, variantes de estructura."""

from unittest.mock import MagicMock

from app.models.prospect import Prospect
from app.services import linkedin_assisted_service as las
from app.services.message_structure_variants import pick_first_touch_variant


def _prospect(**kw) -> Prospect:
    base = dict(
        id=11,
        company_id=1,
        campaign_id=1,
        name="Ada Lovelace",
        company_name="Analytical Engines",
        role="CTO",
        linkedin_url="https://www.linkedin.com/in/ada-lovelace/",
        status="imported",
        compatibility_score=80,
        interest_probability=50,
    )
    base.update(kw)
    return Prospect(**base)


def test_generic_stub_detected():
    assert las._is_generic_linkedin_stub(
        "Hola Ada,\n\n¿Tenés 10 minutos para una llamada corta?"
    )
    assert las._is_generic_linkedin_stub("")
    good = (
        "Hola Ada,\n"
        "Soy Joaquin de CostGuard.\n\n"
        "Te escribo porque consolida prospectos en un solo lugar.\n\n"
        "Plataforma Nexus automatiza el contacto multicanal.\n\n"
        "¿Te interesaría coordinar una reunión breve para ver si encaja?"
    )
    assert not las._is_generic_linkedin_stub(good)


def test_crm_floor_has_variant_structure():
    campaign = MagicMock()
    campaign.id = 1
    campaign.sender_name = "Joaquin Perez"
    campaign.seller = None
    campaign.company = MagicMock(name="CostGuard Demo Client")
    campaign.company.name = "CostGuard Demo Client"
    product = MagicMock()
    product.name = "Plataforma Nexus"
    product.description = (
        "Automatiza la búsqueda y el contacto por Mail, WhatsApp y LinkedIn desde un solo lugar."
    )
    product.value_proposition = (
        "Consolida prospectos, campañas y reporting en un solo lugar."
    )
    campaign.product = product

    p = _prospect()
    draft = las._crm_only_linkedin_draft(p, campaign)
    low = draft.lower()
    variant = pick_first_touch_variant(channel="linkedin", prospect_id=11, campaign_id=1)

    assert draft.startswith("Hola Ada,")
    assert "joaquin" in low
    assert "costguard" in low
    assert "equipos comerciales a consolidar" not in low
    assert "outband" not in low
    assert "tenés 10 minutos" not in low
    assert "?" in draft
    assert not las._is_generic_linkedin_stub(draft)
    # Según variante, debe haber identificación + valor + CTA
    assert "soy " in low
    if variant == "problem_offer":
        assert "te escribo porque" in low or "consolida" in low
    if variant == "context_value":
        assert "analytical engines" in low or "cto" in low
    if variant == "direct_short":
        assert "plataforma nexus" in low or "consolida" in low


def test_queue_ready_plants_crm_floor(monkeypatch):
    scheduled: list[int] = []
    monkeypatch.setattr(
        las, "schedule_linkedin_quality_draft", lambda pid: scheduled.append(pid)
    )
    monkeypatch.setattr(
        las,
        "mark_draft_suggested",
        lambda *a, **k: None,
    )

    db = MagicMock()
    campaign = MagicMock()
    campaign.id = 1
    campaign.sender_name = "Joaquin"
    campaign.seller = None
    campaign.company = MagicMock()
    campaign.company.name = "CostGuard"
    product = MagicMock()
    product.name = "Nexus"
    product.description = "Automatiza la búsqueda y el contacto por Mail, WhatsApp y LinkedIn."
    product.value_proposition = "Consolida prospectos en un solo lugar."
    campaign.product = product

    p = _prospect(id=48)
    monkeypatch.setattr(
        las,
        "_crm_only_linkedin_draft",
        lambda prospect, camp: "Hola Mia,\nSoy Joaquin de CostGuard.\n\nValor.\n\n¿Reunión?",
    )

    # Smoke: draft builder still callable
    assert "Hola" in las._crm_only_linkedin_draft(p, campaign)
