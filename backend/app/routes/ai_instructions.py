from fastapi import APIRouter, Depends, HTTPException, Response

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_company
from app.models import AIInstruction
from app.schemas.ai_instruction import AIInstructionCreate, AIInstructionRead, AIInstructionUpdate
from app.services.ai_behavior_policy import BEHAVIOR_INSTRUCTION_TITLE, is_behavior_system_instruction

router = APIRouter(prefix="/companies", tags=["ai-instructions"])


def _get_instruction_for_company(
    db: Session, company_id: int, instruction_id: int
) -> AIInstruction:
    row = db.get(AIInstruction, instruction_id)
    if row is None or row.company_id != company_id:
        raise HTTPException(status_code=404, detail="Instrucción no encontrada")
    return row


@router.get("/{company_id}/ai-instructions", response_model=list[AIInstructionRead])
def list_ai_instructions(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> list[AIInstructionRead]:
    rows = db.scalars(
        select(AIInstruction)
        .where(AIInstruction.company_id == company_id)
        .order_by(AIInstruction.created_at.desc())
    ).all()
    return [AIInstructionRead.model_validate(r) for r in rows]


@router.post("/{company_id}/ai-instructions", response_model=AIInstructionRead, status_code=201)
def create_ai_instruction(
    company_id: int,
    payload: AIInstructionCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> AIInstructionRead:
    if is_behavior_system_instruction(payload.title):
        raise HTTPException(
            status_code=400,
            detail=f"Usá el panel «Comportamiento del SDR» en Educación IA (no crear «{BEHAVIOR_INSTRUCTION_TITLE}» manualmente).",
        )
    row = AIInstruction(
        company_id=company_id,
        title=payload.title.strip(),
        content=payload.content.strip(),
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return AIInstructionRead.model_validate(row)


@router.patch("/{company_id}/ai-instructions/{instruction_id}", response_model=AIInstructionRead)
def update_ai_instruction(
    company_id: int,
    instruction_id: int,
    payload: AIInstructionUpdate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> AIInstructionRead:
    row = _get_instruction_for_company(db, company_id, instruction_id)
    if is_behavior_system_instruction(row.title):
        raise HTTPException(
            status_code=400,
            detail="La instrucción de comportamiento del SDR se edita desde el panel dedicado en Educación IA.",
        )
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        if is_behavior_system_instruction(str(data["title"])):
            raise HTTPException(status_code=400, detail="Ese título está reservado para el sistema.")
        row.title = str(data["title"]).strip()
    if "content" in data and data["content"] is not None:
        row.content = str(data["content"]).strip()
    if "is_active" in data:
        row.is_active = bool(data["is_active"])
    db.commit()
    db.refresh(row)
    return AIInstructionRead.model_validate(row)


@router.delete("/{company_id}/ai-instructions/{instruction_id}", status_code=204)
def delete_ai_instruction(
    company_id: int,
    instruction_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> Response:
    row = _get_instruction_for_company(db, company_id, instruction_id)
    if is_behavior_system_instruction(row.title):
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar la política de comportamiento; editá los valores en el panel SDR.",
        )
    db.delete(row)
    db.commit()
    return Response(status_code=204)
