from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import RequireUserCreate, get_current_user
from app.core.security import hash_password
from app.database.session import get_db
from app.deps import get_company
from app.models.enums import UserRole
from app.models.seller_allocation import SellerCreditAllocation
from app.models.team import Team
from app.models.user import User
from app.schemas.user import UserCreate, UserReadWithCredit, UserUpdate

router = APIRouter(tags=["users"])


def _user_dict(u: User, team_names: dict[int, str] | None = None) -> dict:
    team_name = None
    if u.team_id and team_names:
        team_name = team_names.get(u.team_id)
    elif u.team_id and u.team is not None:
        team_name = u.team.name
    return {
        "id": u.id,
        "company_id": u.company_id,
        "team_id": u.team_id,
        "team_name": team_name,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "name": u.name,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at,
        "updated_at": u.updated_at,
    }


@router.get("/companies/{company_id}/users", response_model=list[UserReadWithCredit])
def list_users(
    company_id: int,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    _user: User = Depends(get_current_user),
) -> list[UserReadWithCredit]:
    users = db.scalars(select(User).where(User.company_id == company_id).order_by(User.id)).all()
    team_names = {
        t.id: t.name
        for t in db.scalars(select(Team).where(Team.company_id == company_id)).all()
    }
    alloc_map = {
        row.seller_id: row
        for row in db.scalars(
            select(SellerCreditAllocation).where(SellerCreditAllocation.company_id == company_id)
        ).all()
    }
    out: list[UserReadWithCredit] = []
    for u in users:
        extra: dict = {}
        if u.role == UserRole.sdr.value and u.id in alloc_map:
            a = alloc_map[u.id]
            extra["allocated_balance"] = int(a.allocated_balance)
            extra["used_balance"] = int(a.used_balance)
            extra["available_balance"] = int(a.allocated_balance) - int(a.used_balance)
        elif u.role == UserRole.sdr.value:
            extra["allocated_balance"] = 0
            extra["used_balance"] = 0
            extra["available_balance"] = 0
        out.append(UserReadWithCredit.model_validate({**_user_dict(u, team_names), **extra}))
    return out


@router.post("/companies/{company_id}/users", response_model=UserReadWithCredit, status_code=201)
def create_user(
    company_id: int,
    payload: UserCreate,
    db: Session = Depends(get_db),
    _company=Depends(get_company),
    _actor: User = Depends(RequireUserCreate),
) -> UserReadWithCredit:
    exists = db.scalars(
        select(User).where(User.company_id == company_id, User.email == str(payload.email))
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email en la empresa")

    team_id = payload.team_id
    if team_id is not None:
        team = db.get(Team, team_id)
        if team is None or team.company_id != company_id:
            raise HTTPException(status_code=400, detail="Equipo inválido para esta empresa")

    user = User(
        company_id=company_id,
        team_id=team_id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        name=f"{payload.first_name.strip()} {payload.last_name.strip()}".strip(),
        email=str(payload.email).strip().lower(),
        password_hash=hash_password(payload.password),
        role=payload.role.value,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    extra: dict = {}
    if user.role == UserRole.sdr.value:
        extra = {"allocated_balance": 0, "used_balance": 0, "available_balance": 0}
    return UserReadWithCredit.model_validate({**_user_dict(user), **extra})


@router.patch("/users/{user_id}", response_model=UserReadWithCredit)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
) -> UserReadWithCredit:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if actor.company_id != user.company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a este usuario")

    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] is not None:
        from app.core.permissions import normalize_role
        from app.models.enums import UserRole as UR

        if normalize_role(actor.role) != UR.gerente:
            raise HTTPException(status_code=403, detail="Solo Gerente puede cambiar roles")
        user.role = data["role"].value
        del data["role"]
    if "password" in data and data["password"]:
        user.password_hash = hash_password(data["password"])
        del data["password"]
    if "is_active" in data and data["is_active"] is not None:
        from app.core.permissions import normalize_role
        from app.models.enums import UserRole as UR

        if normalize_role(actor.role) != UR.gerente:
            raise HTTPException(status_code=403, detail="Solo Gerente puede activar/desactivar usuarios")
        user.is_active = data["is_active"]
    if "first_name" in data and data["first_name"]:
        user.first_name = data["first_name"].strip()
    if "last_name" in data and data["last_name"]:
        user.last_name = data["last_name"].strip()
    if "team_id" in data:
        from app.core.permissions import normalize_role
        from app.models.enums import UserRole as UR

        if normalize_role(actor.role) != UR.gerente:
            raise HTTPException(status_code=403, detail="Solo Gerente puede asignar equipos")
        new_team_id = data["team_id"]
        if new_team_id is not None:
            team = db.get(Team, new_team_id)
            if team is None or team.company_id != user.company_id:
                raise HTTPException(status_code=400, detail="Equipo inválido para esta empresa")
        user.team_id = new_team_id
    user.sync_display_name()
    db.commit()
    db.refresh(user)
    extra: dict = {}
    if user.role == UserRole.sdr.value:
        extra = {"allocated_balance": 0, "used_balance": 0, "available_balance": 0}
    return UserReadWithCredit.model_validate({**_user_dict(user), **extra})
