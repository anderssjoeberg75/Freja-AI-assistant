import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key

@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}

def test_get_strava_data(auth_headers):
    client = TestClient(app)
    response = client.get("/api/strava/data?limit=5", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

def test_save_strava_data(auth_headers):
    client = TestClient(app)
    payload = {
        "name": "Morgonlöpning Test",
        "type": "Run",
        "date": "2026-07-01",
        "distance": 8.5,
        "moving_time": 2700,
        "elapsed_time": 2800,
        "total_elevation_gain": 120,
        "average_speed": 3.15,
        "max_speed": 4.2,
        "average_heartrate": 148,
        "max_heartrate": 168,
        "calories": 620
    }
    response = client.post("/api/strava/data", json=payload, headers=auth_headers)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json.get("status") == "success"

def test_strava_credentials_via_keys_endpoint(auth_headers):
    # Credentials are stored in the shared api_keys table and served by /api/keys.
    client = TestClient(app)
    response = client.get("/api/keys", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), dict)
