"""Visibilidad de campañas y secuencias: cada vendedor ve las suyas."""

from __future__ import annotations

from fastapi import HTTPException

from app.core.permissions import Permission, has_permission
from app.models.campaign import Campaign
from app.models.prospect import Prospect
from app.models.user import User


def user_can_view_team_campaigns(user: User) -> bool:
    return has_permission(user.role, Permission.CAMPAIGN_VIEW_TEAM)


def campaign_is_visible_to_user(user: User, campaign: Campaign) -> bool:
    if int(getattr(user, "company_id", 0) or 0) != int(getattr(campaign, "company_id", 0) or 0):
        return False
    if user_can_view_team_campaigns(user):
        return True
    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if is_individual_container_campaign(campaign):
        return True
    return int(getattr(campaign, "seller_id", 0) or 0) == int(user.id)


def assert_campaign_access(user: User, campaign: Campaign) -> None:
    if campaign_is_visible_to_user(user, campaign):
        return
    raise HTTPException(
        status_code=403,
        detail="Esta campaña es de otro vendedor. Solo ves tus propias secuencias.",
    )


def prospect_is_visible_to_user(user: User, campaign: Campaign, prospect: Prospect) -> bool:
    if int(getattr(prospect, "company_id", 0) or 0) != int(getattr(user, "company_id", 0) or 0):
        return False
    if not campaign_is_visible_to_user(user, campaign):
        return False
    if user_can_view_team_campaigns(user):
        return True
    from app.services.manual_sequence_kickoff import is_individual_container_campaign

    if not is_individual_container_campaign(campaign):
        return True
    owner = getattr(prospect, "owner_user_id", None)
    if owner is None:
        return False
    return int(owner) == int(user.id)


def filter_prospects_for_viewer(
    user: User, campaign: Campaign, rows: list[Prospect]
) -> list[Prospect]:
    return [p for p in rows if prospect_is_visible_to_user(user, campaign, p)]
