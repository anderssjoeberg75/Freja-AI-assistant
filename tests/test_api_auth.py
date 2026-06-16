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
        return row[0] if (row and row[0]) else "freja_secret"

def test_api_auth_no_token():
    client = TestClient(app)
    # Requests without token to protected API endpoints should fail with 401
    response = client.get("/api/keys")
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized: Invalid or missing Freja Access Token."}

def test_api_auth_invalid_token():
    client = TestClient(app)
    # Requests with invalid token should fail with 401
    response = client.get("/api/keys", headers={"X-Freja-Token": "wrong_token"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized: Invalid or missing Freja Access Token."}

def test_api_auth_valid_token(db_token):
    client = TestClient(app)
    # Requests with the correct token should bypass auth (e.g. return 200/404 etc instead of 401)
    response = client.get("/api/keys", headers={"X-Freja-Token": db_token})
    # Since it's a valid token, we should get 200 OK
    assert response.status_code == 200

def test_api_auth_bypass_strava_callback():
    client = TestClient(app)
    # The Strava OAuth callback path must bypass token verification
    response = client.get("/api/strava/callback?code=mock_code")
    # It will probably return 400 or other errors due to mock code, but NOT 401 Unauthorized
    assert response.status_code != 401
