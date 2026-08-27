from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    employee_count: int = Field(ge=0, default=0)
    plan: str = Field(default="starter", max_length=64)


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    employee_count: int
    plan: str = "starter"
    created_at: datetime
