from unittest.mock import MagicMock, patch

from app.services.crm import salesforce
from app.services.crm.config import salesforce_configured, salesforce_enabled


def _db() -> MagicMock:
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    return db


def _sf_env() -> dict[str, str]:
    return {
        "SALESFORCE_CLIENT_ID": "cid",
        "SALESFORCE_CLIENT_SECRET": "secret",
        "SALESFORCE_REFRESH_TOKEN": "refresh",
        "SALESFORCE_INSTANCE_URL": "https://example.my.salesforce.com",
        "SALESFORCE_ENABLED": "1",
    }


def test_salesforce_not_configured():
    with patch.dict("os.environ", {}, clear=True):
        data = salesforce.verify_salesforce(_db(), 1, deep=False)
    assert data["configured"] is False


def test_salesforce_verify_ok():
    with patch.dict("os.environ", _sf_env(), clear=True):
        salesforce._clear_token_cache()
        assert salesforce_configured()
        assert salesforce_enabled()
        with patch(
            "app.services.crm.company_credentials.get_salesforce_auth",
            return_value=("atok", "https://example.my.salesforce.com"),
        ):
            org_resp = MagicMock()
            org_resp.status_code = 200
            org_resp.json.return_value = {"records": [{"Name": "Acme Org"}]}
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = org_resp
            with patch("app.services.crm.salesforce.httpx.Client", return_value=mock_client):
                data = salesforce.verify_salesforce(_db(), 1, deep=True)

    assert data["api_reachable"] is True
    assert data["org_name"] == "Acme Org"


def test_salesforce_upsert_creates_contact():
    query_resp = MagicMock()
    query_resp.status_code = 200
    query_resp.json.return_value = {"records": []}

    create_resp = MagicMock()
    create_resp.status_code = 201
    create_resp.json.return_value = {"id": "003xx"}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = create_resp
    mock_client.get.return_value = query_resp

    with patch("app.services.crm.salesforce.httpx.Client", return_value=mock_client):
        cid = salesforce.upsert_contact(
            access_token="atok",
            instance_url="https://example.my.salesforce.com",
            email="lead@test.com",
            first_name="Ana",
            last_name="Lopez",
            company_name="Test Co",
        )

    assert cid == "003xx"
