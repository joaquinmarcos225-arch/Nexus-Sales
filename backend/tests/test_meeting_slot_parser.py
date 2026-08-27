from datetime import datetime

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


def test_parse_agendame_manana_15hrs():
    msg = "Agendame mañana a las 15 hs"
    assert parse_meeting_slot(msg) is not None
    assert inbound_has_explicit_meeting_slot(msg)
    assert inbound_requests_meeting_or_demo(msg)


def test_parse_15hrs_without_space():
    msg = "Agendame manana a las 15hrs"
    assert parse_meeting_slot(msg) is not None


def test_parse_reschedule_same_day_with_context():
    from zoneinfo import ZoneInfo

    ctx = datetime(2026, 6, 26, 16, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "che al final a esa hr no puedo, lo podemos pasar a las 14 hs?"
    slot = parse_meeting_slot(
        msg,
        timezone="America/Argentina/Buenos_Aires",
        context_meeting_at=ctx,
        now=datetime(2026, 6, 24, 12, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")),
    )
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.weekday() == 4  # viernes
    assert local.hour == 14
    assert local.minute == 0
    assert resolve_reply_objective(text=msg, sig=_sig(), response_class="interesado") == "agendar"
    campaign = Campaign(
        timezone="America/Argentina/Buenos_Aires",
        calendar_link="https://cal.example/book",
    )
    assert simulation_reply_needs_openai(
        inbound_text=msg,
        campaign=campaign,
        reply_objective="agendar",
        escalation_reason=None,
    ) is False


def test_parse_viernes_15hrs_reschedule():
    from zoneinfo import ZoneInfo

    msg = (
        "Al final podemos pasarla para las 15hrs del viernes? "
        "mil disculpas por los cambios de horarios."
    )
    now = datetime(2026, 6, 25, 12, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.weekday() == 4
    assert local.hour == 15
    assert local.minute == 0


def test_parse_viernes_15hrs_ignores_gmail_quote():
    from zoneinfo import ZoneInfo

    from app.services.meeting_slot_parser import strip_email_reply_quotes

    msg = (
        "Al final podemos pasarla para las 15hrs del viernes? mil disculpas\n\n"
        "El jue, 25 jun 2026 a las 12:00, joaquinmarcos225@gmail.com escribió:\n"
        "> Listo, moví la reunión para mañana a las 14:00."
    )
    assert "mañana" not in strip_email_reply_quotes(msg).lower()
    now = datetime(2026, 6, 25, 14, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.weekday() == 4
    assert local.hour == 15
    assert local.minute == 0


def test_parse_time_only_inherits_friday_from_context():
    from zoneinfo import ZoneInfo

    now = datetime(2026, 6, 25, 17, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    friday_ctx = datetime(2026, 6, 26, 15, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "A las 14:30 puedo 15 min, me agendas porfavor?"
    slot = parse_meeting_slot(
        msg,
        now=now,
        timezone="America/Argentina/Buenos_Aires",
        context_meeting_at=friday_ctx,
    )
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.weekday() == 4
    assert local.hour == 14
    assert local.minute == 30
    assert local.date().isoformat() == "2026-06-26"


def test_parse_time_only_without_context_and_past_time_returns_none():
    """Sin contexto de día, una hora que ya pasó hoy no debe saltar a la semana siguiente."""
    from zoneinfo import ZoneInfo

    now = datetime(2026, 6, 25, 17, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "A las 14:30 puedo 15 min, me agendas porfavor?"
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is None


def test_parse_time_only_with_context_uses_negotiated_day():
    from zoneinfo import ZoneInfo

    now = datetime(2026, 6, 25, 17, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    ctx = datetime(2026, 6, 26, 12, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "A las 14:30 puedo 15 min, me agendas porfavor?"
    slot = parse_meeting_slot(
        msg,
        now=now,
        timezone="America/Argentina/Buenos_Aires",
        context_meeting_at=ctx,
    )
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.date().isoformat() == "2026-06-26"
    assert local.hour == 14
    assert local.minute == 30


def test_parse_hoy_same_day():
    from zoneinfo import ZoneInfo

    now = datetime(2026, 6, 26, 10, 44, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "Podemos agendar para hoy a las 15hrs?"
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.date() == now.date()
    assert local.hour == 15
    assert local.minute == 0


def test_parse_hoy_1530_with_15_min_duration():
    from zoneinfo import ZoneInfo

    from app.services.meeting_slot_parser import parse_meeting_duration_minutes

    now = datetime(2026, 6, 26, 10, 47, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "el dia de hoy a las 15:30 esta bien. 15 min agendame porfavor. no mas que eso"
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.date() == now.date()
    assert local.hour == 15
    assert local.minute == 30
    assert parse_meeting_duration_minutes(msg) == 15


def test_viernes_on_friday_is_today_not_next_week():
    from zoneinfo import ZoneInfo

    now = datetime(2026, 6, 26, 10, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "¿Podemos hablar el viernes a las 15 hs?"
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is not None
    local = slot.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert local.date() == now.date()


def test_viernes_past_time_on_friday_returns_none_not_next_week():
    from zoneinfo import ZoneInfo

    now = datetime(2026, 6, 26, 11, 0, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))
    msg = "¿Podemos hablar el viernes a las 10 hs?"
    slot = parse_meeting_slot(msg, now=now, timezone="America/Argentina/Buenos_Aires")
    assert slot is None


def test_parse_meeting_duration_minutes():
    from app.services.meeting_slot_parser import parse_meeting_duration_minutes

    assert parse_meeting_duration_minutes("A las 14:30 puedo 15 min, me agendas porfavor?") == 15
    assert parse_meeting_duration_minutes("¿30 minutos te viene bien el viernes?") == 30
    assert parse_meeting_duration_minutes("Podemos hablar el viernes a las 15 hs?") == 30
    assert parse_meeting_duration_minutes("Media hora me alcanza") == 30
    assert parse_meeting_duration_minutes("Una hora si hace falta") == 60
    assert parse_meeting_duration_minutes("Hora y media el martes a las 10") == 90
    assert parse_meeting_duration_minutes("Agendemos una llamada de 45 minutos") == 45
    assert parse_meeting_duration_minutes("Martes 11hs, reunión de 20") == 20
    assert parse_meeting_duration_minutes("Puedo el jueves a las 16, 15'") == 15
    assert parse_meeting_duration_minutes("cambiar de 30 a 15 min") == 15
    assert parse_meeting_duration_minutes("Si, solamente cambiar de 30 a 15min. gracias") == 15


def test_inbound_is_duration_only_change():
    from app.services.meeting_slot_parser import inbound_is_duration_only_change

    assert inbound_is_duration_only_change("cambiar de 30 a 15 min") == 15
    assert (
        inbound_is_duration_only_change("Si, solamente cambiar de 30 a 15min. gracias") == 15
    )
    assert inbound_is_duration_only_change("¿Puedo cambiar la reunión a 15 minutos?") == 15
    # Con horario nuevo → no es solo duración
    assert inbound_is_duration_only_change("Martes a las 10, 15 min por favor") is None
    assert inbound_is_duration_only_change("¿Qué horarios tenés?") is None


def test_parse_manana_15_me_queda_comodo():
    msg = "Mañana a las 15 me queda cómodo"
    assert parse_meeting_slot(msg) is not None
    assert meeting_acceptance_detected(msg)
    assert resolve_reply_objective(text=msg, sig=_sig(), response_class="interesado") == "agendar"
