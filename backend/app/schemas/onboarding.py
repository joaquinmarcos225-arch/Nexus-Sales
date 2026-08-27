from pydantic import BaseModel, EmailStr, Field


class WorkspaceSignupRequest(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    employee_count: int = Field(ge=0, default=0)
    plan: str = Field(default="starter", max_length=64)
    first_name: str = Field(min_length=1, max_length=128)
    last_name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    password: str = Field(min_length=6, max_length=256)


class WorkspaceSignupResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    company_id: int
    user_id: int
    company_name: str
    plan: str
    plan_credits: int
