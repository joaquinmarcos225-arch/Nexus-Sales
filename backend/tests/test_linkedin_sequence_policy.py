"""Política de secuencia LinkedIn — conectar, expirar, omitir, reiniciar reloj."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.models.prospect import Prospect
from app.services import linkedin_assisted_service as las
from app.services import linkedin_sequence_policy as lsp


def _prospect(**kw) -> Prospect:
    base = dict(
        id=1,
        company_id=1,
        campaign_id=1,
        name="Ada",
        linkedin_url="https://www.linkedin.com/in/ada/",
        status="contacted",
        sequence_started_at=datetime(2026, 1, 1, tzinfo=UTC),
        sequence_fired_milestones="[]",
        sequence_touch_log="{}",
    )
    base.update(kw)
    return Prospect(**base)


def test_connect_wait_expires_after_three_days():
    sent = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    p = _prospect(
        linkedin_connection_status="invite_sent",
        linkedin_invite_sent_at=sent,
    )
    assert lsp.is_connect_wait_expired(p, now=sent + timedelta(days=2, hours=23)) is False
    assert lsp.is_connect_wait_expired(p, now=sent + timedelta(days=3)) is True


def test_expire_connect_sets_mention_flag():
    sent = datetime(2026, 1, 1, tzinfo=UTC)
    p = _prospect(
        linkedin_connection_status="invite_sent",
        linkedin_invite_sent_at=sent,
        linkedin_assisted_draft="borrador viejo",
    )
    assert lsp.expire_connect_invite(p, now=sent + timedelta(days=4)) is True
    assert p.linkedin_connection_status == lsp.CONN_EXPIRED
    assert p.linkedin_mention_next_touch is True
    assert not (p.linkedin_assisted_draft or "")


def test_expire_post_connect_draft_clears_queue():
    draft_at = datetime(2026, 1, 5, tzinfo=UTC)
    p = _prospect(
        linkedin_connection_status="connected",
        linkedin_post_connect_draft_at=draft_at,
        linkedin_assisted_draft="Hola Ada",
    )
    assert lsp.expire_post_connect_draft(p, now=draft_at + timedelta(days=2)) is False
    assert lsp.expire_post_connect_draft(p, now=draft_at + timedelta(days=3)) is True
    assert not (p.linkedin_assisted_draft or "")
    assert p.linkedin_post_connect_draft_at is None


def test_reset_clock_shifts_next_touch_three_days_after_send():
    # Full touch log mirrors production (_init_touch_log_generado); days 1+4 sent → next is 7.
    from app.core.sequence_playbook import PLAYBOOK_DAYS, scheduled_touch_at
    from app.services.prospect_sequence import TOUCH_PENDIENTE
    import json

    log = {str(d): {"status": TOUCH_PENDIENTE} for d in PLAYBOOK_DAYS}
    log["1"] = {"status": "enviado", "message_body": "hola"}
    log["4"] = {"status": "enviado", "message_body": "li"}
    p = _prospect(
        sequence_fired_milestones="[1,4]",
        sequence_touch_log=json.dumps(log),
    )
    sent = datetime(2026, 2, 10, 15, 0, tzinfo=UTC)
    lsp.reset_sequence_clock_after_post_connect_dm(p, sent)

    assert scheduled_touch_at(p.sequence_started_at, 7) == sent + timedelta(days=3)


def _db_no_inbound() -> MagicMock:
    db = MagicMock()
    db.scalar.return_value = None
    return db


def test_queue_touch_expired_returns_skip():
    p = _prospect(linkedin_connection_status="expired")
    campaign = MagicMock()
    action = las.queue_linkedin_sequence_touch(
        _db_no_inbound(), p, campaign, "DM", log_event=False
    )
    assert action == "skip"


def test_queue_touch_invite_sent_within_window_is_message():
    sent = datetime.now(UTC) - timedelta(days=1)
    p = _prospect(
        linkedin_connection_status="invite_sent",
        linkedin_invite_sent_at=sent,
    )
    campaign = MagicMock()
    action = las.queue_linkedin_sequence_touch(
        _db_no_inbound(), p, campaign, "DM", log_event=False
    )
    assert action == "message"
    assert (p.linkedin_assisted_draft or "").strip() == "DM"


def test_promote_stale_connection_check():
    """Tras 120s sin lectura → check_failed (NO Contactar)."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    young = _prospect(
        linkedin_connection_status="checking",
        linkedin_last_assisted_at=now - timedelta(seconds=30),
    )
    assert lsp.promote_stale_connection_check(young, now=now) is False
    assert young.linkedin_connection_status == "checking"

    stale = _prospect(
        linkedin_connection_status="checking",
        linkedin_last_assisted_at=now - timedelta(seconds=lsp.CHECKING_FALLBACK_SECONDS + 1),
    )
    assert lsp.promote_stale_connection_check(stale, now=now) is True
    assert stale.linkedin_connection_status == "check_failed"


def test_heal_unverified_invite_pending():
    """Contactar sin reloj ni borrador → check_queued (no satura checking)."""
    p = _prospect(
        linkedin_connection_status="invite_pending",
        linkedin_last_assisted_at=None,
        linkedin_invite_sent_at=None,
        linkedin_assisted_draft=None,
    )
    assert lsp.heal_unverified_invite_pending(p) is True
    assert p.linkedin_connection_status == "check_queued"

    with_draft = _prospect(
        linkedin_connection_status="invite_pending",
        linkedin_last_assisted_at=None,
        linkedin_assisted_draft="Hola, gracias por conectar.",
    )
    assert lsp.heal_unverified_invite_pending(with_draft) is False
    assert with_draft.linkedin_connection_status == "invite_pending"

    verified = _prospect(
        linkedin_connection_status="invite_pending",
        linkedin_last_assisted_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )
    assert lsp.heal_unverified_invite_pending(verified) is False
    assert verified.linkedin_connection_status == "invite_pending"


def test_revive_check_failed_requeues():
    p = _prospect(linkedin_connection_status="check_failed")
    assert lsp.revive_check_failed_for_retry(p) is True
    assert p.linkedin_connection_status == "check_queued"
