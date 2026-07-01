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
    # Requests without a token must be rejected
    response = client.get("/api/keys")
    assert response.status_code == 401

def test_api_auth_invalid_token():
    client = TestClient(app)
    # Requests with an arbitrary/wrong token must be rejected
    response = client.get("/api/keys", headers={"X-Freja-Token": "wrong_token"})
    assert response.status_code == 401

def test_api_auth_valid_token(db_token):
    client = TestClient(app)
    # Requests with the real token stored in the database must succeed
    response = client.get("/api/keys", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200

def test_api_auth_bypass_strava_callback():
    client = TestClient(app)
    # The Strava OAuth callback path should bypass and succeed (or at least not return 401)
    response = client.get("/api/strava/callback?code=mock_code")
    assert response.status_code != 401

def test_api_auth_bypass_google_calendar_callback():
    client = TestClient(app)
    # The Google Calendar OAuth callback path should bypass and succeed (or at least not return 401)
    response = client.get("/api/google_calendar/callback?code=mock_code")
    assert response.status_code != 401
