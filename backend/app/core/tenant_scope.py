"""Aislamiento multi-tenant: respuestas que no filtran existencia de recursos ajenos."""

from __future__ import annotations

from fastapi import HTTPException

from app.models.user import User


def user_belongs_to_company(user: User, company_id: int) -> bool:
    return int(user.company_id) == int(company_id)


def deny_cross_tenant(*, user: User, resource_company_id: int, not_found_detail: str) -> None:
    """403 → 404 cuando el recurso pertenece a otra empresa (anti-enumeración)."""
    if not user_belongs_to_company(user, resource_company_id):
        raise HTTPException(status_code=404, detail=not_found_detail)


def assert_user_in_company(
    db,
    *,
    company_id: int,
    user_id: int,
    not_found_detail: str = "Usuario no encontrado en esta empresa",
) -> User:
    """El usuario del path debe existir y pertenecer a la empresa del path."""
    row = db.get(User, int(user_id))
    if row is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
    deny_cross_tenant(
        user=row,
        resource_company_id=int(company_id),
        not_found_detail=not_found_detail,
    )
    return row
