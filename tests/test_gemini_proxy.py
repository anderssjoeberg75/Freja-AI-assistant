import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture(autouse=True)
def restore_gemini_key():
    backup = get_api_key("freja_gemini_apikey")
    yield
    set_api_key("freja_gemini_apikey", backup or "")


def test_gemini_generate_rejects_malformed_model_name(auth_headers):
    """A model identifier must match Gemini's own naming shape before being spliced into the
    outbound request URL - previously any string (including path-breaking characters) was
    forwarded to Google unchecked."""
    set_api_key("freja_gemini_apikey", "fake_key_for_test")
    client = TestClient(app)
    response = client.post(
        "/api/gemini/generate?model=../evil/path",
        json={"contents": []},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_gemini_generate_rejects_oversized_payload(auth_headers):
    """A single request must not be able to run up unbounded cost against the server's own
    Gemini API key - a request body over the sanity cap is rejected before being forwarded."""
    set_api_key("freja_gemini_apikey", "fake_key_for_test")
    client = TestClient(app)
    huge_payload = {"contents": [{"role": "user", "parts": [{"text": "x" * 2_100_000}]}]}
    response = client.post("/api/gemini/generate", json=huge_payload, headers=auth_headers)
    assert response.status_code == 413


def test_gemini_generate_requires_api_key(auth_headers):
    set_api_key("freja_gemini_apikey", "")
    client = TestClient(app)
    response = client.post("/api/gemini/generate", json={"contents": []}, headers=auth_headers)
    assert response.status_code == 400
