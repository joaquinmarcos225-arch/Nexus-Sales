from pydantic import BaseModel, Field


class TestingResetRead(BaseModel):
    company_id: int
    prospects_reset: int = 0
    messages_deleted: int = 0
    meetings_deleted: int = 0
    tasks_deleted: int = 0
    ownership_events_deleted: int = 0
    ai_events_deleted: int = 0
    inbound_receipts_deleted: int = 0
    detail: str = Field(default="Entorno de pruebas reiniciado.")


class TestingResetAvailabilityRead(BaseModel):
    enabled: bool = False
    reason: str = ""
