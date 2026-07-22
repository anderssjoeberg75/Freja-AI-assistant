import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import set_api_key, get_api_key

@pytest.fixture(autouse=True)
def clean_instagram_keys():
    """Backup and clear Instagram credentials before each test, restoring them after."""
    backup = {
        "freja_instagram_client_id": get_api_key("freja_instagram_client_id"),
        "freja_instagram_client_secret": get_api_key("freja_instagram_client_secret"),
        "freja_instagram_access_token": get_api_key("freja_instagram_access_token"),
        "freja_instagram_business_account_id": get_api_key("freja_instagram_business_account_id"),
        "freja_instagram_username": get_api_key("freja_instagram_username"),
    }
    
    # Clear keys for clean testing
    for k in backup:
        set_api_key(k, "")
        
    yield
    
    # Restore keys
    for k, v in backup.items():
        set_api_key(k, v or "")

@pytest.fixture
def db_token():
    """Gets or defaults the API access token for protected routes."""
    return get_api_key('freja_access_token') or "freja1234"

def test_instagram_auth_missing_client_id():
    client = TestClient(app)
    # Auth path should return 400 bad request if Client ID is not configured (exempt from token auth)
    response = client.get("/api/instagram/auth", follow_redirects=False)
    assert response.status_code == 400
    assert "Instagram/Facebook Client ID is missing" in response.text

def test_instagram_auth_with_client_id():
    set_api_key("freja_instagram_client_id", "test_client_id")
    client = TestClient(app)
    # Should redirect to Facebook dialog (exempt from token auth)
    response = client.get("/api/instagram/auth", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers.get("location")
    assert "facebook.com/v19.0/dialog/oauth" in location
    assert "client_id=test_client_id" in location
    assert "state=" in location


def test_instagram_callback_rejects_missing_state():
    """Without a state param (or a mismatched one), the callback must refuse to exchange the
    code - otherwise an attacker who completes their own Facebook login can trick the admin's
    browser into linking the attacker's Instagram account to this Freja instance."""
    client = TestClient(app)
    response = client.get("/api/instagram/callback?code=some_code", follow_redirects=False)
    assert response.status_code in (307, 302)
    assert "error=" in (response.headers.get("location") or "")


def test_instagram_callback_rejects_mismatched_state():
    set_api_key("freja_instagram_client_id", "test_client_id")
    client = TestClient(app)
    # Mint a real pending state via /auth...
    client.get("/api/instagram/auth", follow_redirects=False)
    # ...then present a different one to /callback.
    response = client.get("/api/instagram/callback?code=some_code&state=not-the-real-state", follow_redirects=False)
    assert response.status_code in (307, 302)
    assert "error=" in (response.headers.get("location") or "")

def test_instagram_status_no_token():
    client = TestClient(app)
    # Request without token to protected status endpoint must be rejected
    response = client.get("/api/instagram/status")
    assert response.status_code == 401

def test_instagram_status_disconnected(db_token):
    client = TestClient(app)
    response = client.get("/api/instagram/status", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200
    assert response.json() == {"status": "disconnected"}

def test_instagram_status_connected(db_token):
    set_api_key("freja_instagram_access_token", "fake_token")
    set_api_key("freja_instagram_business_account_id", "fake_id")
    set_api_key("freja_instagram_username", "test_user")
    
    client = TestClient(app)
    response = client.get("/api/instagram/status", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "connected"
    assert data["username"] == "test_user"
    assert data["instagram_business_account_id"] == "fake_id"

def test_instagram_disconnect(db_token):
    set_api_key("freja_instagram_access_token", "fake_token")
    set_api_key("freja_instagram_business_account_id", "fake_id")
    set_api_key("freja_instagram_username", "test_user")
    
    client = TestClient(app)
    # Send DELETE request to disconnect
    response = client.delete("/api/instagram/status", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200
    assert response.json() == {"status": "disconnected"}
    
    # Check that settings were cleared in DB
    assert not get_api_key("freja_instagram_access_token")
    assert not get_api_key("freja_instagram_business_account_id")
    assert not get_api_key("freja_instagram_username")


@pytest.mark.asyncio
async def test_publish_media_rejects_non_https_url():
    """publish_media posts publicly and irreversibly to the real linked Instagram account -
    a non-https media_url (file:/javascript:/plain-http) must be rejected before it ever
    reaches Meta's API, the same minimal bar applied to every other externally-sourced URL
    this app hands off."""
    from backend.services.instagram_service import publish_media

    for bad_url in ["http://example.com/x.jpg", "file:///etc/passwd", "javascript:alert(1)", ""]:
        result = await publish_media(bad_url, "caption")
        assert "error" in result
