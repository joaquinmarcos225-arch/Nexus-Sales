from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AIInstructionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)


class AIInstructionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class AIInstructionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    title: str
    content: str
    is_active: bool
    created_at: datetime
