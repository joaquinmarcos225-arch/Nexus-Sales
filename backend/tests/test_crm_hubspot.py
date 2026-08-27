from unittest.mock import MagicMock, patch

from app.services.crm import hubspot
from app.services.crm.config import hubspot_configured, hubspot_enabled


def _db() -> MagicMock:
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    return db


def test_hubspot_not_configured():
    with patch.dict("os.environ", {}, clear=True):
        data = hubspot.verify_hubspot(_db(), 1, deep=False)
    assert data["configured"] is False


def test_hubspot_verify_ok():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"companyName": "Acme", "portalId": 12345}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with patch.dict("os.environ", {"HUBSPOT_ACCESS_TOKEN": "pat-test", "HUBSPOT_ENABLED": "1"}):
        assert hubspot_configured()
        assert hubspot_enabled()
        with patch("app.services.crm.hubspot.httpx.Client", return_value=mock_client):
            with patch(
                "app.services.crm.company_credentials.get_hubspot_access_token",
                return_value="pat-test",
            ):
                data = hubspot.verify_hubspot(_db(), 1, deep=True)

    assert data["api_reachable"] is True
    assert data["portal_name"] == "Acme"
    assert data["portal_id"] == "12345"


def test_hubspot_upsert_returns_id():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": [{"id": "999"}]}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("app.services.crm.hubspot.httpx.Client", return_value=mock_client):
        cid = hubspot.upsert_contact(
            access_token="pat-test",
            email="lead@test.com",
            first_name="Ana",
            last_name="Lopez",
            company_name="Test Co",
        )

    assert cid == "999"


def test_crm_oauth_redirects_to_integraciones(monkeypatch):
    monkeypatch.setenv("NEXUS_FRONTEND_URL", "http://127.0.0.1:5173")
    from app.services.crm.oauth_state import frontend_redirect_error, frontend_redirect_success

    assert "/configuracion/integraciones?hubspot=connected" in frontend_redirect_success("hubspot")
    err = frontend_redirect_error("salesforce", "denied", "no")
    assert "/configuracion/integraciones?" in err
    assert "salesforce_error=denied" in err
