"""WhatsApp inbound reply compose — contesta el texto del prospecto."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.whatsapp_reply_compose import (
    compose_whatsapp_inbound_reply,
    whatsapp_inbound_offline_draft,
)
from app.services import whatsapp_assisted_service as was
from app.services.whatsapp_inbound_sync import register_whatsapp_inbound


def _prospect(**kw):
    base = dict(
        id=1,
        company_id=1,
        campaign_id=1,
        name="Mia Alvarez",
        company_name="Acme",
        whatsapp="+5491128942875",
        phone=None,
        interest_level="medium",
        whatsapp_assisted_draft=None,
        whatsapp_assist_status="none",
        whatsapp_assist_session_id=None,
        whatsapp_last_assisted_at=None,
        whatsapp_sdr_marked_sent_at=None,
        sequence_paused=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _campaign():
    return SimpleNamespace(
        id=1,
        company_id=1,
        name="Demo",
        tone="profesional",
        seller_id=None,
        sender_name="Christian",
        product=SimpleNamespace(
            name="Nexus Sales",
            value_proposition="automatiza prospección outbound multicanal",
            description="",
        ),
        calendar_link=None,
    )


def test_offline_draft_scheduling_with_time_no_saludos():
    p = _prospect()
    c = _campaign()
    draft = whatsapp_inbound_offline_draft(
        p, c, inbound_text="hola, agendame a las 15hs"
    )
    low = draft.lower()
    assert "15" in draft
    assert "saludos" not in low
    assert "te parece agendar" not in low
    assert "qué día de esta semana" not in low
    assert "anoto" in low or "confirmo" in low or "15:00" in draft


def test_compose_openai_success_strips_email_sign(monkeypatch):
    p = _prospect()
    c = _campaign()
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.openai_service.openai_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.ai_instruction_context.campaign_education_blob",
        lambda *_a, **_k: "",
    )
    monkeypatch.setattr(
        "app.services.openai_service.generate_linkedin_inbound_reply",
        lambda **_k: "Genial Mia! Lo vemos mañana.\n\nSaludos,\nJoaquin",
    )
    draft = compose_whatsapp_inbound_reply(
        db,
        prospect=p,
        campaign=c,
        inbound_text="ok mañana",
        history=[],
    )
    assert "saludos" not in draft.lower()
    assert "joaquin" not in draft.lower() or "genial" in draft.lower()


def test_offline_draft_interest_skips_product_pitch():
    p = _prospect()
    c = _campaign()
    draft = whatsapp_inbound_offline_draft(p, c, inbound_text="hola, me interesa")
    low = draft.lower()
    assert "mi nombre es" not in low
    assert "90%" not in low
    assert "trabajo manual" not in low
    assert "automatiza prospección" not in low
    assert "agendar" in low or "reunión" in low or "reunion" in low or "charla" in low or "coordinamos" in low or "espacio" in low


def test_compose_uses_inbound_via_offline_when_openai_off(monkeypatch):
    p = _prospect()
    c = _campaign()
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.openai_service.openai_configured",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.ai_instruction_context.campaign_education_blob",
        lambda *_a, **_k: "",
    )
    draft = compose_whatsapp_inbound_reply(
        db,
        prospect=p,
        campaign=c,
        inbound_text="¿Cuál es la diferencia vs HubSpot?",
        history=[],
    )
    assert draft
    assert "mi nombre es" not in draft.lower()
    assert "diferencia" in draft.lower() or "une" in draft.lower() or "nexus" in draft.lower()


def test_compose_rejects_cold_open_from_openai(monkeypatch):
    p = _prospect()
    c = _campaign()
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.openai_service.openai_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.ai_instruction_context.campaign_education_blob",
        lambda *_a, **_k: "",
    )
    monkeypatch.setattr(
        "app.services.openai_service.generate_linkedin_inbound_reply",
        lambda **_k: (
            "Hola Mia, Mi nombre es Joaquin, te hablo desde CostGuard. "
            "Te escribo porque ayudamos a empresas a bajar costos."
        ),
    )
    draft = compose_whatsapp_inbound_reply(
        db,
        prospect=p,
        campaign=c,
        inbound_text="¿Cuánto sale?",
        history=[],
    )
    assert draft
    assert "mi nombre es" not in draft.lower()
    assert "te hablo desde" not in draft.lower()
    assert "precio" in draft.lower() or "plan" in draft.lower() or "cuesta" in draft.lower() or "volumen" in draft.lower()


def test_prepare_returns_contextual_draft():
    p = _prospect()
    c = _campaign()
    inbound = SimpleNamespace(
        direction="inbound",
        channel="whatsapp",
        message="¿Cuánto sale el plan?",
        created_at=None,
        id=1,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [inbound]

    with patch.object(was, "require_whatsapp_phone", lambda *_a, **_k: None), patch(
        "app.services.whatsapp_reply_compose.compose_whatsapp_inbound_reply",
        return_value="Hola Mia, el plan depende del volumen. ¿Te cuento en 10 min?",
    ), patch(
        "app.services.inbound_turn_orchestrator.resolve_inbound_scheduling_reply",
        return_value=SimpleNamespace(action="reply", reply_body=None),
    ), patch(
        "app.services.conversation_intelligence.build_signals_from_keywords",
        side_effect=Exception("skip"),
    ), patch.object(was, "mark_draft_suggested") as marked:
        draft, skipped, _meta = was.prepare_whatsapp_reply_after_inbound(db, p, c)

    assert skipped is False
    assert draft and "plan" in draft.lower()
    marked.assert_called_once()


def test_prepare_skips_autoresponder():
    p = _prospect()
    c = _campaign()
    inbound = SimpleNamespace(
        direction="inbound",
        channel="whatsapp",
        message="Estoy fuera de la oficina hasta el lunes",
        created_at=None,
        id=1,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [inbound]

    with patch.object(was, "require_whatsapp_phone", lambda *_a, **_k: None), patch(
        "app.services.whatsapp_reply_compose.compose_whatsapp_inbound_reply",
        return_value="Hola",
    ), patch(
        "app.services.inbound_turn_orchestrator.resolve_inbound_scheduling_reply",
        return_value=SimpleNamespace(action="skip_autoresponder", reply_body=None),
    ), patch(
        "app.services.conversation_intelligence.build_signals_from_keywords",
        side_effect=Exception("skip"),
    ):
        draft, skipped, _meta = was.prepare_whatsapp_reply_after_inbound(db, p, c)

    assert skipped is True
    assert draft is None


def test_register_falls_back_when_prepare_empty():
    p = _prospect()
    c = _campaign()
    db = MagicMock()

    with patch(
        "app.services.whatsapp_inbound_sync.process_whatsapp_inbound_for_prospect",
        return_value=True,
    ), patch(
        "app.services.whatsapp_assisted_service.prepare_whatsapp_reply_after_inbound",
        return_value=(None, False, {}),
    ), patch(
        "app.services.whatsapp_reply_compose.compose_whatsapp_inbound_reply",
        return_value="Fallback contextual al inbound",
    ) as compose, patch(
        "app.services.whatsapp_assisted_service.mark_draft_suggested",
    ):
        result = register_whatsapp_inbound(
            db,
            prospect=p,
            campaign=c,
            message="¿Cuánto sale?",
        )

    assert result["inserted"] is True
    assert result["reply_draft_ready"] is True
    assert "Fallback" in (result["reply_draft"] or "")
    compose.assert_called_once()


def test_register_no_fallback_when_skip():
    p = _prospect()
    c = _campaign()
    db = MagicMock()

    with patch(
        "app.services.whatsapp_inbound_sync.process_whatsapp_inbound_for_prospect",
        return_value=True,
    ), patch(
        "app.services.whatsapp_assisted_service.prepare_whatsapp_reply_after_inbound",
        return_value=(None, True, {}),
    ), patch(
        "app.services.whatsapp_reply_compose.compose_whatsapp_inbound_reply",
    ) as compose:
        result = register_whatsapp_inbound(
            db,
            prospect=p,
            campaign=c,
            message="Estoy de vacaciones",
        )

    assert result["reply_draft_ready"] is False
    compose.assert_not_called()


def test_register_blocks_on_calendar_reconnect():
    p = _prospect()
    c = _campaign()
    db = MagicMock()

    with patch(
        "app.services.whatsapp_inbound_sync.process_whatsapp_inbound_for_prospect",
        return_value=True,
    ), patch(
        "app.services.whatsapp_assisted_service.prepare_whatsapp_reply_after_inbound",
        return_value=(
            None,
            True,
            {
                "calendar_reconnect_required": True,
                "operator_message": "Google Calendar necesita reconexión.",
            },
        ),
    ), patch(
        "app.services.whatsapp_reply_compose.compose_whatsapp_inbound_reply",
    ) as compose:
        result = register_whatsapp_inbound(
            db,
            prospect=p,
            campaign=c,
            message="agendame a las 15hs",
            prepare_reply_draft=True,
        )

    assert result["reply_draft_ready"] is False
    assert result["calendar_reconnect_required"] is True
    assert "reconex" in (result["operator_message"] or "").lower()
    compose.assert_not_called()
