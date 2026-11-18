"""WhatsApp inbound reply compose — contesta el texto del prospecto."""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.whatsapp_reply_compose import (
    compose_whatsapp_inbound_reply,
    strip_whatsapp_email_signature,
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


def test_strip_whatsapp_email_signature_same_line_and_name():
    assert strip_whatsapp_email_signature(
        "¿Te queda bien el martes 3 a las 11:00?\n\nSaludos, joaquin"
    ) == "¿Te queda bien el martes 3 a las 11:00?"
    assert strip_whatsapp_email_signature(
        "¿Te queda bien el martes?\n\nSaludos,\nJoaquín"
    ) == "¿Te queda bien el martes?"
    kept = strip_whatsapp_email_signature("Hola Odille, ¿cómo estás?\n\nSoy Joaquín de CostGuard.")
    assert "Joaquín" in kept
    assert "Saludos" not in kept


def test_draft_confirms_unverified_slot_catches_yes_i_can():
    from app.services.whatsapp_reply_compose import draft_confirms_unverified_slot

    assert draft_confirms_unverified_slot("Sí, puedo a las 16")
    assert draft_confirms_unverified_slot("Dale, a esa hora me viene bien")
    assert not draft_confirms_unverified_slot(
        "Anoté 16:00. Lo cruzo con el calendario y te confirmo."
    )


def test_offline_draft_scheduling_with_time_asks_email_not_fake_booked():
    from app.services.whatsapp_reply_compose import draft_claims_unverified_booking

    p = _prospect()
    c = _campaign()
    draft = whatsapp_inbound_offline_draft(
        p, c, inbound_text="hola, agendame a las 15hs"
    )
    low = draft.lower()
    assert "15" in draft or "15:00" in draft
    assert "saludos" not in low
    assert "te parece agendar" not in low
    assert "mail" in low or "correo" in low
    assert not draft_claims_unverified_booking(draft)


def test_draft_claims_unverified_booking_detects_fake_confirm():
    from app.services.whatsapp_reply_compose import draft_claims_unverified_booking

    fake = (
        "Perfecto, te dejo agendada la llamada para el lunes 19 a las 10:30. "
        "Te mando el link de la reunión un día antes."
    )
    assert draft_claims_unverified_booking(fake)
    assert not draft_claims_unverified_booking("¿Me pasás tu mail para armar la invitación?")


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
    assert "funciones" not in low
    assert "horario" in low or "día" in low or "dia" in low


def test_offline_draft_accepts_offered_thursday_not_product_pitch():
    """«Buenas, me interesa. El jueves a esa hr está bien» → agenda, no pitch."""
    p = _prospect()
    c = _campaign()
    draft = whatsapp_inbound_offline_draft(
        p,
        c,
        inbound_text="Buenas, si me interesa. El jueves a esa hr esta bien.",
    )
    low = draft.lower()
    assert "funciones" not in low
    assert "usarían" not in low and "usarian" not in low
    assert "jueves" in low or "anot" in low or "mail" in low or "calendario" in low


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
    assert "buena pregunta" not in draft.lower()
    low = draft.lower()
    # Sin inventar contraste: puede ser defer a call o mención de producto.
    assert (
        "diferencia" in low
        or "hubspot" in low
        or "nexus" in low
        or "call" in low
        or "reun" in low
    )


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
    assert "buena pregunta" not in draft.lower()
    low = draft.lower()
    assert (
        "call" in low
        or "llamad" in low
        or "reun" in low
        or "precio" in low
        or "matices" in low
        or "agend" in low
    )
    assert "mi nombre es" not in low


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


def test_offline_draft_hubspot_defers_unknown_to_meeting():
    p = _prospect()
    c = _campaign()
    draft = whatsapp_inbound_offline_draft(p, c, inbound_text="¿Integran con HubSpot?")
    low = draft.lower()
    assert "hubspot" in low or "integr" in low
    # Sin inventar sí/no: lleva a call/reunión.
    assert "mirá la diferencia" not in low
    assert "en cambio" not in low
    assert "no tenemos" not in low
    assert "nativa" not in low
    assert any(k in low for k in ("call", "reun", "vemos", "15", "charla", "llamad"))
    assert "\n\n" in draft  # beats para burbujas


def test_crm_integration_generic_defers_and_rejects_invented_no():
    from app.services.whatsapp_reply_compose import draft_invents_integration_answer

    p = _prospect()
    c = _campaign()
    inbound = "Tienen integración con CRMs?"
    draft = whatsapp_inbound_offline_draft(p, c, inbound_text=inbound)
    low = draft.lower()
    assert "crm" in low or "integr" in low
    assert "no tenemos" not in low
    assert any(k in low for k in ("call", "reun", "vemos", "15", "charla", "llamad"))
    assert draft_invents_integration_answer(
        inbound, "No, no tenemos integración con CRMs."
    )
    assert not draft_invents_integration_answer(inbound, draft)


def test_onboarding_24hs_is_not_scheduling_push():
    """'24hs' de SLA no es horario; sin dato en ficha → reunión, no 'qué día'."""
    from app.services.whatsapp_reply_compose import (
        draft_calendar_push_ignores_question,
        inbound_is_info_question,
        prefer_offline_whatsapp_reply,
    )

    p = _prospect()
    c = _campaign()
    for inbound in (
        "Hacen onboarding 24hs?",
        "Hacen onboarding en 24 horas?",
    ):
        assert inbound_is_info_question(inbound)
        assert prefer_offline_whatsapp_reply(inbound) is False
        draft = whatsapp_inbound_offline_draft(p, c, inbound_text=inbound)
        low = draft.lower()
        assert "qué día" not in low and "que dia" not in low
        assert "horario de esta semana" not in low
        assert "buena pregunta" not in low
        assert "onboarding" in low or "stack" in low or "volumen" in low
        assert (
            "call" in low
            or "llamad" in low
            or "reun" in low
            or "vemos" in low
            or "15 min" in low
            or "agend" in low
            or "sirve" in low
        )
        assert not draft_calendar_push_ignores_question(inbound, draft)
        assert draft_calendar_push_ignores_question(
            inbound,
            "Genial.\n\n¿Qué día y horario de esta semana te queda bien para una charla corta?",
        )


def test_si_pero_personalizar_is_info_not_slot_confirm(monkeypatch):
    """«Sí, pero quiero saber si personalizo…» no es 'Anoté ese horario'."""
    from types import SimpleNamespace

    from app.services import whatsapp_reply_compose as w

    inbound = (
        "Si, pero antes quiero saber si puedo personalizar la forma "
        "en la que arma mensaje."
    )
    assert w.inbound_is_info_question(inbound)
    assert w.prefer_offline_whatsapp_reply(inbound) is False
    assert w._inbound_has_substance_beyond_affirm(inbound)

    last = (
        "Veo que te interesa lo de los idiomas. "
        "¿Te parece si coordinamos una charla corta?"
    )
    p = _prospect()
    monkeypatch.setattr(w, "_last_whatsapp_outbound_plain", lambda *_a, **_k: last)
    assert w._inbound_affirms_offered_slot(inbound, db=SimpleNamespace(), prospect=p) is False

    c = _campaign()
    monkeypatch.setattr(
        w,
        "unknown_fact_defer_to_meeting",
        lambda **k: "Lo de personalizar mensajes lo bajamos en una call corta. ¿Te parece?",
    )
    draft = w.whatsapp_inbound_offline_draft(p, c, inbound_text=inbound)
    low = draft.lower()
    assert "anoté" not in low
    assert "ese horario" not in low
    assert "mail para la invitaci" not in low
    assert any(k in low for k in ("call", "reun", "llamad", "personaliz", "mensaje", "15"))


def test_defer_bans_veo_que_te_interesa_opener():
    from app.services.whatsapp_reply_compose import _DEFER_BANNED_OPENERS_RE

    assert _DEFER_BANNED_OPENERS_RE.search(
        "Veo que te interesa lo de los idiomas para prospectar. ¿Call?"
    )
    assert not _DEFER_BANNED_OPENERS_RE.search(
        "Lo de idiomas lo prefiero bajar en una call corta. ¿Te parece?"
    )


def test_any_undocumented_product_question_defers_not_calendar():
    """Gate universal: cualquier pregunta sin dato en ficha → reunión, nunca agenda ciega."""
    from app.services.whatsapp_reply_compose import (
        ensure_info_question_handled,
        inbound_is_info_question,
        prefer_offline_whatsapp_reply,
    )

    p = _prospect()
    c = _campaign()
    questions = (
        "Hacen onboarding 24hs?",
        "Tienen SSO?",
        "Soportan white label?",
        "Tienen API pública?",
        "Hacen compliance ISO?",
        "En cuántos idiomas está la UI?",
    )
    bad = (
        "Genial.\n\n¿Qué día y horario de esta semana te queda bien "
        "para una charla corta?"
    )
    for inbound in questions:
        assert inbound_is_info_question(inbound), inbound
        assert prefer_offline_whatsapp_reply(inbound) is False, inbound
        draft = whatsapp_inbound_offline_draft(p, c, inbound_text=inbound)
        low = draft.lower()
        assert "qué día" not in low and "horario de esta semana" not in low, inbound
        assert any(
            k in low for k in ("call", "reun", "resolvemos", "vemos", "llamad", "demo", "15")
        ), (inbound, draft)
        fixed = ensure_info_question_handled(c, inbound, bad)
        assert "qué día" not in fixed.lower()
        assert any(
            k in fixed.lower()
            for k in ("call", "reun", "resolvemos", "vemos", "llamad", "demo", "15")
        )


def test_invented_yes_on_undocumented_api_is_stripped_even_with_meeting_cta():
    """Guardrail: 'Sí, contamos con API…' sin ficha → defer; invento gana sobre CTA."""
    from app.services.whatsapp_reply_compose import (
        draft_invents_undocumented_claim,
        ensure_info_question_handled,
    )

    c = _campaign()
    inbound = "Tienen API pública?"
    invented = (
        "Genial la consulta. Sí, contamos con API pública que permite "
        "integraciones flexibles para adaptar flujos. Lo mejor es verlo "
        "con tu caso en una charla breve de 15 minutos. ¿Te parece?"
    )
    assert draft_invents_undocumented_claim(c, inbound, invented)
    fixed = ensure_info_question_handled(c, inbound, invented)
    low = fixed.lower()
    assert "contamos con" not in low
    assert "sí, contamos" not in low
    assert not re.search(r"\bs[ií]\b.{0,20}\bapi\b", low)
    assert any(k in low for k in ("call", "reun", "vemos", "llamad", "demo", "15", "charla"))


def test_soft_capability_claim_on_languages_is_invent(monkeypatch):
    """«puede manejar… en varios idiomas» sin ficha → invento (no hace falta 'sí')."""
    from app.services import whatsapp_reply_compose as w

    c = _campaign()
    inbound = "En cuantos idiomas prospecta?"
    invented = (
        "Genial la consulta. La plataforma puede manejar campañas en varios "
        "idiomas, ajustando mensajes según el público objetivo. Lo ideal es "
        "verlo con tu caso específico en una llamada corta para definir "
        "alcance y opciones."
    )
    assert w.draft_invents_undocumented_claim(c, inbound, invented)
    monkeypatch.setattr(
        w,
        "unknown_fact_defer_to_meeting",
        lambda **k: "Lo de idiomas lo vemos fino en una call corta. ¿Te parece?",
    )
    fixed = w.ensure_info_question_handled(c, inbound, invented)
    low = fixed.lower()
    assert "puede manejar" not in low
    assert "varios idiomas" not in low
    assert any(k in low for k in ("call", "reun", "llamad", "demo", "15", "charla"))


def test_uncovered_info_question_never_keeps_model_draft(monkeypatch):
    """Sin ficha: aunque el draft 'suene' a defer, no se conserva (puede mentir)."""
    from app.services import whatsapp_reply_compose as w

    c = _campaign()
    inbound = "Tienen API pública?"
    sneaky = (
        "Genial la consulta. La plataforma puede manejar integraciones flexibles. "
        "Lo ideal es verlo en una llamada corta."
    )
    monkeypatch.setattr(
        w,
        "unknown_fact_defer_to_meeting",
        lambda **k: "DEFER_FRESCO\n\n¿Lo vemos en una call?",
    )
    out = w.ensure_info_question_handled(c, inbound, sneaky)
    assert out.startswith("DEFER_FRESCO")
    assert "puede manejar" not in out.lower()
    assert "genial la consulta" not in out.lower()


def test_ficha_does_not_false_cover_api_via_substring():
    """«aplicación» en ficha no autoriza afirmar API."""
    from app.services.whatsapp_reply_compose import (
        _ficha_covers_inbound_ask,
        draft_invents_undocumented_claim,
        ensure_info_question_handled,
    )

    c = _campaign()
    c.product_description = (
        "Aplicación de outbound multicanal con WhatsApp y LinkedIn."
    )
    inbound = "Tienen API pública?"
    assert not _ficha_covers_inbound_ask(c, inbound)
    invented = (
        "Sí, contamos con API pública que permite integraciones flexibles. "
        "Lo vemos en 15 minutos. ¿Te parece?"
    )
    assert draft_invents_undocumented_claim(c, inbound, invented)
    fixed = ensure_info_question_handled(c, inbound, invented)
    assert "contamos con" not in fixed.lower()


def test_documented_api_claim_is_allowed():
    from app.services.whatsapp_reply_compose import draft_invents_undocumented_claim

    c = _campaign()
    c.product_description = "Nexus incluye API pública para sincronizar datos con CRMs."
    inbound = "Tienen API pública?"
    ok = (
        "Sí, contamos con API pública para sincronizar datos. "
        "Si querés lo vemos en 15 min con tu stack."
    )
    assert not draft_invents_undocumented_claim(c, inbound, ok)


def test_unknown_fact_defer_variants_are_not_identical():
    from app.services.whatsapp_reply_compose import unknown_fact_defer_to_meeting

    texts = {
        unknown_fact_defer_to_meeting(
            topic="SSO", inbound_text="Tienen SSO?", use_llm=False
        )
        for _ in range(12)
    }
    # Con use_llm=False el seed por segundo puede colisionar; forzamos hashes distintos
    # generando offline variants directas.
    from app.services.whatsapp_reply_compose import _offline_defer_variants

    variants = _offline_defer_variants("SSO", booked_when=None)
    assert len(variants) >= 5
    assert len(set(variants)) == len(variants)
    assert all("qué día" not in v.lower() for v in variants)
    assert texts  # smoke
    assert all("buena pregunta sobre" not in v.lower() for v in variants)


def test_unknown_fact_defer_uses_llm_when_inbound_present(monkeypatch):
    from app.services import whatsapp_reply_compose as w

    calls: list[str] = []

    def _fake_llm(*, topic, booked_when, inbound_text, avoid_repeat=None):
        calls.append(inbound_text or "")
        return (
            "Lo del japonés en la UI lo quiero mirar con tu caso.\n\n"
            "¿Lo vemos en una call corta?"
        )

    monkeypatch.setattr(w, "_openai_defer_unknown_fact", _fake_llm)
    out = w.unknown_fact_defer_to_meeting(
        topic="idiomas",
        inbound_text="En cuántos idiomas está la UI? Tienen japonés?",
        use_llm=True,
    )
    assert "japonés" in out.lower()
    assert calls and "japonés" in calls[0].lower()


def test_unknown_fact_defer_without_inbound_skips_empty_llm(monkeypatch):
    from app.services import whatsapp_reply_compose as w

    monkeypatch.setattr(w, "_openai_defer_unknown_fact", lambda **k: None)
    out = w.unknown_fact_defer_to_meeting(
        topic="idiomas", inbound_text="", use_llm=True
    )
    low = out.lower()
    assert any(k in low for k in ("call", "reun", "llamad", "15", "agend", "charla"))
    assert "contamos con" not in low


def test_defer_draft_rejects_soft_lies():
    from app.services.whatsapp_reply_compose import _defer_draft_is_usable

    inbound = "En cuantos idiomas prospecta?"
    bad = (
        "Entiendo lo de idiomas, es clave para muchos clientes. "
        "¿Lo vemos en una call corta?"
    )
    assert not _defer_draft_is_usable(inbound, "idiomas", bad)
    bad2 = (
        "Es relevante para muchas empresas. "
        "¿Lo vemos en una call corta?"
    )
    assert not _defer_draft_is_usable(inbound, "idiomas", bad2)
    good = (
        "Vi lo de prospectar en distintos idiomas; prefiero no tirarte data floja acá.\n\n"
        "¿Lo bajamos en una call corta con tu caso?"
    )
    assert _defer_draft_is_usable(inbound, "idiomas", good)


def test_offline_draft_known_email_and_time_does_not_claim_booked():
    from app.services.commercial_conversation_state import sync_commercial_facts
    from app.services.whatsapp_reply_compose import draft_claims_unverified_booking

    p = _prospect(email="test@example.com", email_source="prospect")
    sync_commercial_facts(
        p,
        "Mi mail es test@example.com",
        timezone="America/Argentina/Buenos_Aires",
    )
    c = _campaign()
    draft = whatsapp_inbound_offline_draft(p, c, inbound_text="El martes a las 15 hs")
    assert "test@example.com" in draft
    assert not draft_claims_unverified_booking(draft)
    assert "invitación" in draft.lower() or "disponibilidad" in draft.lower()


def test_product_questions_prefer_llm_not_offline_gate():
    from app.services.whatsapp_reply_compose import prefer_offline_whatsapp_reply

    assert prefer_offline_whatsapp_reply("¿Qué hace Nexus exactamente?") is False
    assert prefer_offline_whatsapp_reply("¿Cuánto cuesta?") is False
    assert prefer_offline_whatsapp_reply("¿En qué se diferencian de Apollo?") is False
    assert prefer_offline_whatsapp_reply("¿Integran con HubSpot?") is False
    assert prefer_offline_whatsapp_reply("No gracias") is True
    assert prefer_offline_whatsapp_reply("dale") is True
    assert prefer_offline_whatsapp_reply("el jueves a las 15") is True


def test_integran_hubspot_angle_is_substantive_not_diff():
    from app.services.linkedin_reply_compose import _inbound_reply_angle

    assert _inbound_reply_angle("¿Integran con HubSpot?") == "substantive"
    assert _inbound_reply_angle("¿Cuál es la diferencia vs HubSpot?") == "differentiation"


def test_how_it_works_uses_bubbles_not_wall():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Qué hace Nexus exactamente?",
    )
    assert "\n\n" in draft
    assert len(draft.split()) <= 55


def test_affirm_after_slot_offer_continues_booking(monkeypatch):
    from types import SimpleNamespace

    p = _prospect(name="Odille")
    c = _campaign()
    db = SimpleNamespace()

    class _Row:
        message = "¿Te queda bien el lunes a las 10?"
        direction = "outbound"
        channel = "whatsapp"
        id = 9

    monkeypatch.setattr(
        "app.services.whatsapp_reply_compose._last_whatsapp_outbound_plain",
        lambda prospect, db=None: _Row.message,
    )
    draft = whatsapp_inbound_offline_draft(
        p, c, inbound_text="Dale", db=db
    )
    low = draft.lower()
    assert "cualquier cosa" not in low
    assert "mail" in low or "invitaci" in low or "calendario" in low


def test_compose_product_question_falls_back_to_product_facts_when_openai_off():
    p = _prospect()
    c = _campaign()
    draft = compose_whatsapp_inbound_reply(
        MagicMock(),
        prospect=p,
        campaign=c,
        inbound_text="¿Qué hace Nexus exactamente?",
        history=[],
    )
    low = draft.lower()
    assert "follow-up automático" not in low
    assert "nexus" in low or "mail" in low or "linkedin" in low or "prospec" in low


def test_rejection_closeout_stops_watching_chat():
    from app.services.whatsapp_reply_compose import apply_whatsapp_rejection_closeout

    p = _prospect(status="replied", sequence_group="contactado", sequence_paused=False, ai_paused=False)
    apply_whatsapp_rejection_closeout(p, stop_watching=False)
    assert p.status == "not_interested"
    assert p.sequence_group == "encajonado"
    assert p.sequence_paused is True
    assert p.ai_paused is False
    apply_whatsapp_rejection_closeout(p, stop_watching=True)
    assert p.ai_paused is True


def test_draft_off_topic_detects_followup_monologue():
    from app.services.whatsapp_reply_compose import draft_off_topic_for_inbound

    assert draft_off_topic_for_inbound(
        "Hola, me interesa Nexus",
        "El follow-up automático usa IA en email y WhatsApp.",
    )
    assert not draft_off_topic_for_inbound(
        "¿Cómo funciona el follow-up automático?",
        "El follow-up automático coordina mail y LinkedIn.",
    )


def test_ai_identity_question_is_denied():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="sos una IA?",
    )
    assert "no" in draft.lower()
    assert "IA" not in draft
    assert "Odille" not in draft
    assert "ayudar" not in draft.lower()
    assert "horario" in draft.lower() or "mail" in draft.lower()


def test_no_gracias_closes_without_meeting_cta():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Javier"),
        _campaign(),
        inbound_text="No gracias",
    )
    low = draft.lower()
    assert "entendido" in low or "dejamos" in low or "contacto" in low
    assert "charla" not in low
    assert "llamada" not in low
    assert "horario" not in low


def test_gracias_after_rejection_close_stays_silent():
    from app.models.outreach import OutreachMessage

    db = MagicMock()
    db.scalars.return_value.first.return_value = OutreachMessage(
        id=1,
        prospect_id=1,
        direction="outbound",
        channel="whatsapp",
        sender_type="ai",
        message="Entendido, gracias por avisar. Lo dejamos acá y quedamos en contacto.",
    )
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Javier"),
        _campaign(),
        inbound_text="Gracias",
        db=db,
    )
    assert draft == ""


def test_how_it_works_before_scheduling_word():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Antes de agendar, como funciona?",
    )
    low = draft.lower()
    assert "buena pregunta" not in low
    # Con ficha Nexus: contesta producto (no solo defer).
    assert "nexus" in low or "outbound" in low or "automatiz" in low or "60%" in draft or "90%" in draft
    assert "qué día" not in low and "que dia" not in low
    assert "ayudar" not in low


def test_what_is_uses_full_value_prop_not_truncated():
    camp = _campaign()
    camp.product = type(
        "P",
        (),
        {
            "name": "Nexus Sales",
            "value_proposition": (
                "Automatiza entre 60% y 90% de tareas manuales de prospección outbound "
                "integrando email, LinkedIn y WhatsApp en un flujo único con IA, "
                "permitiendo que el SDR solo intervenga cuando el prospecto muestra interés real."
            ),
            "description": "Plataforma B2B de ventas outbound.",
            "target_notes": "Beneficios principales\nSecuencias multicanal coordinadas.",
        },
    )()
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        camp,
        inbound_text="Que es?",
    )
    low = draft.lower()
    # Diferenciador multicanal (cualquier seed de variedad).
    assert "linkedin" in low and "whatsapp" in low
    assert "email" in low
    assert "ayudar" not in low
    assert "usd" not in low and "$" not in draft


def test_price_question_does_not_invent_numbers():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Y el precio?",
    )
    low = draft.lower()
    # Sin pricing en ficha → defer a call (sin inventar cifras).
    assert "call" in low or "reun" in low or "15" in low or "charla" in low
    assert "usd" not in low and "$" not in draft
    assert "por usuario" not in low
    assert not re.search(r"\$\s*\d|\d+\s*usd", low)


def test_short_yes_asks_for_meeting_slot():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Dale",
    )
    # Apertura: sin latiguillo («Genial.»); directo al pedido de horario.
    assert "genial" not in draft.lower()
    assert "Odille" not in draft
    assert "día y horario" in draft.lower()


def test_inbound_replies_do_not_vocative_name():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Y el precio?",
    )
    assert not draft.lower().startswith("odille")
    assert "Odille," not in draft


def test_crm_differentiation_does_not_reask_email():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Perdon que siga preguntando, pero de que se diferencia de un CRM?",
    )
    low = draft.lower()
    assert "crm" in low
    assert "pasás tu mail" not in low and "necesito tu mail" not in low
    assert "google calendar con link" not in low
    assert "odille" not in low


def test_differentiation_uses_campaign_product_not_fixed_script():
    camp = _campaign()
    camp.product = type(
        "P",
        (),
        {
            "name": "FleteYa",
            "value_proposition": (
                "Cotiza envíos en segundos y elige la mejor transportista para cada pedido."
            ),
            "description": "",
            "target_notes": "Objeciones comunes\nNo reemplaza la planilla: actualiza tarifas solas.",
        },
    )()
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        camp,
        inbound_text="en que se diferencia de un excel?",
    )
    low = draft.lower()
    assert "buena pregunta" not in low
    assert "fleteya" in low or "envíos" in low or "transportista" in low
    assert "excel" in low or "planilla" in low
    assert "sdr" not in low
    assert "prospección outbound" not in low
    assert "pasás tu mail" not in low


def test_just_curious_does_not_reask_meeting():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="Esta bien, era para saber nada mas.",
    )
    low = draft.lower()
    assert "cualquier cosa" in low or "avisás" in low or "avisas" in low
    assert "necesito tu mail" not in low
    assert "11:00" not in draft


def test_quoted_dot_uses_cited_email():
    from app.services.whatsapp_reply_compose import inbound_is_thin, merge_quoted_inbound

    assert inbound_is_thin(".")
    assert inbound_is_thin("…")
    assert not inbound_is_thin("odillemarcos@gmail.com")
    assert merge_quoted_inbound(".", "odillemarcos@gmail.com") == "odillemarcos@gmail.com"
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text=merge_quoted_inbound(".", "Te envio mi mail, odillemarcos@gmail.com"),
    )
    low = draft.lower()
    assert "odillemarcos@gmail.com" in low
    assert "necesito tu mail" not in low


def test_phone_origin_question_cites_linkedin_databases():
    draft = whatsapp_inbound_offline_draft(
        _prospect(name="Odille"),
        _campaign(),
        inbound_text="De donde sacaste mi celular?",
    )
    low = draft.lower()
    assert "linkedin" in low
    assert "odille" not in low
    assert "no te escriba" in low or "no te molesto" in low
