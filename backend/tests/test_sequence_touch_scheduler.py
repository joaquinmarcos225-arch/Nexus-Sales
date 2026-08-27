"""Tests del scheduler de toques de secuencia."""

import json
from datetime import UTC, datetime, timedelta

from app.models.prospect import Prospect
from app.services.sequence_touch_scheduler import evaluate_scheduled_touch


def _prospect_day4_due() -> Prospect:
    started = datetime.now(UTC) - timedelta(days=3)
    log = {
        "1": {
            "status": "enviado",
            "sent_at": (started + timedelta(hours=2)).isoformat(),
            "message_body": "Hola día 1",
            "body": "Hola día 1",
            "gmail_draft_id": "draft-1",
        },
    }
    return Prospect(
        id=10,
        company_id=1,
        campaign_id=4,
        owner_user_id=3,
        email="mia@acme.com",
        linkedin_url="https://www.linkedin.com/in/mia-alvarez",
        sequence_started_at=started,
        sequence_touch_log=json.dumps(log),
        sequence_fired_milestones=json.dumps([1]),
        sequence_playbook_draft=json.dumps([{"day": 1, "body": "Hola día 1"}]),
    )


def test_evaluate_scheduled_touch_day4_when_calendar_due():
    prospect = _prospect_day4_due()
    day, reason = evaluate_scheduled_touch(prospect)
    assert day == 4
    assert reason is None


def test_evaluate_scheduled_touch_not_due_yet():
    prospect = _prospect_day4_due()
    prospect.sequence_started_at = datetime.now(UTC) - timedelta(days=1)
    day, reason = evaluate_scheduled_touch(prospect)
    assert day is None
    assert reason == "not_calendar_due"


def test_evaluate_scheduled_touch_skips_day1():
    prospect = _prospect_day4_due()
    prospect.sequence_started_at = datetime.now(UTC)
    prospect.sequence_touch_log = json.dumps({})
    prospect.sequence_fired_milestones = json.dumps([])
    day, reason = evaluate_scheduled_touch(prospect)
    assert day is None
    assert reason == "day1_initial_outreach"


def test_evaluate_scheduled_touch_day7_not_before_calendar():
    """Tras completar día 4, día 7 espera el calendario (no el mismo tick)."""
    started = datetime.now(UTC) - timedelta(days=3)
    log = {
        "1": {
            "status": "enviado",
            "sent_at": (started + timedelta(hours=2)).isoformat(),
            "message_body": "Hola día 1",
            "body": "Hola día 1",
            "gmail_draft_id": "draft-1",
        },
        "4": {
            "status": "enviado",
            "sent_at": datetime.now(UTC).isoformat(),
            "message_body": "Hola día 4",
            "body": "Hola día 4",
            "sdr_marked_sent": True,
        },
    }
    prospect = Prospect(
        id=11,
        company_id=1,
        campaign_id=4,
        owner_user_id=3,
        email="mia@acme.com",
        linkedin_url="https://www.linkedin.com/in/mia-alvarez",
        sequence_started_at=started,
        sequence_touch_log=json.dumps(log),
        sequence_fired_milestones=json.dumps([1, 4]),
        sequence_playbook_draft=json.dumps(
            [{"day": d, "body": f"body {d}"} for d in (1, 4, 7, 10, 13, 16, 19)]
        ),
    )
    day, reason = evaluate_scheduled_touch(prospect)
    assert day is None
    assert reason == "not_calendar_due"


def test_masked_phone_is_not_whatsapp_ready():
    from app.services.prospect_sequence import _has_valid_whatsapp

    assert _has_valid_whatsapp("+5491112345678", None) is True
    assert _has_valid_whatsapp("+54 9 342 6**-****", "+54 9 342 6**-****") is False


def test_calendar_helpers_from_playbook():
    from app.core.sequence_playbook import (
        is_touch_calendar_due,
        sequence_calendar_day_index,
        scheduled_touch_at,
    )

    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    now = datetime(2026, 6, 4, 12, 0, tzinfo=UTC)
    assert sequence_calendar_day_index(start, now) == 4
    assert is_touch_calendar_due(start, 4, now=now) is True
    assert is_touch_calendar_due(start, 7, now=now) is False
    assert scheduled_touch_at(start, 4) == datetime(2026, 6, 4, 0, 0, tzinfo=UTC)


def _li_wa_campaign():
    from types import SimpleNamespace

    return SimpleNamespace(
        id=7,
        company_id=1,
        seller_id=3,
        allowed_channels=["linkedin", "whatsapp"],
        post_sequence_followup_enabled=False,
        sequence_plan={
            "mode": "fixed",
            "steps": [
                {"day": 1, "channel": "linkedin"},
                {"day": 4, "channel": "whatsapp"},
            ],
        },
    )


def _prospect_unsent_li_day1(*, days_elapsed: int) -> Prospect:
    started = datetime.now(UTC) - timedelta(days=days_elapsed)
    log = {
        "1": {
            "status": "generado",
            "channel": "linkedin",
            "message_body": "Hola Mario, te escribo por LinkedIn",
            "body": "Hola Mario, te escribo por LinkedIn",
        }
    }
    return Prospect(
        id=59,
        company_id=1,
        campaign_id=7,
        owner_user_id=3,
        name="Mario",
        email=None,
        phone="+5491112345678",
        whatsapp="+5491112345678",
        linkedin_url="https://www.linkedin.com/in/mario-test",
        linkedin_assisted_draft="Hola Mario, te escribo por LinkedIn",
        linkedin_assist_status="suggested",
        sequence_started_at=started,
        sequence_touch_log=json.dumps(log),
        sequence_fired_milestones=json.dumps([]),
        sequence_playbook_draft=json.dumps(
            [
                {"day": 1, "channel": "linkedin", "body": "Hola Mario, te escribo por LinkedIn"},
                {"day": 4, "channel": "whatsapp", "body": ""},
            ]
        ),
    )


def test_expire_unsent_linkedin_when_next_touch_is_due_even_with_live_draft():
    """A los 3 días de calendario el LI no enviado sale y libera WhatsApp (día 4)."""
    from unittest.mock import MagicMock

    from app.services.linkedin_assisted_service import is_queue_eligible
    from app.services.prospect_sequence import (
        TOUCH_OMITIDO,
        _prior_sent_touches,
        _touch_log,
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=3)
    db = MagicMock()
    db.get.return_value = None

    omitted = expire_unsent_assisted_touches_for_calendar(
        db, prospect=prospect, campaign=campaign
    )
    assert omitted == [1]
    entry = _touch_log(prospect)["1"]
    assert entry["status"] == TOUCH_OMITIDO
    assert entry.get("skip_reason") == "asistido_sin_envio_3d"
    assert prospect.linkedin_assisted_draft is None
    assert next_executable_day(prospect, campaign) == 4
    assert _prior_sent_touches(prospect, 4, campaign) == []

    day, reason = evaluate_scheduled_touch(prospect, campaign=campaign)
    assert day == 4
    assert reason is None
    assert is_queue_eligible(prospect) is False


def test_expire_unsent_linkedin_when_sequence_old_even_if_draft_fresh():
    """Contactar sin aceptación: el calendario del día 4 manda, no la edad del borrador."""
    from unittest.mock import MagicMock

    from app.services.linkedin_assisted_service import is_queue_eligible
    from app.services.prospect_sequence import (
        TOUCH_OMITIDO,
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    log = json.loads(prospect.sequence_touch_log)
    log["1"]["generated_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    prospect.sequence_touch_log = json.dumps(log)

    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert omitted == [1]
    assert next_executable_day(prospect, campaign) == 4
    day, reason = evaluate_scheduled_touch(prospect, campaign=campaign)
    assert day == 4
    assert reason is None
    assert is_queue_eligible(prospect) is False
    assert not prospect.linkedin_assisted_draft


def test_do_not_expire_unsent_linkedin_before_ttl():
    from unittest.mock import MagicMock

    from app.services.linkedin_assisted_service import is_queue_eligible
    from app.services.prospect_sequence import (
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=1)
    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    # Sin generated_at → backfill, sin omitir.
    assert omitted == []
    assert next_executable_day(prospect, campaign) == 1
    day, reason = evaluate_scheduled_touch(prospect, campaign=campaign)
    assert day is None
    assert reason == "linkedin_pending_sdr"
    assert is_queue_eligible(prospect) is True


def test_expire_clears_omitted_linkedin_when_next_touch_due():
    """Omitido + card LI viva pero ya toca el día 4 → saca la card y sigue."""
    from unittest.mock import MagicMock

    from app.services.prospect_sequence import (
        TOUCH_OMITIDO,
        _touch_log,
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    prospect.sequence_touch_log = json.dumps(
        {
            "1": {
                "status": "omitido",
                "channel": "linkedin",
                "skip_reason": "asistido_sin_envio_3d",
                "message_body": "Hola Mario, te escribo por LinkedIn",
                "generated_at": (datetime.now(UTC) - timedelta(days=5)).isoformat(),
            },
            "4": {"status": "pendiente", "channel": "whatsapp"},
        }
    )
    prospect.linkedin_assisted_draft = "Hola Mario, te escribo por LinkedIn"
    prospect.linkedin_assist_status = "suggested"
    changed = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert changed == [1]
    assert _touch_log(prospect)["1"]["status"] == TOUCH_OMITIDO
    assert not prospect.linkedin_assisted_draft
    assert next_executable_day(prospect, campaign) == 4


def test_expire_restores_omitted_linkedin_if_next_touch_not_due_yet():
    """Omitido prematuro con card viva, todavía no toca el día 4 → vuelve a generado."""
    from unittest.mock import MagicMock

    from app.services.linkedin_assisted_service import is_queue_eligible
    from app.services.prospect_sequence import (
        TOUCH_GENERADO,
        _touch_log,
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=1)
    prospect.sequence_touch_log = json.dumps(
        {
            "1": {
                "status": "omitido",
                "channel": "linkedin",
                "skip_reason": "asistido_sin_envio_3d",
                "message_body": "Hola Mario, te escribo por LinkedIn",
                "generated_at": datetime.now(UTC).isoformat(),
            }
        }
    )
    prospect.linkedin_assisted_draft = "Hola Mario, te escribo por LinkedIn"
    prospect.linkedin_assist_status = "suggested"
    changed = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert changed == [1]
    assert _touch_log(prospect)["1"]["status"] == TOUCH_GENERADO
    assert prospect.linkedin_assisted_draft
    assert next_executable_day(prospect, campaign) == 1
    assert is_queue_eligible(prospect) is True

def test_expire_skips_linkedin_already_marked_sent():
    from unittest.mock import MagicMock

    from app.services.prospect_sequence import (
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    prospect.linkedin_sdr_marked_sent_at = datetime.now(UTC)
    log = json.loads(prospect.sequence_touch_log)
    log["1"]["generated_at"] = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    prospect.sequence_touch_log = json.dumps(log)
    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert omitted == []
    assert next_executable_day(prospect, campaign) == 1


def test_expire_unsent_linkedin_after_3_days_from_sequence_start():
    """Sin mark-sent a los 3 días desde el inicio → sale LI y sigue (WA día 4)."""
    from unittest.mock import MagicMock

    from app.services.linkedin_assisted_service import is_queue_eligible
    from app.services.prospect_sequence import (
        TOUCH_OMITIDO,
        _touch_log,
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=3)
    log = json.loads(prospect.sequence_touch_log)
    log["1"]["generated_at"] = prospect.sequence_started_at.isoformat()
    prospect.sequence_touch_log = json.dumps(log)
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_status = None

    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert omitted == [1]
    entry = _touch_log(prospect)["1"]
    assert entry["status"] == TOUCH_OMITIDO
    assert entry.get("skip_reason") == "asistido_sin_envio_3d"
    assert is_queue_eligible(prospect) is False
    assert next_executable_day(prospect, campaign) == 4


def test_handoff_respects_exact_start_hour():
    """Vie 17:00 → el salto es lun 17:00, no antes."""
    from unittest.mock import MagicMock

    from app.services.prospect_sequence import (
        assisted_next_touch_due_at,
        expire_unsent_assisted_touches_for_calendar,
    )

    campaign = _li_wa_campaign()
    # Viernes 21/08/2026 17:00 Argentina = 20:00 UTC
    started = datetime(2026, 8, 21, 20, 0, 0, tzinfo=UTC)
    prospect = _prospect_unsent_li_day1(days_elapsed=0)
    prospect.sequence_started_at = started
    log = json.loads(prospect.sequence_touch_log)
    log["1"]["generated_at"] = started.isoformat()
    prospect.sequence_touch_log = json.dumps(log)

    due = assisted_next_touch_due_at(prospect, 4, campaign)
    assert due == datetime(2026, 8, 24, 20, 0, 0, tzinfo=UTC)

    before = datetime(2026, 8, 24, 19, 59, 0, tzinfo=UTC)
    assert (
        expire_unsent_assisted_touches_for_calendar(
            MagicMock(), prospect=prospect, campaign=campaign, now=before
        )
        == []
    )
    assert prospect.linkedin_assisted_draft

    after = datetime(2026, 8, 24, 20, 0, 0, tzinfo=UTC)
    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign, now=after
    )
    assert omitted == [1]
    assert not prospect.linkedin_assisted_draft


def test_never_both_linkedin_and_whatsapp_live_drafts():
    from app.services.prospect_sequence import (
        ensure_single_assisted_live_queue,
        next_executable_day,
    )
    from app.services.whatsapp_assisted_service import is_queue_eligible as wa_eligible
    from app.services.linkedin_assisted_service import is_queue_eligible as li_eligible

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=3)
    prospect.sequence_touch_log = json.dumps(
        {
            "1": {
                "status": "omitido",
                "channel": "linkedin",
                "skip_reason": "asistido_sin_envio_3d",
                "generated_at": (datetime.now(UTC) - timedelta(days=3)).isoformat(),
            },
            "4": {
                "status": "generado",
                "channel": "whatsapp",
                "generated_at": datetime.now(UTC).isoformat(),
                "message_body": "Hola por WhatsApp",
            },
        }
    )
    prospect.linkedin_assisted_draft = "Hola Mario, te escribo por LinkedIn"
    prospect.linkedin_assist_status = "suggested"
    prospect.whatsapp_assisted_draft = "Hola por WhatsApp"
    prospect.whatsapp_assist_status = "suggested"

    assert ensure_single_assisted_live_queue(prospect, campaign) is True
    assert next_executable_day(prospect, campaign) == 4
    assert not prospect.linkedin_assisted_draft
    assert prospect.whatsapp_assisted_draft
    assert li_eligible(prospect) is False
    assert wa_eligible(prospect, campaign) is True


def test_conversation_hold_keeps_linkedin_not_whatsapp():
    """Hold con ambos borradores: gana LinkedIn; WA frío sale."""
    from app.services.prospect_sequence import ensure_single_assisted_live_queue
    from app.services.whatsapp_assisted_service import is_queue_eligible as wa_eligible

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    prospect.sequence_paused = True
    prospect.whatsapp_assisted_draft = "No debería verse"
    prospect.whatsapp_assist_status = "suggested"

    assert ensure_single_assisted_live_queue(prospect, campaign) is True
    assert prospect.linkedin_assisted_draft
    assert not prospect.whatsapp_assisted_draft
    assert wa_eligible(prospect, campaign) is False


def test_conversation_hold_keeps_whatsapp_reply_draft():
    """Hold solo con borrador WA (réplica inbound): se conserva y entra a cola."""
    from app.services.prospect_sequence import ensure_single_assisted_live_queue
    from app.services.whatsapp_assisted_service import is_queue_eligible as wa_eligible

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    prospect.sequence_paused = True
    prospect.linkedin_assisted_draft = None
    prospect.linkedin_assist_status = "sent"
    prospect.whatsapp_assisted_draft = "Gracias por responder, ¿agendamos?"
    prospect.whatsapp_assist_status = "suggested"
    prospect.whatsapp = "+5491112345678"
    prospect.phone = "+5491112345678"

    assert ensure_single_assisted_live_queue(prospect, campaign) is False
    assert prospect.whatsapp_assisted_draft
    assert wa_eligible(prospect, campaign) is True


def test_ttl_omits_linkedin_even_while_draft_still_in_queue():
    """Card LI viva + 3 días sin envío → sale igual y no bloquea WhatsApp."""
    from unittest.mock import MagicMock

    from app.services.linkedin_assisted_service import is_queue_eligible
    from app.services.prospect_sequence import (
        expire_unsent_assisted_touches_for_calendar,
        next_executable_day,
    )

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    log = json.loads(prospect.sequence_touch_log)
    log["1"]["generated_at"] = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    prospect.sequence_touch_log = json.dumps(log)

    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert omitted == [1]
    assert next_executable_day(prospect, campaign) == 4
    assert is_queue_eligible(prospect) is False
    day, reason = evaluate_scheduled_touch(prospect, campaign=campaign)
    assert day == 4
    assert reason is None

def test_expire_ttl_respects_sequence_paused():
    from unittest.mock import MagicMock

    from app.services.prospect_sequence import expire_unsent_assisted_touches_for_calendar

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    prospect.sequence_paused = True
    log = json.loads(prospect.sequence_touch_log)
    log["1"]["generated_at"] = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    prospect.sequence_touch_log = json.dumps(log)

    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert omitted == []
    assert prospect.linkedin_assisted_draft


def test_expire_ttl_respects_conversation_group():
    from unittest.mock import MagicMock

    from app.services.prospect_sequence import expire_unsent_assisted_touches_for_calendar

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=5)
    prospect.sequence_group = "reuniones"
    omitted = expire_unsent_assisted_touches_for_calendar(
        MagicMock(), prospect=prospect, campaign=campaign
    )
    assert omitted == []
    assert prospect.linkedin_assisted_draft


def test_ensure_single_assisted_queue_drops_whatsapp_while_linkedin_pending():
    from app.services.prospect_sequence import ensure_single_assisted_live_queue

    campaign = _li_wa_campaign()
    prospect = _prospect_unsent_li_day1(days_elapsed=1)
    prospect.whatsapp_assisted_draft = "Hola por WhatsApp (no debería estar)"
    prospect.whatsapp_assist_status = "suggested"
    changed = ensure_single_assisted_live_queue(prospect, campaign)
    assert changed is True
    assert prospect.linkedin_assisted_draft
    assert prospect.whatsapp_assisted_draft is None
