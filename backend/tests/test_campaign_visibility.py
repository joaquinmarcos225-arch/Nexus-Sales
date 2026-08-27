"""Visibilidad de campañas por vendedor."""

from types import SimpleNamespace

from app.models.enums import UserRole
from app.services.campaign_visibility import campaign_is_visible_to_user, prospect_is_visible_to_user
from app.services.manual_sequence_kickoff import INDIVIDUAL_CAMPAIGN_NAME


def test_sdr_sees_own_campaign_not_teammate():
    sdr = SimpleNamespace(id=6, company_id=1, role=UserRole.sdr.value)
    own = SimpleNamespace(id=10, company_id=1, seller_id=6, name="Outbound Joaquin")
    other = SimpleNamespace(id=11, company_id=1, seller_id=5, name="Outbound Mia")
    other_company = SimpleNamespace(id=12, company_id=99, seller_id=6, name="Otra empresa")
    assert campaign_is_visible_to_user(sdr, own) is True
    assert campaign_is_visible_to_user(sdr, other) is False
    assert campaign_is_visible_to_user(sdr, other_company) is False


def test_gerente_sees_team_campaigns_same_company_only():
    boss = SimpleNamespace(id=6, company_id=1, role=UserRole.gerente.value)
    teammate = SimpleNamespace(id=11, company_id=1, seller_id=5, name="Outbound Mia")
    foreign = SimpleNamespace(id=12, company_id=2, seller_id=5, name="Otra")
    assert campaign_is_visible_to_user(boss, teammate) is True
    assert campaign_is_visible_to_user(boss, foreign) is False


def test_individual_container_hides_others_prospects_from_sdr():
    sdr = SimpleNamespace(id=6, company_id=1, role=UserRole.sdr.value)
    camp = SimpleNamespace(
        id=3, company_id=1, seller_id=1, name=INDIVIDUAL_CAMPAIGN_NAME
    )
    mine = SimpleNamespace(id=1, company_id=1, owner_user_id=6)
    theirs = SimpleNamespace(id=2, company_id=1, owner_user_id=5)
    assert prospect_is_visible_to_user(sdr, camp, mine) is True
    assert prospect_is_visible_to_user(sdr, camp, theirs) is False
