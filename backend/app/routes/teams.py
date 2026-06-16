from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user, require_permission
from app.core.permissions import Permission, normalize_role
from app.database.session import get_db
from app.deps import get_company
from app.models.enums import UserRole
from app.models.team import Team
from app.models.user import User
from app.schemas.team import (
    EquipoCapabilities,
    EquipoWorkspaceRead,
    TeamCreate,
    TeamMemberMetrics,
    TeamMemberRead,
    TeamRead,
    TeamUpdate,
)
from app.services.team_metrics import user_team_metrics

router = APIRouter(tags=["teams"])


def _team_read(team: Team, member_count: int) -> TeamRead:
    return TeamRead(
        id=team.id,
        company_id=team.company_id,
        name=team.name,
        description=team.description,
        member_count=member_count,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


def _member_counts(db: Session, company_id: int) -> dict[int, int]:
    rows = db.execute(
        select(User.team_id, func.count(User.id))
        .where(User.company_id == company_id, User.team_id.isnot(None))
        .group_by(User.team_id)
    ).all()
    return {int(r[0]): int(r[1]) for r in rows if r[0] is not None}


def _team_name_map(db: Session, company_id: int) -> dict[int, str]:
    teams = db.scalars(select(Team).where(Team.company_id == company_id)).all()
    return {t.id: t.name for t in teams}


def _format_member(
    u: User,
    *,
    actor: User,
    team_names: dict[int, str],
    metrics_map: dict[int, dict[str, int]] | None,
    show_email_all: bool,
    show_metrics: bool,
) -> TeamMemberRead:
    is_self = u.id == actor.id
    email: str | None = u.email
    if not show_email_all and not is_self:
        email = None
    metrics = None
    if show_metrics and metrics_map is not None:
        m = metrics_map.get(u.id, {})
        metrics = TeamMemberMetrics(
            prospects_claimed=m.get("prospects_claimed", 0),
            active_sequences=m.get("active_sequences", 0),
            active_campaigns=m.get("active_campaigns", 0),
        )
    return TeamMemberRead(
        id=u.id,
        company_id=u.company_id,
        team_id=u.team_id,
        team_name=team_names.get(u.team_id) if u.team_id else None,
        first_name=u.first_name,
        last_name=u.last_name,
        name=u.name,
        email=email,
        role=u.role,
        is_active=u.is_active,
        metrics=metrics,
        is_self=is_self,
    )


def _capabilities_for_role(role: UserRole) -> EquipoCapabilities:
    if role == UserRole.sdr:
        return EquipoCapabilities()
    if role == UserRole.manager:
        return EquipoCapabilities(show_email_all=True, show_metrics=True)
    return EquipoCapabilities(
        can_create_team=True,
        can_edit_team=True,
        can_assign_team=True,
        can_change_role=True,
        can_toggle_active=True,
        show_email_all=True,
        show_metrics=True,
        show_all_teams=True,
    )


@router.get("/companies/{company_id}/equipo", response_model=EquipoWorkspaceRead)
def get_equipo_workspace(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    actor: User = Depends(get_current_user),
) -> EquipoWorkspaceRead:
    role = normalize_role(actor.role)
    caps = _capabilities_for_role(role)
    counts = _member_counts(db, company_id)
    team_names = _team_name_map(db, company_id)

    if role == UserRole.gerente:
        teams = db.scalars(select(Team).where(Team.company_id == company_id).order_by(Team.name)).all()
        members = db.scalars(
            select(User).where(User.company_id == company_id).order_by(User.name)
        ).all()
        team_reads = [_team_read(t, counts.get(t.id, 0)) for t in teams]
    elif actor.team_id:
        team = db.get(Team, actor.team_id)
        if team is None or team.company_id != company_id:
            team_reads = []
            members = []
        else:
            team_reads = [_team_read(team, counts.get(team.id, 0))]
            members = db.scalars(
                select(User)
                .where(User.company_id == company_id, User.team_id == actor.team_id)
                .order_by(User.name)
            ).all()
    else:
        team_reads = []
        members = [actor] if actor.company_id == company_id else []

    metrics_map = None
    if caps.show_metrics and members:
        metrics_map = user_team_metrics(db, company_id=company_id, user_ids=[m.id for m in members])

    member_reads = [
        _format_member(
            u,
            actor=actor,
            team_names=team_names,
            metrics_map=metrics_map,
            show_email_all=caps.show_email_all,
            show_metrics=caps.show_metrics,
        )
        for u in members
    ]

    return EquipoWorkspaceRead(
        viewer_role=role.value,
        teams=team_reads,
        members=member_reads,
        capabilities=caps,
    )


@router.post("/companies/{company_id}/teams", response_model=TeamRead, status_code=201)
def create_team(
    company_id: int,
    payload: TeamCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    _actor: User = Depends(require_permission(Permission.TEAM_CREATE)),
) -> TeamRead:
    name = payload.name.strip()
    exists = db.scalars(
        select(Team).where(Team.company_id == company_id, Team.name == name)
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Ya existe un equipo con ese nombre")

    team = Team(
        company_id=company_id,
        name=name,
        description=(payload.description or "").strip() or None,
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return _team_read(team, 0)


@router.patch("/teams/{team_id}", response_model=TeamRead)
def update_team(
    team_id: int,
    payload: TeamUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission(Permission.TEAM_EDIT)),
) -> TeamRead:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Equipo no encontrado")
    if actor.company_id != team.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este equipo")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        new_name = data["name"].strip()
        dup = db.scalars(
            select(Team).where(
                Team.company_id == team.company_id,
                Team.name == new_name,
                Team.id != team.id,
            )
        ).first()
        if dup:
            raise HTTPException(status_code=409, detail="Ya existe un equipo con ese nombre")
        team.name = new_name
    if "description" in data:
        team.description = (data["description"] or "").strip() or None

    db.commit()
    db.refresh(team)
    count = db.scalar(
        select(func.count(User.id)).where(User.team_id == team.id)
    ) or 0
    return _team_read(team, int(count))
