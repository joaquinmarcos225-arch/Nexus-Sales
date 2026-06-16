from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.deps import get_company
from app.schemas.ai_behavior_policy import (
    POLICY_FIELD_HELP,
    AiBehaviorPolicyFieldHelp,
    AiBehaviorPolicyRead,
    AiBehaviorPolicyUpdate,
)
from app.services.ai_behavior_policy import load_behavior_policy, save_behavior_policy

router = APIRouter(prefix="/companies", tags=["ai-behavior-policy"])


@router.get("/{company_id}/ai-behavior-policy", response_model=AiBehaviorPolicyRead)
def get_ai_behavior_policy(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> AiBehaviorPolicyRead:
    return load_behavior_policy(db, company_id).to_schema()


@router.get("/{company_id}/ai-behavior-policy/fields", response_model=list[AiBehaviorPolicyFieldHelp])
def list_ai_behavior_policy_fields(
    company_id: int,
    _company=Depends(get_company),
) -> list[AiBehaviorPolicyFieldHelp]:
    del company_id
    return POLICY_FIELD_HELP


@router.put("/{company_id}/ai-behavior-policy", response_model=AiBehaviorPolicyRead)
def put_ai_behavior_policy(
    company_id: int,
    payload: AiBehaviorPolicyUpdate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
) -> AiBehaviorPolicyRead:
    policy = save_behavior_policy(db, company_id, payload)
    return policy.to_schema()
