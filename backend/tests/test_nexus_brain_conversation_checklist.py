"""
Checklist contrato — brain conversacional Nexus (pre-pruebas de equipo).

Casos happy-path + bordes que el brain debe respetar sin LLM.
Correr: pytest backend/tests/test_nexus_brain_conversation_checklist.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import conversation_intelligence as ci
from app.services import meeting_scheduling_extras as extras
from app.services.inbound_turn_orchestrator import (
    inbound_mentions_scheduling,
    inbound_text_supports_scheduling_turn,
)
from app.services.meeting_scheduling_extras import (
    auto_reply_silenced,
    inbound_asks_reschedule,
    should_ignore_inbound_after_meeting,
)


# ---------------------------------------------------------------------------
# 1) Pedido de info / “explicame” — NO es timing ni postergar
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Buenas,\n\nMe explicas un poco mas porfavor? automatiza la prospección?",
        "¿Me explicás un poco más cómo funciona?",
        "Contame más por favor",
        "Qué hace exactamente Nexus Sales?",
    ],
)
def test_checklist_product_question_is_not_timing(text: str):
    norm = ci.normalize_inbound_text_for_classification(text)
    sig = ci.build_signals_from_keywords(norm, None)
    assert sig.objection_type != "timing"
    assert not sig.prospect_timing_hold
    assert not ci.timing_deferral_should_apply(sig, inbound_text=text)


def test_checklist_eduardo_style_asks_questions():
    text = "Me explicas un poco mas porfavor? automatiza la prospección?"
    norm = ci.normalize_inbound_text_for_classification(text)
    sig = ci.build_signals_from_keywords(norm, None)
    assert sig.asks_concrete_questions


# ---------------------------------------------------------------------------
# 2) Timing real — SÍ posterga
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Ahora no, escribime la semana que viene",
        "Por ahora no es el momento, más adelante",
        "Hablame en 2 semanas",
        "Más adelante retomo, ahora no puedo",
        "Gracias! Escribime más tarde que ahora estoy en reunión",
    ],
)
def test_checklist_real_timing_defers(text: str):
    norm = ci.normalize_inbound_text_for_classification(text)
    sig = ci.build_signals_from_keywords(norm, "low")
    assert ci.timing_deferral_should_apply(sig, inbound_text=text)


def test_checklist_busy_in_meeting_is_timing_not_booking():
    text = "Gracias! Escribime más tarde que ahora estoy en reunión"
    norm = ci.normalize_inbound_text_for_classification(text)
    sig = ci.build_signals_from_keywords(norm, "low")
    assert not sig.prospect_wants_meeting
    assert not ci.inbound_wants_immediate_booking(text)
    assert ci.timing_deferral_should_apply(sig, inbound_text=text)


# ---------------------------------------------------------------------------
# 3) Agendar — se detecta intención de reunión / slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Si, agendemos para el lunes a las 14hs o 16hs.",
        "Mañana a las 15hs",
        "Dale el martes a las 10",
    ],
)
def test_checklist_scheduling_intent_detected(text: str):
    assert inbound_text_supports_scheduling_turn(text)
    assert inbound_mentions_scheduling(text)


def test_checklist_product_question_is_not_scheduling():
    assert not inbound_mentions_scheduling("¿Me explicás cómo automatiza la prospección?")
    assert not inbound_text_supports_scheduling_turn("¿Cuánto cuesta?")


# ---------------------------------------------------------------------------
# 4) Post-agendar — silencio (status/grupo), no solo commercial_state pegado
# ---------------------------------------------------------------------------


def test_checklist_post_booking_silence_by_status():
    p = SimpleNamespace(
        status="meeting_booked",
        ai_paused=False,
        meeting_scheduling_state=None,
        sequence_paused=True,
        sequence_group=None,
        commercial_state="reunion_agendada",
    )
    assert should_ignore_inbound_after_meeting(p) == "meeting_booked"
    assert auto_reply_silenced(p) == "meeting_booked"


def test_checklist_post_booking_silence_by_reuniones_group():
    p = SimpleNamespace(
        status="interested",
        ai_paused=False,
        meeting_scheduling_state=None,
        sequence_paused=True,
        sequence_group="reuniones",
        commercial_state=None,
    )
    assert should_ignore_inbound_after_meeting(p) == "meeting_booked"


def test_checklist_stale_commercial_state_alone_does_not_silence():
    """Tras reiniciar secuencia, reunion_agendada pegado no debe matar inbound."""
    p = SimpleNamespace(
        status="contacted",
        ai_paused=False,
        meeting_scheduling_state=None,
        sequence_paused=False,
        sequence_group="follow_ups",
        commercial_state="reunion_agendada",
    )
    assert should_ignore_inbound_after_meeting(p) is None
    assert auto_reply_silenced(p) is None


def test_checklist_pending_invite_still_allows_reply():
    p = SimpleNamespace(
        status="interested",
        ai_paused=False,
        meeting_scheduling_state=None,
        sequence_paused=True,
        sequence_group="follow_ups",
        commercial_state="reunion_pendiente",
    )
    assert should_ignore_inbound_after_meeting(p) is None
    assert auto_reply_silenced(p) is None


# ---------------------------------------------------------------------------
# 5) Confirmación de reunión — planned con flag no se silencia en worker
# ---------------------------------------------------------------------------


def test_checklist_meeting_confirmation_planned_bypasses_silence_gate():
    """Contrato: is_meeting_confirmation + silence meeting_booked → se permite enviar."""
    from app.services import inbound_auto_reply as iar

    planned = {"subject": "Nexus", "body": "Te agendé…", "is_meeting_confirmation": True}
    assert iar._allow_meeting_confirmation_despite_silence("meeting_booked", planned)
    assert not iar._allow_meeting_confirmation_despite_silence(
        "meeting_booked",
        {"subject": "X", "body": "hola", "is_meeting_confirmation": False},
    )


def test_checklist_closure_reply_kwarg_compatible():
    body = ci.build_professional_closure_reply(
        prospect_name="Eduardo Marcos",
        closure_kind=ci.CLOSURE_KIND_NOT_PRIORITY,
        inbound_text="porfavor",
    )
    assert "Eduardo" in body


# ---------------------------------------------------------------------------
# 6) Reagenda vs mensaje normal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Al final el jueves no puedo, ¿puede ser otro día?",
        "¿Podemos pasarla para otro horario?",
    ],
)
def test_checklist_reschedule_detected(text: str):
    assert inbound_asks_reschedule(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿Y cómo funciona con LinkedIn?",
        "Esta bueno.",
        "¿Cuánto cuesta?",
    ],
)
def test_checklist_normal_not_reschedule(text: str):
    assert not inbound_asks_reschedule(text)


# ---------------------------------------------------------------------------
# 7) Invitado extra — hoy NO soportado (contrato documentado; mejora futura)
# ---------------------------------------------------------------------------


def test_checklist_extra_guest_not_yet_a_multi_attendee_api():
    """
    Hoy el booking usa un solo attendee_email.
    'Sumá a Juanito + mail' no debe confundirse con un API de multi-invitado
    hasta que exista esa feature — este test fija la expectativa actual.
    """
    import inspect

    from app.services import google_calendar_create as gcc

    sig = inspect.signature(gcc.create_calendar_event)
    params = sig.parameters
    assert "attendee_email" in params
    assert "guest_emails" not in params
    assert "extra_attendees" not in params
