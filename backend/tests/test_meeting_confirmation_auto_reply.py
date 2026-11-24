"""Confirmación post-agenda: no cancelar el mail «te agendé» por silence meeting_booked."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import inbound_auto_reply as iar


def test_allow_meeting_confirmation_despite_silence():
    planned = {
        "subject": "Nexus Sales",
        "body": "Listo Eduardo, te agendé…",
        "is_meeting_confirmation": True,
    }
    assert iar._allow_meeting_confirmation_despite_silence("meeting_booked", planned)
    assert iar._allow_meeting_confirmation_despite_silence("meeting_done", planned)
    assert not iar._allow_meeting_confirmation_despite_silence(
        "meeting_booked",
        {"body": "hola", "is_meeting_confirmation": False},
    )
    assert not iar._allow_meeting_confirmation_despite_silence("meeting_booked", None)
    assert not iar._allow_meeting_confirmation_despite_silence("manual_pause", planned)
    assert not iar._allow_meeting_confirmation_despite_silence(None, planned)


def test_planned_reply_reads_in_progress_task():
    """Tras claim pending→in_progress el worker aún debe ver el body planificado."""
    mid = "1a1fe7186e8c05c0"
    notes = json.dumps(
        {
            "inbound_gmail_message_id": mid,
            "channel": "email",
            "v": 1,
            "planned": {
                "subject": "Nexus Sales",
                "body": "Te agendé para el lunes.",
                "is_meeting_confirmation": True,
            },
        }
    )
    task = SimpleNamespace(
        prospect_id=177,
        task_kind=iar.TASK_KIND_INBOUND_AUTO_REPLY,
        status="in_progress",
        notes=notes,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [task]
    planned = iar._planned_reply(db, 177, mid)
    assert planned is not None
    assert planned["is_meeting_confirmation"] is True
    assert "Te agendé" in planned["body"]


def test_planned_reply_ignores_other_inbound_mid():
    notes = json.dumps(
        {
            "inbound_gmail_message_id": "other-mid",
            "planned": {"body": "otro", "is_meeting_confirmation": True},
        }
    )
    task = SimpleNamespace(
        prospect_id=1,
        task_kind=iar.TASK_KIND_INBOUND_AUTO_REPLY,
        status="pending",
        notes=notes,
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = [task]
    assert iar._planned_reply(db, 1, "wanted-mid") is None
