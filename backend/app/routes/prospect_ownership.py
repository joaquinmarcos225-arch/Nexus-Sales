from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth.deps import RequireProspectReassign, get_current_user, get_company_for_user
from app.core.permissions import Permission, has_permission, is_company_admin, normalize_role
from app.database.session import get_db
from app.deps import get_prospect
from app.models.enums import UserRole
from app.models.prospect import Prospect
from app.models.user import User
from app.schemas.prospect import (
    CommercialSummaryRead,
    ProspectCapabilities,
    ProspectRead,
    ProspectReassignRequest,
    ProspectsWorkspaceRead,
)
from app.services import prospect_ownership as own
from app.services import prospect_commercial_state as pcs
from app.services import prospect_sequence as pseq

router = APIRouter(tags=["prospect-ownership"])


def _owner_name(prospect: Prospect) -> str | None:
    owner = prospect.owner
    if owner is None:
        return None
    return owner.name


def _owner_team_name(prospect: Prospect) -> str | None:
    owner = prospect.owner
    if owner is None or owner.team is None:
        return None
    return owner.team.name


def _serialize_owned(
    prospect: Prospect,
    viewer: User,
    db: Session,
    *,
    include_testing: bool = False,
) -> ProspectRead:
    from app.routes.prospects import _serialize

    readiness = pseq.assess_outreach_readiness(db, prospect=prospect)
    base = _serialize(prospect).model_dump()
    base.update(
        {
            "owner_user_id": prospect.owner_user_id,
            "owner_name": _owner_name(prospect),
            "owner_team_name": _owner_team_name(prospect),
            "ownership_status": own.effective_ownership_status(prospect),
            "claimed_at": prospect.claimed_at,
            "sequence_completed_at": prospect.sequence_completed_at,
            "ownership_cooldown_until": prospect.ownership_cooldown_until,
            "previous_owner_user_id": prospect.previous_owner_user_id,
            "last_sequence_label": own.last_sequence_label(prospect),
            "released_at": own.release_at(prospect),
            "can_act": own.can_act_on_prospect(viewer, prospect),
            "can_claim": own.can_claim_prospect(viewer, prospect),
            "can_release": own.can_release_prospect(viewer, prospect),
            "can_reassign": own.can_reassign_prospect(viewer, prospect),
            **pseq.sequence_tracking_fields(prospect),
            **pseq.outreach_action_flags(viewer, prospect, readiness=readiness),
            **pcs.commercial_fields(prospect, db=db, include_testing=include_testing),
        }
    )
    return ProspectRead.model_validate(base)


def _capabilities_for_viewer(user: User) -> ProspectCapabilities:
    role = normalize_role(user.role)
    return ProspectCapabilities(
        can_configure_rules=has_permission(role, Permission.PROSPECT_RULES),
    )


@router.get("/companies/{company_id}/prospects/ownership", response_model=ProspectsWorkspaceRead)
def list_company_prospects_with_ownership(
    company_id: int,
    include_testing: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _company=Depends(get_company_for_user),
) -> ProspectsWorkspaceRead:
    rows = db.scalars(
        select(Prospect)
        .where(Prospect.company_id == company_id)
        .options(joinedload(Prospect.owner).joinedload(User.team))
        .order_by(Prospect.id.desc())
    ).all()
    for p in rows:
        own.refresh_ownership_if_expired(p, db)
        pseq.reconcile_sequence_state(db, p, commit=False)
    db.commit()
    rows = own.prospects_visible_to_viewer(user, rows)
    role = normalize_role(user.role)
    serialized = [_serialize_owned(p, user, db, include_testing=include_testing) for p in rows]
    summary_data = pcs.build_commercial_summary(
        [s.model_dump() for s in serialized],
        include_testing=include_testing,
    )
    return ProspectsWorkspaceRead(
        viewer_role=role.value,
        prospects=serialized,
        capabilities=_capabilities_for_viewer(user),
        commercial_summary=CommercialSummaryRead.model_validate(summary_data),
    )


@router.post("/prospects/{prospect_id}/claim", response_model=ProspectRead)
def claim_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectRead:
    if user.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    own.claim_prospect(db, user=user, prospect=prospect)
    db.refresh(prospect, attribute_names=["owner"])
    if prospect.owner:
        db.refresh(prospect.owner, attribute_names=["team"])
    return _serialize_owned(prospect, user, db)


@router.post("/prospects/{prospect_id}/release", response_model=ProspectRead)
def release_prospect(
    prospect_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectRead:
    if not is_company_admin(user.role):
        raise HTTPException(status_code=403, detail="Solo Director/Owner puede liberar prospectos")
    if not own.can_release_prospect(user, prospect):
        raise HTTPException(status_code=403, detail="No podés liberar este prospecto")
    own.release_prospect(db, user=user, prospect=prospect)
    db.refresh(prospect, attribute_names=["owner"])
    return _serialize_owned(prospect, user, db)


@router.post("/prospects/{prospect_id}/reassign", response_model=ProspectRead)
def reassign_prospect(
    prospect_id: int,
    payload: ProspectReassignRequest,
    actor: RequireProspectReassign,
    db: Session = Depends(get_db),
    prospect: Prospect = Depends(get_prospect),
) -> ProspectRead:
    if not is_company_admin(actor.role):
        raise HTTPException(status_code=403, detail="Solo Director/Owner puede reasignar prospectos")
    if actor.company_id != prospect.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este prospecto")
    own.reassign_prospect(db, actor=actor, prospect=prospect, to_user_id=payload.to_user_id)
    db.refresh(prospect, attribute_names=["owner"])
    if prospect.owner:
        db.refresh(prospect.owner, attribute_names=["team"])
    return _serialize_owned(prospect, actor, db)
