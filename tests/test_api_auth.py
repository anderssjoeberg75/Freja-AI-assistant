import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_db_connection

@pytest.fixture
def db_token():
    # Retrieve the actual token stored in the database to use in the tests
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key_value FROM api_keys WHERE key_name = 'freja_access_token'")
        row = cursor.fetchone()
        return row[0] if (row and row[0]) else "freja1234"

def test_api_auth_no_token():
    client = TestClient(app)
    # Requests without token should now succeed since token auth is disabled
    response = client.get("/api/keys")
    assert response.status_code == 200

def test_api_auth_invalid_token():
    client = TestClient(app)
    # Requests with arbitrary tokens should also succeed
    response = client.get("/api/keys", headers={"X-Freja-Token": "wrong_token"})
    assert response.status_code == 200

def test_api_auth_bypass_strava_callback():
    client = TestClient(app)
    # The Strava OAuth callback path should bypass and succeed (or at least not return 401)
    response = client.get("/api/strava/callback?code=mock_code")
    assert response.status_code != 401
