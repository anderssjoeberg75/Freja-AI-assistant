import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key

@pytest.fixture
def db_token():
    # Retrieve the actual token stored in the database to use in the tests
    return get_api_key('freja_access_token') or "freja1234"

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

def test_api_keys_endpoint_supports_unmask(db_token):
    from backend.database import set_api_key
    client = TestClient(app)
    set_api_key("freja_gemini_apikey", "test_gemini_key")
    
    # Without unmask query param (defaults to false)
    response = client.get("/api/keys", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200
    assert response.json().get("freja_gemini_apikey") == "••••••••"
    
    # With unmask query param set to true
    response = client.get("/api/keys?unmask=true", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200
    assert response.json().get("freja_gemini_apikey") == "test_gemini_key"

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


def test_api_auth_lockout_after_repeated_failures(db_token):
    from backend.middleware import auth as auth_module

    client = TestClient(app)
    # Isolate this test's IP bucket so leftover failures from other tests in this
    # module (which share TestClient's synthetic "testclient" host) don't interfere.
    auth_module._failed_attempts.pop("testclient", None)
    auth_module._locked_until.pop("testclient", None)

    try:
        for _ in range(auth_module.FAILED_ATTEMPT_THRESHOLD):
            response = client.get("/api/keys", headers={"X-Freja-Token": "wrong_token"})
            assert response.status_code == 401

        # The next request, even with the correct token, should now be rate-limited.
        response = client.get("/api/keys", headers={"X-Freja-Token": db_token})
        assert response.status_code == 429
        assert "Retry-After" in response.headers
    finally:
        auth_module._failed_attempts.pop("testclient", None)
        auth_module._locked_until.pop("testclient", None)


def test_api_auth_options_preflight():
    client = TestClient(app)
    # OPTIONS requests (CORS preflight) must bypass authentication and return success/not 401
    response = client.options("/api/keys")
    assert response.status_code != 401


def test_get_gemini_models(db_token):
    client = TestClient(app)
    response = client.get("/api/system/gemini-models", headers={"X-Freja-Token": db_token})
    assert response.status_code == 200
    models = response.json()
    assert isinstance(models, list)
    assert len(models) > 0
    for m in models:
        assert "id" in m
        assert "name" in m


