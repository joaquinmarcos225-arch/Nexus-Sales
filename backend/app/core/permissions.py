"""Permisos centralizados por rol — backend es la fuente de verdad."""

from __future__ import annotations

from enum import Enum

from app.models.enums import UserRole


class Permission(str, Enum):
    # Campañas y sourcing
    CAMPAIGN_VIEW = "campaign.view"
    CAMPAIGN_CREATE = "campaign.create"
    CAMPAIGN_VIEW_TEAM = "campaign.view_team"
    LEAD_SOURCING_RUN = "lead_sourcing.run"
    OUTREACH_GENERATE = "outreach.generate"
    OUTREACH_EDIT_OWN = "outreach.edit_own"
    OUTREACH_REVIEW_TEAM = "outreach.review_team"

    # Prospectos
    PROSPECT_VIEW = "prospect.view"
    PROSPECT_CLAIM = "prospect.claim"
    PROSPECT_ACT_OWN = "prospect.act_own"
    PROSPECT_ACT_TEAM = "prospect.act_team"
    PROSPECT_RELEASE = "prospect.release"
    PROSPECT_REASSIGN = "prospect.reassign"
    PROSPECT_RULES = "prospect.rules"

    # Secuencias
    SEQUENCE_VIEW_OWN = "sequence.view_own"
    SEQUENCE_VIEW_TEAM = "sequence.view_team"
    SEQUENCE_PAUSE_TEAM = "sequence.pause_team"

    # Productos / ICP / playbooks
    PRODUCT_VIEW = "product.view"
    PRODUCT_CREATE = "product.create"
    PRODUCT_EDIT = "product.edit"
    PRODUCT_DELETE = "product.delete"
    ICP_MANAGE = "icp.manage"
    PLAYBOOK_MANAGE = "playbook.manage"

    # Usuarios y empresa
    TEAM_VIEW = "team.view"
    TEAM_CREATE = "team.create"
    TEAM_EDIT = "team.edit"
    USER_CREATE = "user.create"
    USER_EDIT = "user.edit"
    USER_CHANGE_ROLE = "user.change_role"
    USER_ACTIVATE = "user.activate"
    COMPANY_CONFIG = "company.config"
    BILLING_MANAGE = "billing.manage"
    CONNECTIONS_OWN = "connections.own"

    # Métricas y operaciones
    METRICS_TEAM = "metrics.team"
    OPERATIONS_CONTROL = "operations.control"


_GERENTE_BASE = frozenset(
    {
        Permission.CAMPAIGN_VIEW,
        Permission.CAMPAIGN_CREATE,
        Permission.CAMPAIGN_VIEW_TEAM,
        Permission.LEAD_SOURCING_RUN,
        Permission.OUTREACH_GENERATE,
        Permission.OUTREACH_EDIT_OWN,
        Permission.OUTREACH_REVIEW_TEAM,
        Permission.PROSPECT_VIEW,
        Permission.PROSPECT_CLAIM,
        Permission.PROSPECT_ACT_OWN,
        Permission.PROSPECT_ACT_TEAM,
        Permission.PROSPECT_RELEASE,
        Permission.PROSPECT_REASSIGN,
        Permission.PROSPECT_RULES,
        Permission.SEQUENCE_VIEW_OWN,
        Permission.SEQUENCE_VIEW_TEAM,
        Permission.SEQUENCE_PAUSE_TEAM,
        Permission.PRODUCT_VIEW,
        Permission.PRODUCT_CREATE,
        Permission.PRODUCT_EDIT,
        Permission.PRODUCT_DELETE,
        Permission.ICP_MANAGE,
        Permission.PLAYBOOK_MANAGE,
        Permission.TEAM_VIEW,
        Permission.TEAM_CREATE,
        Permission.TEAM_EDIT,
        Permission.USER_CREATE,
        Permission.USER_EDIT,
        Permission.USER_CHANGE_ROLE,
        Permission.USER_ACTIVATE,
        Permission.COMPANY_CONFIG,
        Permission.CONNECTIONS_OWN,
        Permission.METRICS_TEAM,
    }
)

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.sdr: frozenset(
        {
            Permission.CAMPAIGN_VIEW,
            Permission.CAMPAIGN_CREATE,
            Permission.LEAD_SOURCING_RUN,
            Permission.OUTREACH_GENERATE,
            Permission.OUTREACH_EDIT_OWN,
            Permission.PROSPECT_VIEW,
            Permission.PROSPECT_CLAIM,
            Permission.PROSPECT_ACT_OWN,
            Permission.SEQUENCE_VIEW_OWN,
            Permission.PRODUCT_VIEW,
            Permission.TEAM_VIEW,
            Permission.CONNECTIONS_OWN,
        }
    ),
    UserRole.manager: frozenset(
        {
            Permission.CAMPAIGN_VIEW,
            Permission.CAMPAIGN_CREATE,
            Permission.CAMPAIGN_VIEW_TEAM,
            Permission.LEAD_SOURCING_RUN,
            Permission.OUTREACH_GENERATE,
            Permission.OUTREACH_EDIT_OWN,
            Permission.OUTREACH_REVIEW_TEAM,
            Permission.PROSPECT_VIEW,
            Permission.PROSPECT_CLAIM,
            Permission.PROSPECT_ACT_OWN,
            Permission.PROSPECT_ACT_TEAM,
            Permission.SEQUENCE_VIEW_OWN,
            Permission.SEQUENCE_VIEW_TEAM,
            Permission.SEQUENCE_PAUSE_TEAM,
            Permission.PRODUCT_VIEW,
            Permission.TEAM_VIEW,
            Permission.CONNECTIONS_OWN,
            Permission.METRICS_TEAM,
            Permission.OPERATIONS_CONTROL,
        }
    ),
    # Director / gerente: opera la empresa e también puede tomar y ejecutar outreach propio
    UserRole.gerente: _GERENTE_BASE,
    # Owner: mismo alcance que Director (billing self-serve removido; B2B sales-led)
    UserRole.owner: _GERENTE_BASE,
}


def normalize_role(raw: str) -> UserRole:
    """Acepta roles legacy (seller/admin) y los normaliza."""
    key = (raw or "").strip().lower()
    legacy = {
        "seller": UserRole.sdr,
        "admin": UserRole.gerente,
        "director": UserRole.gerente,
    }
    if key in legacy:
        return legacy[key]
    try:
        return UserRole(key)
    except ValueError as exc:
        raise ValueError(f"Rol inválido: {raw!r}") from exc


def safe_normalize_role(raw: str, *, default: UserRole = UserRole.sdr) -> UserRole:
    try:
        return normalize_role(raw)
    except ValueError:
        return default


def is_company_admin(role: UserRole | str) -> bool:
    """Gerente (director) u owner — administración de empresa / pool de créditos."""
    if isinstance(role, str):
        role = normalize_role(role)
    return role in (UserRole.gerente, UserRole.owner)


def permissions_for_role(role: UserRole | str) -> frozenset[Permission]:
    if isinstance(role, str):
        role = normalize_role(role)
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: UserRole | str, permission: Permission) -> bool:
    return permission in permissions_for_role(role)


def permission_codes_for_role(role: UserRole | str) -> list[str]:
    return sorted(p.value for p in permissions_for_role(role))
