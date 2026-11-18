from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.permissions import Permission, has_permission, normalize_role, permission_codes_for_role
from app.core.security import safe_decode_access_token
from app.database.session import get_db
from app.models.company import Company
from app.models.user import User

_bearer = HTTPBearer(auto_error=False)


def get_current_user_optional(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User | None:
    if credentials is None or not credentials.credentials:
        return None
    payload = safe_decode_access_token(credentials.credentials)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub") or 0)
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    user: User | None = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_same_company(company_id: int, user: User = Depends(get_current_user)) -> User:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    return user


def get_company_for_user(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Company:
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="No tenés acceso a esta empresa")
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return company


def require_permission(permission: Permission):
    def _dep(user: User = Depends(get_current_user)) -> User:
        if not has_permission(normalize_role(user.role), permission):
            raise HTTPException(
                status_code=403,
                detail=f"Permiso denegado: {permission.value}",
            )
        return user

    return _dep


RequireProductCreate = Annotated[User, Depends(require_permission(Permission.PRODUCT_CREATE))]
RequireProductEdit = Annotated[User, Depends(require_permission(Permission.PRODUCT_EDIT))]
RequireProductDelete = Annotated[User, Depends(require_permission(Permission.PRODUCT_DELETE))]
RequireUserCreate = Annotated[User, Depends(require_permission(Permission.USER_CREATE))]
RequireUserChangeRole = Annotated[User, Depends(require_permission(Permission.USER_CHANGE_ROLE))]
RequireProspectReassign = Annotated[User, Depends(require_permission(Permission.PROSPECT_REASSIGN))]
RequireCompanyConfig = Annotated[User, Depends(require_permission(Permission.COMPANY_CONFIG))]


def user_session_payload(user: User, db: Session | None = None) -> dict:
    from app.services.support import is_nexus_support_ops

    role = normalize_role(user.role)
    company_name = ""
    try:
        company = getattr(user, "company", None)
        if company is not None and getattr(company, "name", None):
            company_name = str(company.name).strip()
        elif db is not None and user.company_id:
            row = db.get(Company, user.company_id)
            if row is not None and row.name:
                company_name = str(row.name).strip()
    except Exception:
        company_name = ""

    avatar_url = None
    if (getattr(user, "avatar_key", None) or "").strip():
        avatar_url = f"/users/{int(user.id)}/avatar"

    return {
        "user_id": user.id,
        "company_id": user.company_id,
        "company_name": company_name,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "name": user.name or "",
        "email": user.email,
        "role": role.value,
        "team_id": getattr(user, "team_id", None),
        "is_active": bool(user.is_active),
        "permissions": permission_codes_for_role(role),
        "is_support_ops": is_nexus_support_ops(user),
        "avatar_url": avatar_url,
        "connection_alerts": _connection_alerts(user, db),
    }


def _connection_alerts(user: User, db: Session | None) -> list[dict]:
    """Canales caídos o nunca conectados — cartel arriba en Nexus con CTA a Integraciones."""
    if db is None:
        return []
    from sqlalchemy import select

    from app.models.connected_account import ConnectedAccount
    from app.models.enums import IntegrationStatus

    alerts: list[dict] = []
    try:
        alerts.extend(_plan_payment_alerts(user, db))
    except Exception:
        pass

    # Support ops no vende con canales propios: no spamear cartel.
    try:
        from app.services.support import is_nexus_support_ops

        if is_nexus_support_ops(user):
            return alerts
    except Exception:
        pass

    integ_url = "/configuracion/integraciones"
    try:
        rows = db.scalars(
            select(ConnectedAccount).where(
                ConnectedAccount.company_id == int(user.company_id),
                ConnectedAccount.user_id == int(user.id),
            )
        ).all()
        by_prov = {str(r.provider or "").lower(): r for r in rows}
        gmail = by_prov.get("gmail")
        calendar = by_prov.get("google_calendar")
        gmail_ok = bool(
            gmail is not None
            and str(gmail.status or "").lower() == IntegrationStatus.connected.value
        )
        calendar_ok = bool(
            calendar is not None
            and str(calendar.status or "").lower() == IntegrationStatus.connected.value
        )
    except Exception:
        gmail_ok = False
        calendar_ok = False

    gmail_dropped = False
    try:
        from sqlalchemy import text

        # SQL directo: este slice de deploy puede no traer el modelo MailboxHealth.
        row = db.execute(
            text(
                "SELECT gmail_down_since FROM mailbox_health "
                "WHERE seller_id = :sid LIMIT 1"
            ),
            {"sid": int(user.id)},
        ).first()
        gmail_dropped = bool(row is not None and row[0] is not None)
    except Exception:
        gmail_dropped = False

    if gmail_dropped or not gmail_ok or not calendar_ok:
        missing: list[str] = []
        if not gmail_ok or gmail_dropped:
            missing.append("Gmail")
        if not calendar_ok:
            missing.append("Calendar")
        if gmail_dropped and gmail_ok:
            title = "Tu Gmail se desconectó"
            detail = "Los mails de tus secuencias van frenados hasta que vuelvas a conectar Google."
            action = "Reconectar"
            tone = "error"
        else:
            title = f"{' · '.join(missing)} sin conectar"
            detail = "Conectalo para mandar mails y agendar reuniones desde Nexus."
            action = "Conectar"
            tone = "error" if (not gmail_ok or gmail_dropped) else "warn"
        alerts.append(
            {
                "key": "google_channels",
                "tone": tone,
                "title": title,
                "detail": detail,
                "action_label": action,
                "action_url": integ_url,
            }
        )

    wa_ok = False
    try:
        from sqlalchemy import text

        # SQL directo: evita depender del modelo Meow en este slice de deploy.
        wa_ok = (
            db.execute(
                text(
                    "SELECT 1 FROM whatsapp_meow_sessions "
                    "WHERE company_id = :cid AND user_id = :uid AND status = 'connected' "
                    "LIMIT 1"
                ),
                {"cid": int(user.company_id), "uid": int(user.id)},
            ).first()
            is not None
        )
    except Exception:
        wa_ok = False
    if not wa_ok:
        alerts.append(
            {
                "key": "whatsapp_channel",
                "tone": "warn",
                "title": "WhatsApp sin conectar",
                "detail": "Vinculá tu número para enviar y responder por WhatsApp.",
                "action_label": "Conectar",
                "action_url": integ_url,
            }
        )

    return alerts


def _plan_payment_alerts(user: User, db: Session) -> list[dict]:
    """Pago del plan (opcional): no rompe el banner si el módulo no está en este deploy."""
    from datetime import date

    from app.core.permissions import is_company_admin
    from app.models.company import Company

    try:
        from app.services.ops_agenda import payment_notice
    except Exception:
        return []

    company = db.get(Company, user.company_id)
    if company is None:
        return []
    notice = payment_notice(db, company)
    if notice is None:
        return []
    if notice["blocked"]:
        detail = (
            "Las secuencias que ya arrancaron siguen, pero no se pueden usar créditos nuevos "
            "hasta que se registre el pago del plan."
        )
        if not is_company_admin(user.role):
            detail += " Avisale al director de tu empresa."
        return [{"key": "plan_blocked", "title": "Créditos bloqueados", "detail": detail}]
    if not is_company_admin(user.role):
        return []
    pay_by = date.fromisoformat(notice["pay_by"])
    credit_on = date.fromisoformat(notice["credit_on"])
    return [
        {
            "key": "plan_due",
            "tone": "warn",
            "title": f"Pagá el plan antes del {pay_by:%d/%m}",
            "detail": (
                f"Tus créditos del mes se activan el {credit_on:%d/%m}. "
                "Si el pago no llega a tiempo, se bloquean hasta que se registre."
            ),
        }
    ]
