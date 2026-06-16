from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductInterpretRequest(BaseModel):
    """Texto largo (pegado o .txt); se resume vía IA antes de persistir."""

    document_text: str = Field(min_length=40, max_length=500_000)


class ProductInterpretRootRequest(BaseModel):
    """Alias de entrada para clientes que llaman `POST /products/interpret`."""

    company_id: int = Field(ge=1)
    raw_text: str = Field(min_length=40, max_length=500_000)


class ProductInterpretRead(BaseModel):
    suggested_name: str
    description: str
    value_proposition: str
    target_notes: str
    benefits: str = ""
    pain_points: str = ""
    objections: str = ""
    recommended_tone: str = ""
    use_cases: str = ""


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    value_proposition: str = ""
    target_notes: str = ""


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    value_proposition: str | None = None
    target_notes: str | None = None
    is_active: bool | None = None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    name: str
    description: str
    value_proposition: str
    target_notes: str
    created_at: datetime
    is_active: bool
