from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, get_company_for_user
from app.core.permissions import is_company_admin, normalize_role
from app.database.session import get_db
from app.deps import get_prospect
from app.models.enums import UserRole
from app.models.prospect import Prospect
from app.models.user import User
from app.routes.prospect_ownership import _serialize_owned
from app.schemas.prospect import ProspectRead
from app.schemas.prospect_sequence import (
    ActiveSequenceSummaryRead,
    ActiveSequencesWorkspaceRead,
    ExecuteTouchRead,
    ProspectEnrichRead,
    ProspectOutreachContextRead,
    SequencePreviewRead,
    SimulateSequenceResponseBody,
    SimulateSequenceResponseRead,
    SequenceTrackingRead,
    StartSequenceRead,
)
from app.services import prospect_ownership as own
from app.services import prospect_sequence as seq

router = APIRouter(tags=["prospect-sequence"])


@router.get("/prospects/{prospect_id}/outreach-context", response_model=ProspectOutreachContextRead)
def get_outreach_context(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProspectOutreachContextRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    ctx = seq.build_outreach_context(db, prospect=prospect, user=user)
    return ProspectOutreachContextRead.model_validate(ctx)


@router.post("/prospects/{prospect_id}/sequence/generate-preview", response_model=SequencePreviewRead)
def generate_sequence_preview(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    force_regenerate: bool = False,
) -> SequencePreviewRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    data = seq.generate_sequence_preview(
        db,
        user=user,
        prospect=prospect,
        force_regenerate=force_regenerate,
    )
    return SequencePreviewRead.model_validate(data)


@router.post("/prospects/{prospect_id}/sequence/reset-draft")
def reset_sequence_draft(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    if not seq.can_manage_outreach(user, prospect):
        raise HTTPException(status_code=403, detail="No podés modificar la secuencia de este prospecto")
    debug = seq.clear_sequence_draft(db, prospect=prospect)
    return {"ok": True, "sequence_debug": debug}


@router.get("/prospects/{prospect_id}/sequence/preview", response_model=SequencePreviewRead)
def get_sequence_preview(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SequencePreviewRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    seq.reconcile_sequence_state(db, prospect, commit=True)
    if not seq.can_view_sequence(user, prospect):
        debug = seq.build_sequence_debug(prospect)
        raise HTTPException(
            status_code=403,
            detail={
                "message": "No podés ver la secuencia de este prospecto",
                "sequence_debug": debug,
            },
        )
    data = seq.get_saved_sequence_preview(prospect)
    return SequencePreviewRead.model_validate(data)


@router.get("/prospects/{prospect_id}/sequence/tracking", response_model=SequenceTrackingRead)
def get_sequence_tracking(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SequenceTrackingRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    status = own.effective_ownership_status(prospect)
    allowed = seq.can_view_sequence(user, prospect) or (
        status == "en_secuencia" and seq.can_manage_outreach(user, prospect)
    )
    if not allowed and normalize_role(user.role) != UserRole.manager and not is_company_admin(user.role):
        raise HTTPException(status_code=403, detail="No podés ver el seguimiento de este prospecto")
    seq.reconcile_sequence_state(db, prospect, commit=True)
    data = seq.build_sequence_tracking(db, prospect=prospect)
    data["sequence_debug"] = seq.build_sequence_debug(prospect)
    return SequenceTrackingRead.model_validate(data)


@router.get(
    "/companies/{company_id}/prospects/active-sequences",
    response_model=ActiveSequencesWorkspaceRead,
)
def list_active_sequences(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> ActiveSequencesWorkspaceRead:
    rows = seq.list_active_sequences(db, company_id=company_id, user=user)
    return ActiveSequencesWorkspaceRead(
        sequences=[ActiveSequenceSummaryRead.model_validate(r) for r in rows]
    )


@router.post("/prospects/{prospect_id}/sequence/start", response_model=StartSequenceRead)
def start_prospect_sequence(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    consume_credit: bool = False,
) -> StartSequenceRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    if consume_credit:
        from app.services.credits import CreditError, consume_sequence_individual_credit

        try:
            consume_sequence_individual_credit(
                db,
                int(prospect.company_id),
                int(user.id),
                actor_user_id=int(user.id),
            )
        except CreditError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    # start_prospect_sequence hace commit (incluye el crédito si se consumió arriba)
    seq.start_prospect_sequence(db, user=user, prospect=prospect)
    next_at, next_label = seq.compute_next_touch(prospect)
    return StartSequenceRead(
        prospect_id=prospect.id,
        ownership_status=prospect.ownership_status,
        sequence_started_at=prospect.sequence_started_at,
        next_touch_at=next_at,
        next_touch_label=next_label,
        playbook_name=prospect.playbook_name,
    )


@router.post("/prospects/{prospect_id}/sequence/touches/{day}/execute", response_model=ExecuteTouchRead)
def execute_sequence_touch(
    prospect_id: int,
    day: int,
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExecuteTouchRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    data = seq.execute_sequence_touch(db, user=user, prospect=prospect, day=day)
    return ExecuteTouchRead.model_validate(data)


@router.post("/prospects/{prospect_id}/sequence/touches/{day}/skip", response_model=ExecuteTouchRead)
def skip_sequence_touch(
    prospect_id: int,
    day: int,
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExecuteTouchRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    data = seq.skip_sequence_touch(db, user=user, prospect=prospect, day=day)
    return ExecuteTouchRead.model_validate(data)


@router.post(
    "/prospects/{prospect_id}/sequence/touches/{day}/mark-sent",
    response_model=ExecuteTouchRead,
)
def mark_sequence_gmail_touch_sent(
    prospect_id: int,
    day: int,
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExecuteTouchRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    data = seq.mark_sequence_gmail_touch_sent(db, user=user, prospect=prospect, day=day)
    return ExecuteTouchRead.model_validate(data)


@router.post(
    "/prospects/{prospect_id}/sequence/simulate-response",
    response_model=SimulateSequenceResponseRead,
)
def simulate_sequence_response(
    prospect_id: int,
    body: SimulateSequenceResponseBody,
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimulateSequenceResponseRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    data = seq.simulate_sequence_response(
        db,
        user=user,
        prospect=prospect,
        message=body.message,
        channel=body.channel,
    )
    return SimulateSequenceResponseRead.model_validate(data)


@router.post("/prospects/{prospect_id}/enrich", response_model=ProspectEnrichRead)
def enrich_prospect(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProspectEnrichRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    data = seq.enrich_prospect_contact(db, user=user, prospect=prospect)
    return ProspectEnrichRead.model_validate(data)


@router.get("/prospects/{prospect_id}/outreach-workspace", response_model=ProspectRead)
def get_prospect_outreach_workspace(
    prospect: Prospect = Depends(get_prospect),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProspectRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    return _serialize_owned(prospect, user, db)
