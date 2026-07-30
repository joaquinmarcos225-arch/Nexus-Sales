"""Tests mínimos: research notes + cola LinkedIn visible en checking."""
from datetime import UTC, datetime, timedelta

from app.services.linkedin_assisted_service import (
    CONN_CHECKING,
    CONN_INVITE_PENDING,
    is_queue_eligible,
)
from app.services.linkedin_sequence_policy import promote_stale_connection_check
from app.services.outreach_prospect_research import (
    RESEARCH_END,
    RESEARCH_START,
    extract_stored_research,
)


class _P:
    def __init__(self, **kw):
        self.linkedin_url = kw.get(
            "linkedin_url", "https://www.linkedin.com/in/ivan-braga-253454262/"
        )
        self.linkedin_connection_status = kw.get("conn", CONN_CHECKING)
        self.linkedin_assisted_draft = kw.get("draft", "Hola Ivan, demo")
        self.linkedin_last_assisted_at = kw.get("assisted_at")
        self.linkedin_sdr_marked_sent_at = None
        self.linkedin_assist_status = "suggested"
        self.linkedin_reply_available_at = None
        self.notes = kw.get("notes")


def test_extract_stored_research():
    notes = f"x\n{RESEARCH_START}\nbrief aqui\n{RESEARCH_END}\ny"
    assert extract_stored_research(notes) == "brief aqui"


def test_checking_with_draft_is_queue_eligible():
    p = _P(conn=CONN_CHECKING, draft="hola")
    assert is_queue_eligible(p) is True


def test_checking_without_draft_is_eligible_for_auto_verify():
    """Verify-first: checking sin borrador igual entra a sondeo / pending_verify."""
    p = _P(conn=CONN_CHECKING, draft="")
    assert is_queue_eligible(p) is True


def test_promote_stale_connection_check_after_75s():
    """checking >75s sin respuesta de extensión → invite_pending (Contactar)."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    young = _P(assisted_at=now - timedelta(seconds=30))
    assert promote_stale_connection_check(young, now=now) is False
    assert young.linkedin_connection_status == CONN_CHECKING

    stale = _P(assisted_at=now - timedelta(seconds=76))
    assert promote_stale_connection_check(stale, now=now) is True
    assert stale.linkedin_connection_status == CONN_INVITE_PENDING
