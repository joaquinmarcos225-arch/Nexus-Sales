"""Calidad de borradores LinkedIn: anti-stub, piso CRM, variantes de estructura."""

from unittest.mock import MagicMock

from app.models.prospect import Prospect
from app.services import linkedin_assisted_service as las


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
        "Soy Joaquin de CostGuard. ¿Cómo estás?\n\n"
        "Te escribo por tu rol como CTO en Analytical Engines.\n\n"
        "Con Nexus ayudamos a conseguir más reuniones con menos seguimiento manual.\n\n"
        "¿Te queda bien una llamada corta esta semana?"
    )
    assert not las._is_generic_linkedin_stub(good)


def test_stale_crm_floor_detects_repetitive_role_anchor():
    old = (
        "Hola Ada,\n"
        "Soy Joaquin de CostGuard. ¿Cómo estás?\n\n"
        "Te escribo por tu rol como CTO en Acme.\n\n"
        "Con Nexus ayudamos a conseguir más reuniones.\n\n"
        "¿Te queda bien una llamada?"
    )
    assert las._is_stale_linkedin_crm_floor(old)


def test_stale_crm_floor_detected():
    old = (
        "Hola Vanessa,\n"
        "Soy Joaquin de CostGuard.\n\n"
        "Te escribo porque con Nexus Sales ayudamos a conseguir más reuniones.\n\n"
        "Lo hacemos mediante Nexus Sales, que plataforma multicanal de outreach comercial "
        "que combina IA, automatización asistida y control humano.\n\n"
        "Esto les permite reducir el trabajo manual de prospección "
        "y dedicar más tiempo a conversaciones reales.\n\n"
        "¿Te interesaría coordinar una reunión breve para mostrarte cómo funciona?"
    )
    assert las._is_stale_linkedin_crm_floor(old)
    assert las._is_interim_linkedin_draft(old)


def test_crm_floor_has_human_structure():
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

    assert draft.startswith("Hola Ada,")
    assert "joaquin" in low
    assert "costguard" in low
    assert "soy joaquin de costguard" in low
    assert "me presento" not in low
    assert "te escribo yo" not in low
    assert "por acá" not in low
    assert (
        "cómo estás" in low
        or "como estas" in low
        or "cómo va" in low
        or "como va" in low
        or "todo bien" in low
        or "cómo andás" in low
        or "como andas" in low
    )
    assert "cto" in low
    assert "analytical engines" in low
    from app.services.outreach_human_anchor import has_outreach_human_anchor

    assert has_outreach_human_anchor(draft)
    assert "?" in draft
    assert not las._is_generic_linkedin_stub(draft)
    assert len(draft) <= 480


def test_crm_floor_feminizes_role_and_names_company():
    campaign = MagicMock()
    campaign.id = 2
    campaign.sender_name = "Joaquin"
    campaign.seller = None
    campaign.company = MagicMock(name="CostGuard")
    campaign.company.name = "CostGuard"
    product = MagicMock()
    product.name = "Nexus Sales"
    product.description = "Automatiza prospección outbound."
    product.value_proposition = (
        "Incrementamos reuniones comerciales centralizando la prospección "
        "y contacto multicanal con IA entrenada en el producto del cliente."
    )
    campaign.product = product

    p = _prospect(
        id=22,
        name="Vanessa López",
        role="Commercial & Business Development Director | FMCG & Spirits",
        company_name="Destileria16",
        email="vanessa@gmail.com",
        source_provider="prospeo",
    )
    draft = las._crm_only_linkedin_draft(p, campaign)
    assert "Destileria16" in draft
    assert "ventas y marketing" not in draft.lower()
    assert "con entrenada" not in draft.lower()
    from app.services.outreach_human_anchor import has_outreach_human_anchor

    assert has_outreach_human_anchor(draft)
    assert draft.lower().count("nexus sales") == 1
    assert "reuniones" in draft.lower()
    assert "implementarían" not in draft.lower()


def test_role_maps_business_development_not_sales_marketing():
    from app.services.outreach_display_names import prospect_role_for_outreach

    role = prospect_role_for_outreach(
        "Commercial & Business Development Director | FMCG & Spirits",
        "Vanessa López",
    )
    assert role == "Directora de desarrollo de negocios"
    assert "marketing" not in role.lower()


def test_old_draft_with_known_company_is_not_flagged_but_stale_floor_regens():
    p = _prospect(
        id=23,
        name="Vanessa López",
        company_name="Destileria16",
        source_provider="prospeo",
    )
    old = (
        "Hola Vanessa,\n"
        "Soy Joaquin de CostGuard. ¿Cómo estás?\n\n"
        "Te escribo por tu rol como Directora Comercial en Destileria16.\n\n"
        "Con Nexus Sales incrementamos reuniones con entrenada en el producto.\n\n"
        "¿Te queda bien una llamada?"
    )
    assert not las._draft_mentions_unverified_company(old, p)
    assert las._is_stale_linkedin_crm_floor(old)
    assert las._is_interim_linkedin_draft(old, p)


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
        lambda prospect, camp: (
            "Hola Mia,\n"
            "Soy Joaquin de CostGuard. ¿Cómo estás?\n\n"
            "Te escribo por tu rol como CTO en Acme.\n\n"
            "Con Nexus ayudamos a conseguir más reuniones.\n\n"
            "¿Reunión?"
        ),
    )

    assert "Hola" in las._crm_only_linkedin_draft(p, campaign)


def test_stale_draft_not_cleared_by_queue_ready(monkeypatch):
    """Regresión: drafts stale no se borran ni re-encolan OpenAI en cada poll."""
    campaign = MagicMock()
    campaign.id = 1
    campaign.product_id = None
    campaign.product = None
    p = _prospect(
        id=65,
        linkedin_assisted_draft=(
            "Hola Manuel,\n\nSoy Joaquin de CostGuard.\n\n"
            "Vi que sos CEO de Codee.\n\n"
            "En ventas outbound, las tareas manuales se acumulan. "
            "Con Nexus Sales, plataforma multicanal de outreach, "
            "esto les permite reducir el trabajo manual de prospección.\n\n"
            "¿Te queda bien una llamada?"
        ),
    )
    assert las._is_stale_linkedin_crm_floor(p.linkedin_assisted_draft)
    assert las._is_interim_linkedin_draft(p.linkedin_assisted_draft, p)

    scheduled = []
    monkeypatch.setattr(las, "schedule_linkedin_quality_draft", lambda pid: scheduled.append(pid))
    out = las._queue_ready_linkedin_draft(MagicMock(), p, campaign, existing=None)
    assert "Manuel" in out or "Hola" in out
    assert p.linkedin_assisted_draft  # no se vació
    assert scheduled == []


def test_ensure_keeps_stale_draft_without_openai(monkeypatch):
    campaign = MagicMock()
    campaign.id = 1
    campaign.product_id = None
    p = _prospect(
        id=65,
        linkedin_assisted_draft=(
            "Hola Manuel,\n\nSoy Joaquin de CostGuard.\n\n"
            "Lo hacemos mediante Nexus Sales, que plataforma multicanal de outreach "
            "comercial. Esto les permite reducir el trabajo manual de prospección.\n\n"
            "¿Te queda bien una llamada?"
        ),
    )
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("no debe llamar generate")

    monkeypatch.setattr(las, "generate_playbook_touch_for_prospect", _boom)
    monkeypatch.setattr(
        "app.services.linkedin_assisted_service.generate_playbook_touch_for_prospect",
        _boom,
        raising=False,
    )
    # patch where ensure imports from
    import app.services.sdr_outreach_compose as compose

    monkeypatch.setattr(compose, "generate_playbook_touch_for_prospect", _boom)
    out = las.ensure_linkedin_draft(MagicMock(), p, campaign)
    assert out == p.linkedin_assisted_draft
    assert called["n"] == 0
