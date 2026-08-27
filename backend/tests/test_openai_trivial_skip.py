"""Paso F: OpenAI evitado en clasificación inbound y brief de research."""

from __future__ import annotations

from unittest.mock import patch

from app.services import conversation_intelligence as ci
from app.services.lead_sourcing import cogs_runtime_metrics as m
from app.services.outreach_prospect_research import (
    _deterministic_research_brief,
    _research_openai_synth_enabled,
)


def test_inbound_classifier_skips_llm_on_clear_rejection():
    m.reset_for_tests()
    with patch("app.services.openai_service.classify_inbound_json_raw") as mock_llm:
        sig = ci.classify_inbound_full(
            inbound_text="No nos interesa, gracias.",
            prior_interest="low",
            conversation_digest="",
            education="",
        )
        mock_llm.assert_not_called()
    assert sig.objection_type == "not_interested"
    snap = m.snapshot()
    assert snap["openai_skipped_trivial"] == 1


def test_inbound_classifier_skips_llm_on_timing_defer():
    m.reset_for_tests()
    with patch("app.services.openai_service.classify_inbound_json_raw") as mock_llm:
        sig = ci.classify_inbound_full(
            inbound_text="Hablame dentro de 2 días, ahora no puedo.",
            prior_interest="medium",
            conversation_digest="",
            education="",
        )
        mock_llm.assert_not_called()
    assert sig.prospect_timing_hold
    assert sig.defer_resume_at_iso


def test_inbound_classifier_uses_llm_on_substantive_question():
    m.reset_for_tests()
    llm_payload = (
        '{"objection":"none","interest":"medium","wants_meeting":false,'
        '"explicit_meeting_commitment":false,"asks_questions":true,'
        '"brushoff":false,"prospect_timing_hold":false,"defer_resume_at":null}'
    )
    with patch(
        "app.services.openai_service.classify_inbound_json_raw",
        return_value=llm_payload,
    ) as mock_llm:
        with patch("app.services.openai_service.openai_configured", return_value=True):
            sig = ci.classify_inbound_full(
                inbound_text=(
                    "¿Cómo se integra con HubSpot y cuánto tarda la implementación "
                    "para un equipo de 15 personas?"
                ),
                prior_interest="low",
                conversation_digest="",
                education="",
            )
        mock_llm.assert_called_once()
    assert sig.asks_concrete_questions
    assert m.snapshot()["openai_skipped_trivial"] == 0


def test_inbound_classifier_confident_heuristic_cases():
    assert ci.inbound_classifier_confident_without_llm(
        "Coordinemos el jueves a las 15",
        ci.build_signals_from_keywords("Coordinemos el jueves a las 15", "low"),
    )
    assert not ci.inbound_classifier_confident_without_llm(
        "¿Qué incluye el plan enterprise y cómo facturan?",
        ci.build_signals_from_keywords(
            "¿Qué incluye el plan enterprise y cómo facturan?", "low"
        ),
    )


def test_deterministic_research_brief_no_openai_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_RESEARCH_OPENAI_SYNTH", raising=False)
    assert not _research_openai_synth_enabled()

    class _P:
        name = "Ana"
        role = "CEO"
        company_name = "Acme"
        industry = "SaaS"
        linkedin_url = ""

    class _C:
        outreach_mode = "b2b"

    class _Prod:
        name = "Nexus"

    brief = _deterministic_research_brief(
        prospect=_P(),
        campaign=_C(),
        product=_Prod(),
        snippets=["Acme — software de ventas — acme.com"],
    )
    assert "Acme" in brief
    assert "Hallazgos web" in brief
