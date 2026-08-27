"""Regresión: resolve_commercial_state no debe lanzar NameError."""

from unittest.mock import MagicMock

from app.models.enums import ProspectStatus
from app.services.prospect_commercial_state import (
    COMMERCIAL_INTERESADO,
    resolve_commercial_state,
)


def _prospect_stub(**overrides):
    p = MagicMock()
    p.pipeline_stage = ""
    p.commercial_state = None
    p.commercial_state_is_testing = False
    p.sequence_started_at = None
    p.last_outbound_at = None
    p.status = ProspectStatus.interested.value
    p.last_inbound_at = None
    p.objection_type = None
    p.interest_level = "high"
    p.sequence_touch_log = None
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


def test_resolve_commercial_state_uses_prospect_status_not_undefined_name():
    prospect = _prospect_stub()
    state = resolve_commercial_state(prospect, db=None, include_testing=True)
    assert state == COMMERCIAL_INTERESADO
