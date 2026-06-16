from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AutopilotStatus


class AutopilotCycleStats(BaseModel):
    processed: int = 0
    messages_generated: int = 0
    responses_simulated: int = 0
    followups_generated: int = 0
    tasks_created: int = 0
    meetings_created: int = 0
    interested_detected: int = 0


class AutopilotCycleRead(BaseModel):
    campaign_id: int
    autopilot_status: AutopilotStatus
    executed_at: datetime
    stats: AutopilotCycleStats
    log: list[str]
