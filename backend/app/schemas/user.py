from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole


class UserCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)
    role: UserRole
    team_id: int | None = None


class UserUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=128)
    last_name: str | None = Field(default=None, min_length=1, max_length=128)
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=6, max_length=256)
    team_id: int | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    team_id: int | None = None
    team_name: str | None = None
    first_name: str
    last_name: str
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserReadWithCredit(UserRead):
    allocated_balance: int | None = None
    used_balance: int | None = None
    available_balance: int | None = None
