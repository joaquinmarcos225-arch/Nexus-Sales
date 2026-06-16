from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class TeamRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    name: str
    description: str | None
    member_count: int = 0
    created_at: datetime
    updated_at: datetime


class TeamMemberMetrics(BaseModel):
    prospects_claimed: int = 0
    active_sequences: int = 0
    active_campaigns: int = 0


class TeamMemberRead(BaseModel):
    id: int
    company_id: int
    team_id: int | None
    team_name: str | None
    first_name: str
    last_name: str
    name: str
    email: str | None = None
    role: str
    is_active: bool
    metrics: TeamMemberMetrics | None = None
    is_self: bool = False


class EquipoCapabilities(BaseModel):
    can_create_team: bool = False
    can_edit_team: bool = False
    can_assign_team: bool = False
    can_change_role: bool = False
    can_toggle_active: bool = False
    show_email_all: bool = False
    show_metrics: bool = False
    show_all_teams: bool = False


class EquipoWorkspaceRead(BaseModel):
    viewer_role: str
    teams: list[TeamRead]
    members: list[TeamMemberRead]
    capabilities: EquipoCapabilities
