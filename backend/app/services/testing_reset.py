"""Reinicio de datos operativos de testing (solo desarrollo)."""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai_decision_event import AiDecisionEvent
from app.models.enums import PipelineStage, ProspectOwnershipStatus, ProspectStatus
from app.models.inbound_auto_reply_receipt import InboundAutoReplyReceipt
from app.models.meeting import Meeting
from app.models.outreach import OutreachMessage
from app.models.outreach_task import OutreachTask
from app.models.prospect import Prospect
from app.models.prospect_ownership_event import ProspectOwnershipEvent
from app.services.commercial_conversation_agent import CONV_NONE
from app.services.prospect_commercial_state import COMMERCIAL_PROSPECCION

logger = logging.getLogger(__name__)


def is_testing_reset_enabled() -> bool:
    app_env = (os.getenv("APP_ENV") or os.getenv("ENV") or "").strip().lower()
    test_mode = (os.getenv("TEST_MODE") or "").strip().lower()
    if app_env in ("development", "dev", "local"):
        return True
    return test_mode in ("1", "true", "yes", "on")


def _baseline_status(current: str) -> str:
    base = (current or "").strip().lower()
    if base in (
        ProspectStatus.imported.value,
        ProspectStatus.compatible.value,
        ProspectStatus.not_compatible.value,
    ):
        return base
    return ProspectStatus.compatible.value


def reset_company_testing_data(db: Session, *, company_id: int) -> dict[str, Any]:
    """
    Vuelve prospectos de la empresa a estado inicial de testing.
    No borra usuarios, campañas, productos ni configuración.
    """
    prospect_ids = list(
        db.scalars(select(Prospect.id).where(Prospect.company_id == company_id)).all()
    )
    if not prospect_ids:
        return {
            "company_id": company_id,
            "prospects_reset": 0,
            "messages_deleted": 0,
            "meetings_deleted": 0,
            "tasks_deleted": 0,
            "ownership_events_deleted": 0,
            "ai_events_deleted": 0,
            "inbound_receipts_deleted": 0,
        }

    messages_deleted = db.execute(
        delete(OutreachMessage).where(OutreachMessage.prospect_id.in_(prospect_ids))
    ).rowcount or 0

    meetings_deleted = db.execute(
        delete(Meeting).where(Meeting.company_id == company_id)
    ).rowcount or 0

    tasks_deleted = db.execute(
        delete(OutreachTask).where(OutreachTask.company_id == company_id)
    ).rowcount or 0

    ownership_events_deleted = db.execute(
        delete(ProspectOwnershipEvent).where(ProspectOwnershipEvent.company_id == company_id)
    ).rowcount or 0

    ai_events_deleted = db.execute(
        delete(AiDecisionEvent).where(AiDecisionEvent.company_id == company_id)
    ).rowcount or 0

    inbound_receipts_deleted = db.execute(
        delete(InboundAutoReplyReceipt).where(InboundAutoReplyReceipt.company_id == company_id)
    ).rowcount or 0

    prospects = db.scalars(select(Prospect).where(Prospect.company_id == company_id)).all()
    for prospect in prospects:
        prospect.status = _baseline_status(prospect.status)
        prospect.pipeline_stage = PipelineStage.nuevo.value

        prospect.owner_user_id = None
        prospect.previous_owner_user_id = None
        prospect.ownership_status = ProspectOwnershipStatus.libre.value
        prospect.claimed_at = None
        prospect.ownership_cooldown_until = None
        prospect.sequence_completed_at = None

        prospect.commercial_state = COMMERCIAL_PROSPECCION
        prospect.commercial_state_is_testing = False
        prospect.conversation_state = CONV_NONE

        prospect.sequence_started_at = None
        prospect.sequence_playbook_draft = None
        prospect.sequence_touch_log = None
        prospect.playbook_name = None
        prospect.next_touch_at = None
        prospect.sequence_paused = False
        prospect.sequence_group = "contactado"
        prospect.sequence_state = "sin_respuesta"
        prospect.sequence_fired_milestones = "[]"
        prospect.reactivation_sent_at = None
        prospect.defer_resume_at = None
        prospect.ai_paused = False

        prospect.outreach_touch_count = 0
        prospect.last_outbound_at = None
        prospect.last_inbound_at = None
        prospect.objection_type = None
        prospect.objection_detected_at = None
        prospect.interest_level = "low"
        prospect.meeting_nudge_sent_at = None
        prospect.followup_count = 0
        prospect.last_followup_at = None
        prospect.score_reason = None
        prospect.next_best_action = None
        prospect.meeting_suggestion_pending = False

        prospect.preferred_channel = None
        prospect.channel_reason = None
        prospect.linkedin_assisted_draft = None
        prospect.linkedin_assist_status = None
        prospect.linkedin_assist_session_id = None
        prospect.linkedin_last_assisted_at = None
        prospect.linkedin_sdr_marked_sent_at = None
        prospect.gmail_thread_id = None

    db.commit()
    logger.warning(
        "testing_reset company_id=%s prospects=%s messages=%s meetings=%s",
        company_id,
        len(prospects),
        messages_deleted,
        meetings_deleted,
    )
    return {
        "company_id": company_id,
        "prospects_reset": len(prospects),
        "messages_deleted": int(messages_deleted),
        "meetings_deleted": int(meetings_deleted),
        "tasks_deleted": int(tasks_deleted),
        "ownership_events_deleted": int(ownership_events_deleted),
        "ai_events_deleted": int(ai_events_deleted),
        "inbound_receipts_deleted": int(inbound_receipts_deleted),
    }
