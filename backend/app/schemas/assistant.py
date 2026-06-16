from typing import Literal

from pydantic import BaseModel, Field


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class AssistantChatRequest(BaseModel):
    company_id: int = Field(ge=1)
    messages: list[ChatTurn] = Field(min_length=1, max_length=40)


class AssistantChatResponse(BaseModel):
    reply: str
