from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.company import Company
from app.schemas.assistant import AssistantChatRequest, AssistantChatResponse
from app.services import openai_service
from app.services.ai_instruction_context import active_instruction_blob
from app.services.assistant_context import build_company_snapshot

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/chat", response_model=AssistantChatResponse)
def assistant_chat(payload: AssistantChatRequest, db: Session = Depends(get_db)) -> AssistantChatResponse:
    company = db.get(Company, payload.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    snapshot = build_company_snapshot(db, payload.company_id)
    edu = active_instruction_blob(db, payload.company_id)
    turns = [{"role": m.role, "content": m.content} for m in payload.messages]
    reply = openai_service.assistant_reply(
        company_snapshot=snapshot,
        education=edu,
        chat_turns=turns,
    )
    return AssistantChatResponse(reply=reply)
