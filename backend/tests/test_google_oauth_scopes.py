import urllib.parse

from app.services.google_oauth import DEFAULT_SCOPES, build_authorization_url


def test_default_scopes_are_minimum_product_set():
    assert "https://www.googleapis.com/auth/gmail.modify" not in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/calendar" not in DEFAULT_SCOPES
    assert "openid" not in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/gmail.compose" in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/gmail.readonly" in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/calendar.events" in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/calendar.events.freebusy" in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/calendar.calendarlist.readonly" in DEFAULT_SCOPES
    assert "https://www.googleapis.com/auth/userinfo.email" in DEFAULT_SCOPES


def test_authorization_url_does_not_include_prior_grants(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://example.test/auth/google/callback")
    monkeypatch.setenv("GOOGLE_OAUTH_STATE_SECRET", "state-secret")
    url = build_authorization_url(state="abc")
    assert "include_granted_scopes=false" in url
    assert "gmail.modify" not in url
    assert "gmail.compose" in url


def test_strips_windows_path_prefix_from_oauth_env(monkeypatch):
    monkeypatch.setenv(
        "GOOGLE_CLIENT_ID",
        r"C:\Users\mjray\OneDrive\165388230781-t046tkugjg1krigae29p2mta8mdkvpu2.apps.googleusercontent.com",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", r"C:\Users\mjray\GOCSPX-exampleSecretValue123")
    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URI",
        r"C:\Users\mjray\https:\api-production-21aa.up.railway.app\auth\google\callback",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_STATE_SECRET", "state-secret")
    url = build_authorization_url(state="abc")
    assert "165388230781-t046tkugjg1krigae29p2mta8mdkvpu2.apps.googleusercontent.com" in url
    assert "https://api-production-21aa.up.railway.app/auth/google/callback" in urllib.parse.unquote(url)
    assert "C:" not in url
    assert "Users" not in url
    assert "OneDrive" not in url
