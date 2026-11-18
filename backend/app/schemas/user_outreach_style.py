"""Schema — Mi estilo (perfil SDR)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

MAX_STYLE_EXAMPLES = 10


def _normalize_examples(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        text = (raw or "").strip()
        if not text:
            continue
        out.append(text)
        if len(out) >= MAX_STYLE_EXAMPLES:
            break
    return out


class UserOutreachStyleRead(BaseModel):
    country: str | None = None
    examples_linkedin: list[str] = Field(default_factory=list)
    examples_email: list[str] = Field(default_factory=list)
    examples_whatsapp: list[str] = Field(default_factory=list)
    max_examples: int = MAX_STYLE_EXAMPLES


class UserOutreachStyleUpdate(BaseModel):
    country: str | None = Field(default=None, max_length=64)
    examples_linkedin: list[str] = Field(default_factory=list)
    examples_email: list[str] = Field(default_factory=list)
    examples_whatsapp: list[str] = Field(default_factory=list)

    @field_validator("examples_linkedin", "examples_email", "examples_whatsapp")
    @classmethod
    def cap_examples(cls, values: list[str]) -> list[str]:
        return _normalize_examples(values)
