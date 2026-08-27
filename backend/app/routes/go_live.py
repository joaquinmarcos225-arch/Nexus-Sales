"""Readiness go-live — servidor y workspace."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.database.session import get_db
from app.deps import get_company
from app.models.user import User
from app.services.go_live import assess_company_go_live

router = APIRouter(tags=["go-live"])


@router.get("/companies/{company_id}/go-live")
def company_go_live(
    company_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
    _company=Depends(get_company),
) -> dict:
    """Checks del workspace + servidor para arrancar a vender."""
    from app.services.go_live import assess_server_go_live

    server = assess_server_go_live()
    workspace = assess_company_go_live(db, company_id)
    pending = int(server.get("pending_count") or 0) + int(workspace.get("pending_count") or 0)
    return {
        "ready": bool(server.get("prod_ready")) and workspace.get("ready"),
        "pending_count": pending,
        "server": server,
        "workspace": workspace,
    }
