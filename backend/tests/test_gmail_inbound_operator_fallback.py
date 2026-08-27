"""Inbound Gmail sync puede usar operador de empresa si el seller no tiene Gmail."""

from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.campaign import Campaign
from app.models.company import Company
from app.models.enums import UserRole
from app.models.product import Product
from app.models.user import User
from app.services.gmail_inbound_sync import sync_campaign_gmail_inbound
from app.services.manual_sequence_kickoff import try_find_gmail_operator


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _bundle(db):
    company = Company(name="Co", employee_count=5, plan="starter")
    db.add(company)
    db.flush()
    seller = User(
        company_id=company.id,
        email="seller@test.com",
        first_name="S",
        last_name="E",
        name="Seller",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    operator = User(
        company_id=company.id,
        email="ops@test.com",
        first_name="O",
        last_name="P",
        name="Ops",
        role=UserRole.sdr.value,
        password_hash="x",
    )
    product = Product(company_id=company.id, name="P", description="d", is_active=True)
    db.add_all([seller, operator, product])
    db.flush()
    campaign = Campaign(
        company_id=company.id,
        seller_id=seller.id,
        product_id=product.id,
        name="C",
        prospect_count=5,
        calendar_link="https://cal.test",
        timezone="America/Argentina/Buenos_Aires",
        available_hours="9-18",
        tone="profesional",
        estimated_meetings_min=1,
        estimated_meetings_max=2,
        estimated_cost_min=1,
        estimated_cost_max=2,
    )
    db.add(campaign)
    db.commit()
    return company, seller, operator, campaign


def test_try_find_gmail_operator_falls_back_to_other_user():
    db = _session()
    company, seller, operator, _campaign = _bundle(db)

    def _has(db_s, *, company_id, user_id):
        return int(user_id) == int(operator.id)

    with patch(
        "app.services.manual_sequence_kickoff._user_has_gmail",
        side_effect=_has,
    ):
        found = try_find_gmail_operator(db, company_id=company.id, preferred=seller)
    assert found is not None
    assert found.id == operator.id


@patch("app.services.gmail_inbound_sync.gmail_automation_enabled", return_value=True)
@patch(
    "app.services.gmail_inbound_sync.mseq.reconcile_meeting_vs_postergado_for_campaign",
    return_value=0,
)
def test_sync_allows_company_gmail_operator(_mock_reconcile, _mock_flag):
    db = _session()
    company, seller, operator, campaign = _bundle(db)
    fake_row = MagicMock()
    fake_row.external_email = "ops@gmail.com"

    with patch(
        "app.services.gmail_inbound_sync.get_valid_gmail_connection",
        return_value=("token", fake_row),
    ):
        with patch("app.services.gmail_inbound_sync.httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value = MagicMock()
            out = sync_campaign_gmail_inbound(
                db,
                company_id=company.id,
                user_id=operator.id,
                campaign_id=campaign.id,
                allow_company_gmail_operator=True,
            )
    assert out.get("skipped") is not True
    assert "imported" in out


@patch("app.services.gmail_inbound_sync.gmail_automation_enabled", return_value=True)
def test_sync_rejects_non_seller_without_flag(_mock_flag):
    db = _session()
    company, seller, operator, campaign = _bundle(db)
    try:
        sync_campaign_gmail_inbound(
            db,
            company_id=company.id,
            user_id=operator.id,
            campaign_id=campaign.id,
            allow_company_gmail_operator=False,
        )
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "vendedor asignado" in str(e).lower()
