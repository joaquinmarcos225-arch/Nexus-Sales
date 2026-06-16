"""Lógica de propiedad de prospectos entre SDRs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission, normalize_role
from app.models.enums import ProspectOwnershipStatus, UserRole
from app.models.prospect import Prospect
from app.models.prospect_ownership_event import ProspectOwnershipEvent
from app.models.user import User

OWNERSHIP_COOLDOWN_DAYS = 20

LOCKED_STATUSES = frozenset(
    {
        ProspectOwnershipStatus.tomado.value,
        ProspectOwnershipStatus.en_secuencia.value,
        ProspectOwnershipStatus.secuencia_finalizada.value,
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def refresh_ownership_if_expired(prospect: Prospect, db: Session) -> None:
    """Libera prospectos cuyo cooldown de 20 días ya venció."""
    if prospect.ownership_status != ProspectOwnershipStatus.secuencia_finalizada.value:
        return
    until = prospect.ownership_cooldown_until
    if until is None:
        return
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if _now() >= until:
        prev_owner = prospect.owner_user_id
        prospect.previous_owner_user_id = prev_owner
        prospect.owner_user_id = None
        prospect.ownership_status = ProspectOwnershipStatus.liberado.value
        prospect.claimed_at = None
        prospect.ownership_cooldown_until = None
        db.add(
            ProspectOwnershipEvent(
                company_id=prospect.company_id,
                prospect_id=prospect.id,
                actor_user_id=None,
                from_user_id=prev_owner,
                to_user_id=None,
                action="auto_release_cooldown",
                note=f"Cooldown de {OWNERSHIP_COOLDOWN_DAYS} días vencido",
            )
        )


def effective_ownership_status(prospect: Prospect) -> str:
    return prospect.ownership_status or ProspectOwnershipStatus.libre.value


def is_prospect_locked(prospect: Prospect) -> bool:
    status = effective_ownership_status(prospect)
    if status == ProspectOwnershipStatus.secuencia_finalizada.value:
        until = prospect.ownership_cooldown_until
        if until is not None:
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if _now() < until:
                return True
        return False
    return status in LOCKED_STATUSES


def can_view_prospect(_user: User, prospect: Prospect) -> bool:
    return _user.company_id == prospect.company_id


def prospects_visible_to_viewer(user: User, prospects: list[Prospect]) -> list[Prospect]:
    """Alcance de bandeja: SDR → propios + libres; Manager → equipo; Director → empresa."""
    role = normalize_role(user.role)
    if role == UserRole.gerente:
        return prospects
    if role == UserRole.manager:
        team_id = user.team_id
        if team_id is None:
            return prospects

        def _manager_visible(p: Prospect) -> bool:
            if p.owner_user_id is None:
                return True
            owner = p.owner
            return owner is not None and owner.team_id == team_id

        return [p for p in prospects if _manager_visible(p)]
    if role == UserRole.sdr:
        libre_like = {
            ProspectOwnershipStatus.libre.value,
            ProspectOwnershipStatus.liberado.value,
        }

        def _sdr_visible(p: Prospect) -> bool:
            if p.owner_user_id == user.id:
                return True
            return effective_ownership_status(p) in libre_like

        return [p for p in prospects if _sdr_visible(p)]
    return prospects


def can_act_on_prospect(user: User, prospect: Prospect) -> bool:
    if user.company_id != prospect.company_id:
        return False
    role = normalize_role(user.role)
    if role == UserRole.gerente:
        return False
    if role == UserRole.manager:
        return True
    if role == UserRole.sdr:
        status = effective_ownership_status(prospect)
        if status in (ProspectOwnershipStatus.libre.value, ProspectOwnershipStatus.liberado.value):
            return True
        return prospect.owner_user_id == user.id
    return False


def can_claim_prospect(user: User, prospect: Prospect) -> bool:
    role = normalize_role(user.role)
    if role == UserRole.gerente:
        return False
    if not has_permission(user.role, Permission.PROSPECT_CLAIM):
        return False
    if user.company_id != prospect.company_id:
        return False
    if is_prospect_locked(prospect):
        return False
    status = effective_ownership_status(prospect)
    return status in (ProspectOwnershipStatus.libre.value, ProspectOwnershipStatus.liberado.value)


def assert_company_access(user: User, company_id: int) -> None:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")


def can_release_prospect(user: User, prospect: Prospect) -> bool:
    if normalize_role(user.role) != UserRole.gerente:
        return False
    if user.company_id != prospect.company_id:
        return False
    status = effective_ownership_status(prospect)
    if status == ProspectOwnershipStatus.libre.value:
        return False
    return prospect.owner_user_id is not None or status in LOCKED_STATUSES


def can_reassign_prospect(user: User, prospect: Prospect) -> bool:
    if normalize_role(user.role) != UserRole.gerente:
        return False
    if user.company_id != prospect.company_id:
        return False
    status = effective_ownership_status(prospect)
    if status in (ProspectOwnershipStatus.libre.value, ProspectOwnershipStatus.liberado.value):
        return False
    return prospect.owner_user_id is not None or status in LOCKED_STATUSES


def last_sequence_label(prospect: Prospect) -> str | None:
    if prospect.sequence_completed_at:
        return "Finalizada"
    if (
        prospect.ownership_status == ProspectOwnershipStatus.en_secuencia.value
        or prospect.sequence_started_at
    ):
        group = (prospect.sequence_group or "contactado").replace("_", " ")
        state = (prospect.sequence_state or "sin_respuesta").replace("_", " ")
        return f"{group} · {state}"
    return None


def release_at(prospect: Prospect) -> datetime | None:
    status = effective_ownership_status(prospect)
    if status == ProspectOwnershipStatus.liberado.value:
        return prospect.updated_at
    if status == ProspectOwnershipStatus.secuencia_finalizada.value:
        return prospect.ownership_cooldown_until
    return None


def claim_prospect(db: Session, *, user: User, prospect: Prospect) -> Prospect:
    if normalize_role(user.role) == UserRole.gerente:
        raise HTTPException(status_code=403, detail="Gerente no puede tomar prospectos")
    refresh_ownership_if_expired(prospect, db)
    if not can_claim_prospect(user, prospect):
        raise HTTPException(
            status_code=403,
            detail="No podés tomar este prospecto — está ocupado por otro SDR o en cooldown",
        )
    prev = prospect.owner_user_id
    now = _now()
    prospect.owner_user_id = user.id
    prospect.claimed_at = now
    prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    prospect.ownership_cooldown_until = None
    db.add(
        ProspectOwnershipEvent(
            company_id=prospect.company_id,
            prospect_id=prospect.id,
            actor_user_id=user.id,
            from_user_id=prev,
            to_user_id=user.id,
            action="claim",
        )
    )
    db.commit()
    db.refresh(prospect)
    return prospect


def release_prospect(db: Session, *, user: User, prospect: Prospect) -> Prospect:
    if normalize_role(user.role) != UserRole.gerente:
        raise HTTPException(status_code=403, detail="Solo Gerente puede liberar prospectos")
    assert_company_access(user, prospect.company_id)
    if not can_release_prospect(user, prospect):
        raise HTTPException(status_code=403, detail="No podés liberar este prospecto")
    prev = prospect.owner_user_id
    prospect.previous_owner_user_id = prev
    prospect.owner_user_id = None
    prospect.ownership_status = ProspectOwnershipStatus.liberado.value
    prospect.claimed_at = None
    prospect.ownership_cooldown_until = None
    db.add(
        ProspectOwnershipEvent(
            company_id=prospect.company_id,
            prospect_id=prospect.id,
            actor_user_id=user.id,
            from_user_id=prev,
            to_user_id=None,
            action="release",
        )
    )
    db.commit()
    db.refresh(prospect)
    return prospect


def reassign_prospect(
    db: Session,
    *,
    actor: User,
    prospect: Prospect,
    to_user_id: int,
) -> Prospect:
    if normalize_role(actor.role) != UserRole.gerente:
        raise HTTPException(status_code=403, detail="Solo Gerente puede reasignar prospectos")
    if not has_permission(actor.role, Permission.PROSPECT_REASSIGN):
        raise HTTPException(status_code=403, detail="No tenés permiso para reasignar prospectos")
    assert_company_access(actor, prospect.company_id)
    target = db.get(User, to_user_id)
    if target is None or target.company_id != prospect.company_id:
        raise HTTPException(status_code=404, detail="Usuario destino no encontrado en la empresa")
    if normalize_role(target.role) != UserRole.sdr:
        raise HTTPException(status_code=400, detail="Solo se puede reasignar a un SDR")
    prev = prospect.owner_user_id
    now = _now()
    prospect.owner_user_id = target.id
    prospect.claimed_at = now
    prospect.ownership_status = ProspectOwnershipStatus.tomado.value
    prospect.ownership_cooldown_until = None
    db.add(
        ProspectOwnershipEvent(
            company_id=prospect.company_id,
            prospect_id=prospect.id,
            actor_user_id=actor.id,
            from_user_id=prev,
            to_user_id=target.id,
            action="reassign",
        )
    )
    db.commit()
    db.refresh(prospect)
    return prospect


def mark_sequence_started(db: Session, *, user: User, prospect: Prospect) -> None:
    if normalize_role(user.role) == UserRole.gerente:
        raise HTTPException(status_code=403, detail="Gerente no puede iniciar secuencias")
    if prospect.owner_user_id != user.id and normalize_role(user.role) == UserRole.sdr:
        raise HTTPException(status_code=403, detail="No podés iniciar secuencia en prospecto de otro SDR")
    prospect.ownership_status = ProspectOwnershipStatus.en_secuencia.value
    if prospect.claimed_at is None:
        prospect.claimed_at = _now()
    if prospect.owner_user_id is None:
        prospect.owner_user_id = user.id
    db.commit()


def mark_sequence_completed(db: Session, *, prospect: Prospect) -> None:
    now = _now()
    prospect.ownership_status = ProspectOwnershipStatus.secuencia_finalizada.value
    prospect.sequence_completed_at = now
    prospect.ownership_cooldown_until = now + timedelta(days=OWNERSHIP_COOLDOWN_DAYS)
    db.commit()
