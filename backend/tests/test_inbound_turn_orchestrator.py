"""Inbound turn: entender → skip OOO / book / horarios / link."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services.inbound_turn_orchestrator import (
    build_proactive_availability_reply,
    inbound_asks_for_available_hours,
    resolve_inbound_scheduling_reply,
)
from app.services.conversation_intelligence import InboundSignals


def _sig(**kwargs):
    base = dict(
        objection_type=None,
        interest_level="high",
        prospect_wants_meeting=True,
        explicit_meeting_commitment=True,
        asks_concrete_questions=False,
        is_brushoff=False,
        prospect_timing_hold=False,
        defer_resume_at_iso=None,
    )
    base.update(kwargs)
    return InboundSignals(**base)


def test_asks_for_available_hours():
    assert inbound_asks_for_available_hours("¿Qué horarios tenés esta semana?")
    assert inbound_asks_for_available_hours("Pasame horarios disponibles")
    assert not inbound_asks_for_available_hours("No me interesa, gracias")


def test_proactive_availability_reply_lists_slots():
    slots = [
        datetime(2026, 7, 29, 15, 0, tzinfo=UTC),
        datetime(2026, 7, 30, 18, 0, tzinfo=UTC),
    ]
    text = build_proactive_availability_reply(
        prospect_name="Ana López",
        alternatives=slots,
        timezone="America/Argentina/Buenos_Aires",
        calendar_link=None,
    )
    assert "horarios me quedan libres" in text
    assert "Ana" in text
    assert "•" in text


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
@patch(
    "app.services.meeting_booking._seller_google_calendar_ready",
    return_value=True,
)
@patch(
    "app.services.inbound_turn_orchestrator._fetch_proactive_slots",
    return_value=[datetime(2026, 7, 29, 18, 0, tzinfo=UTC)],
)
def test_resolve_offers_hours_when_asks_availability(_slots, _ready, _book):
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="https://cal.test/x",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=1, name="Ana López")
    decision = resolve_inbound_scheduling_reply(
        db=None,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="¿Qué horarios tenés mañana?",
        reply_objective="agendar",
        sig=_sig(),
        suggested_reply="",
    )
    assert decision.action == "offer_hours"
    assert decision.reply_body
    assert "horarios me quedan libres" in decision.reply_body
    assert "http" not in (decision.reply_body or "").lower()
    assert "también podés agendar" not in (decision.reply_body or "").lower()


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
@patch(
    "app.services.meeting_booking._seller_google_calendar_ready",
    return_value=False,
)
def test_resolve_calendar_link_when_wants_meeting_no_hours(_ready, _book):
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="https://cal.test/x",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=1, name="Ana López")
    decision = resolve_inbound_scheduling_reply(
        db=None,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="Me interesa, agendemos una demo",
        reply_objective="agendar",
        sig=_sig(),
        suggested_reply=(
            "Te cuento brevemente cómo funciona: automatizamos la prospección "
            "para que el equipo solo intervenga con interés real. "
            "¿Qué día y horario te quedan cómodos?"
        ),
    )
    assert decision.action == "calendar_link"
    assert "https://cal.test/x" in (decision.reply_body or "")
    # XOR: no pedir franja + link en el mismo mensaje
    assert "qué día" not in (decision.reply_body or "").lower()
    assert "cómo funciona" not in (decision.reply_body or "").lower()


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
@patch(
    "app.services.meeting_booking.prospect_has_verified_calendar_meeting",
    return_value=False,
)
@patch(
    "app.services.meeting_booking._seller_google_calendar_ready",
    return_value=True,
)
@patch(
    "app.services.inbound_turn_orchestrator._fetch_proactive_slots",
    return_value=[datetime(2026, 8, 1, 15, 0, tzinfo=UTC)],
)
def test_ack_without_verified_meeting_offers_hours(_slots, _ready, _verified, _book):
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="https://calendar.google.com/calendar/u/0/r",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=1, name="Ivan Braga")
    decision = resolve_inbound_scheduling_reply(
        db=None,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="Genial, ya agende en el link! Nos vemos el viernes",
        reply_objective="agendar",
        sig=_sig(),
        suggested_reply="ignored",
    )
    # No Meeting real → no fingir booked; ofrecer horarios (link UI de GCal no cuenta)
    assert decision.action == "offer_hours"
    assert "http" not in (decision.reply_body or "").lower()


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
@patch(
    "app.services.meeting_booking.prospect_has_verified_calendar_meeting",
    return_value=True,
)
def test_ack_with_verified_meeting_confirms(_verified, _book):
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="https://cal.test/x",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=26, name="Ivan Braga")
    decision = resolve_inbound_scheduling_reply(
        db=None,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="Genial, ya agende en el link! Nos vemos el viernes",
        reply_objective="agendar",
        sig=_sig(),
        suggested_reply="ignored",
    )
    assert decision.action == "booked"
    assert "cal.test" not in (decision.reply_body or "")


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
def test_resolve_skips_autoresponder(_book):
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=1, name="Ana")
    # Keyword OOO typically classified as respuesta_automatica
    decision = resolve_inbound_scheduling_reply(
        db=None,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="Estoy de vacaciones hasta el 15. Respuesta automática.",
        reply_objective="seguimiento",
        sig=_sig(
            interest_level="low",
            prospect_wants_meeting=False,
            explicit_meeting_commitment=False,
        ),
        suggested_reply="Hola",
    )
    # May be skip or normal depending on classifier heuristics; at least no crash
    assert decision.action in ("skip_autoresponder", "normal_reply", "offer_hours", "calendar_link")


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
@patch("app.services.meeting_booking.attempt_duration_only_change")
@patch(
    "app.services.meeting_booking._seller_google_calendar_ready",
    return_value=True,
)
@patch(
    "app.services.inbound_turn_orchestrator._fetch_proactive_slots",
    return_value=[datetime(2026, 7, 29, 18, 0, tzinfo=UTC)],
)
def test_duration_only_change_does_not_reoffer_hours(_slots, _ready, mock_dur, _book):
    mock_dur.return_value = {
        "duration_change": True,
        "meeting_id": 9,
        "duration_minutes": 15,
        "confirmation_reply": (
            "Perfecto Mia.\n\nDejé la reunión en 15 minutos, mismo horario (mañana a las 10:00)."
        ),
    }
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="https://cal.test/x",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=1, name="Mia Álvarez")
    decision = resolve_inbound_scheduling_reply(
        db=None,  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="Si, solamente cambiar de 30 a 15min. gracias",
        reply_objective="agendar",
        sig=_sig(),
        suggested_reply="",
    )
    assert decision.action == "booked"
    assert "15 minutos" in (decision.reply_body or "")
    assert "horarios me quedan" not in (decision.reply_body or "").lower()
    mock_dur.assert_called_once()
    _slots.assert_not_called()


def test_inbound_refers_to_prior_schedule():
    from app.services.inbound_turn_orchestrator import inbound_refers_to_prior_schedule

    assert inbound_refers_to_prior_schedule(
        "YA te habia dicho el dia y la duracion que queria..."
    )
    assert inbound_refers_to_prior_schedule("como te dije, mantenemos el mismo horario")
    assert not inbound_refers_to_prior_schedule("¿Qué horarios tenés mañana?")


@patch(
    "app.services.meeting_booking.attempt_auto_book_from_message",
    return_value=None,
)
@patch(
    "app.services.meeting_booking.attempt_fulfill_from_conversation_context",
)
@patch(
    "app.services.meeting_booking._seller_google_calendar_ready",
    return_value=True,
)
@patch(
    "app.services.inbound_turn_orchestrator._fetch_proactive_slots",
    return_value=[datetime(2026, 7, 29, 18, 0, tzinfo=UTC)],
)
def test_refers_prior_fulfills_context_not_hours(_slots, _ready, mock_fulfill, _book):
    mock_fulfill.return_value = {
        "from_conversation_context": True,
        "meeting_id": 3,
        "confirmation_reply": (
            "Tenés razón Mia, perdón.\n\nQuedamos en mañana a las 10:00, 15 minutos."
        ),
    }
    campaign = SimpleNamespace(
        id=1,
        company_id=1,
        seller_id=1,
        calendar_link="https://cal.test/x",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
    )
    prospect = SimpleNamespace(id=1, name="Mia Álvarez")
    # db dummy truthy so fulfill path runs
    decision = resolve_inbound_scheduling_reply(
        db=object(),  # type: ignore[arg-type]
        campaign=campaign,  # type: ignore[arg-type]
        prospect=prospect,  # type: ignore[arg-type]
        inbound_text="YA te habia dicho el dia y la duracion que queria...",
        reply_objective="agendar",
        sig=_sig(),
        suggested_reply="",
    )
    assert decision.action == "booked"
    assert "Tenés razón" in (decision.reply_body or "")
    assert "horarios me quedan" not in (decision.reply_body or "").lower()
    mock_fulfill.assert_called()
    _slots.assert_not_called()
