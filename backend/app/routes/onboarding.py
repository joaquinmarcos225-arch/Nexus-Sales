from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import normalize_role
from app.core.security import create_access_token
from app.database.session import get_db
from app.schemas.onboarding import WorkspaceSignupRequest, WorkspaceSignupResponse
from app.services.credit_plans import credits_for_plan, normalize_plan_key
from app.services.onboarding import OnboardingError, register_workspace

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("/workspace", response_model=WorkspaceSignupResponse, status_code=status.HTTP_201_CREATED)
def signup_workspace(
    payload: WorkspaceSignupRequest,
    db: Session = Depends(get_db),
) -> WorkspaceSignupResponse:
    """Alta de empresa + usuario directora. Requiere NEXUS_ALLOW_WORKSPACE_SIGNUP=1."""
    try:
        company, user = register_workspace(
            db,
            company_name=payload.company_name,
            employee_count=payload.employee_count,
            plan=payload.plan,
            first_name=payload.first_name,
            last_name=payload.last_name,
            email=str(payload.email),
            password=payload.password,
        )
        db.commit()
        db.refresh(company)
        db.refresh(user)
    except OnboardingError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

    role = normalize_role(user.role)
    token = create_access_token(user_id=user.id, company_id=user.company_id, role=role.value)
    plan_key = normalize_plan_key(company.plan)
    return WorkspaceSignupResponse(
        access_token=token,
        company_id=company.id,
        user_id=user.id,
        company_name=company.name,
        plan=plan_key,
        plan_credits=credits_for_plan(plan_key),
    )
