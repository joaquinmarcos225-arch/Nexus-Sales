from app.services.conversation_intelligence import (
    InboundSignals,
    inbound_has_explicit_meeting_slot,
    inbound_requests_meeting_or_demo,
    meeting_acceptance_detected,
    resolve_reply_objective,
)
from app.services.meeting_slot_parser import parse_meeting_slot
from app.services.commercial_conversation_agent import simulation_reply_needs_openai
from app.models.campaign import Campaign


def _sig() -> InboundSignals:
    return InboundSignals(
        objection_type=None,
        interest_level="medium",
        prospect_wants_meeting=False,
        explicit_meeting_commitment=False,
        asks_concrete_questions=False,
        is_brushoff=False,
        prospect_timing_hold=False,
        defer_resume_at_iso=None,
    )


def test_parse_agendame_manana_15hs():
    msg = "Agendame mañana a las 15 hs"
    assert parse_meeting_slot(msg) is not None
    assert inbound_has_explicit_meeting_slot(msg)
    assert inbound_requests_meeting_or_demo(msg)
    assert resolve_reply_objective(text=msg, sig=_sig(), response_class="interesado") == "agendar"
    campaign = Campaign(timezone="America/Argentina/Buenos_Aires")
    assert simulation_reply_needs_openai(
        inbound_text=msg,
        campaign=campaign,
        reply_objective="agendar",
        escalation_reason=None,
    ) is False


def test_parse_manana_15_me_queda_comodo():
    msg = "Mañana a las 15 me queda cómodo"
    assert parse_meeting_slot(msg) is not None
    assert meeting_acceptance_detected(msg)
    assert resolve_reply_objective(text=msg, sig=_sig(), response_class="interesado") == "agendar"
