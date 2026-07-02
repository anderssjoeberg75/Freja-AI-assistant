import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key

@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}

def test_get_garmin_data(auth_headers):
    client = TestClient(app)
    response = client.get("/api/garmin/data?days=7", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

def test_save_garmin_data(auth_headers):
    client = TestClient(app)
    payload = {
        "date": "2026-07-01",
        "steps": 10500,
        "sleep_hours": 7.5,
        "resting_hr": 55,
        "active_calories": 450,
        "workout_type": "Löpning",
        "workout_duration": 45,
        "body_battery": 85,
        "hrv": 65,
        "recovery_time": 18,
        "training_status": "Optimal"
    }
    response = client.post("/api/garmin/save", json=payload, headers=auth_headers)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json.get("status") == "success"

def test_garmin_credentials(auth_headers):
    client = TestClient(app)
    # GET credentials
    response = client.get("/api/garmin/credentials", headers=auth_headers)
    assert response.status_code == 200
    assert "email" in response.json()
