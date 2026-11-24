"""Conversación por mail después de agendar: mover, preguntar algo, reenvío programado."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services import inbound_auto_reply as iar
from app.services.inbound_turn_orchestrator import (
    inbound_mentions_scheduling,
    inbound_text_supports_scheduling_turn,
)
from app.services.meeting_scheduling_extras import (
    build_canceled_reply,
    inbound_asks_reschedule,
    inbound_cancels_meeting,
)
from app.services.outbound_text_normalize import normalize_outbound_email_body


@pytest.mark.parametrize(
    "text",
    [
        "Al final el jueves no puedo, ¿puede ser otro día?",
        "No voy a poder el jueves",
        "¿Podemos pasarla para otro horario?",
        "Hoy no llego, ¿la reprogramamos?",
        "Necesito cambiar la reunión",
    ],
)
def test_reschedule_requests_are_detected(text):
    assert inbound_asks_reschedule(text)
    assert inbound_cancels_meeting(text)


@pytest.mark.parametrize(
    "text",
    [
        "¿Y cómo funciona con LinkedIn?",
        "No puedo creer lo bueno que está",
        "Dale, el primero me viene bien",
        "¿Cuánto cuesta?",
    ],
)
def test_normal_messages_are_not_reschedules(text):
    assert not inbound_asks_reschedule(text)


def test_reschedule_reply_lists_slots_one_per_line():
    from datetime import UTC, datetime

    slots = [datetime(2026, 10, 16, 12, tzinfo=UTC), datetime(2026, 10, 19, 13, tzinfo=UTC)]
    text = build_canceled_reply(
        prospect_name="Joaquin Perez",
        alternatives=slots,
        timezone="America/Argentina/Buenos_Aires",
        reschedule=True,
    )
    assert text.startswith("Sin problema Joaquin, la movemos.")
    assert "cancelé" not in text
    assert text.count("\n•") == 2


def test_numeric_inbound_does_not_support_scheduling_turn():
    assert not inbound_text_supports_scheduling_turn("15")
    assert not inbound_text_supports_scheduling_turn("54911 234 5678")
    assert inbound_text_supports_scheduling_turn("Mañana a las 15hs")


def test_product_question_is_not_about_scheduling():
    assert not inbound_mentions_scheduling("¿Y cómo funciona con LinkedIn?")
    assert not inbound_mentions_scheduling("¿Cuánto cuesta?")
    assert not inbound_mentions_scheduling("Hacen onboarding 24hs?")
    assert not inbound_mentions_scheduling("Hacen onboarding en 24 horas?")
    assert inbound_mentions_scheduling("¿A qué hora era la reunión?")
    assert inbound_mentions_scheduling("¿Me reenviás la invitación?")
    assert inbound_mentions_scheduling("Mañana a las 15hs")


def test_answer_drops_greeting_signoff_and_reconfirmation():
    raw = (
        "Hola Joaquin, el costo depende del volumen. Confirmo entonces el jueves a las 17.\n"
        "Quedo atento.\n\nSaludos,\nJoaquin"
    )
    assert iar._answer_for_booking_reply(raw) == "El costo depende del volumen."


def test_answer_drops_fake_link_promise_and_ack():
    raw = "Perfecto, Joaquin. Te enviaré el enlace para la llamada."
    assert iar._answer_for_booking_reply(raw) == ""


def test_alternatives_do_not_interleave_link_promise():
    """Reproduce el bug: IA se metía entre 'pero sí a las…' y '¿Te sirve alguno?'."""
    alts = (
        "El lunes no tengo a las 12:00, pero sí a las 12:30, 11:00, 13:00.\n\n"
        "¿Te sirve alguno?"
    )
    fake_ai = "Perfecto, Joaquin. Te enviaré el enlace para la llamada."
    answer = iar._answer_for_booking_reply(fake_ai)
    assert answer == ""
    # Con answer vacío no se hace el partition/insert del path de alternativas.
    head, sep, rest = alts.partition("\n\n")
    mixed = f"{head}\n\n{answer}\n\n{rest}" if answer and sep else alts
    assert mixed == alts
    assert "enlace" not in mixed.lower()


def test_answer_goes_before_closing_line():
    confirmation = "Perfecto.\n\nTe agendé para mañana a las 17:00.\n\nNos vemos ahí."
    merged = iar._merge_booking_and_answer(confirmation, "El costo depende del volumen.")
    assert merged.endswith("El costo depende del volumen.\n\nNos vemos ahí.")
    assert merged.index("Te agendé") < merged.index("El costo")


def test_bullets_keep_their_own_line():
    body = "Sin problema, la movemos. Tengo libres:\n• Viernes 09:00\n• Lunes 10:00\n\n¿Cuál?"
    out = normalize_outbound_email_body(body)
    assert "libres:\n• Viernes 09:00\n• Lunes 10:00" in out


def test_soft_line_breaks_still_join():
    out = normalize_outbound_email_body("Esto es una oración\npartida en dos.")
    assert out == "Esto es una oración partida en dos."


def test_scheduled_task_keeps_the_decided_reply():
    notes = iar._task_notes("m1", planned={"subject": "Re:", "body": "Sin problema, la movemos."})
    task = SimpleNamespace(notes=notes)
    assert json.loads(notes)["planned"]["body"] == "Sin problema, la movemos."
    with patch.object(iar, "get_pending_send_task", return_value=task):
        assert iar._planned_reply(MagicMock(), 1, "m1")["body"] == "Sin problema, la movemos."


def test_old_tasks_without_planned_reply_are_recomputed():
    task = SimpleNamespace(notes=iar._task_notes("m1"))
    with patch.object(iar, "get_pending_send_task", return_value=task):
        assert iar._planned_reply(MagicMock(), 1, "m1") is None
