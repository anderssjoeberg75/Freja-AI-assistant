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
    response = client.post("/api/garmin/data", json=payload, headers=auth_headers)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json.get("status") == "success"

def test_garmin_credentials_via_keys_endpoint(auth_headers):
    # Credentials are stored in the shared api_keys table and served by /api/keys.
    client = TestClient(app)
    response = client.get("/api/keys", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), dict)

def test_garmin_sync_since_last_sync_logic(auth_headers, monkeypatch):
    import datetime
    from backend.database import set_api_key
    
    # 1. Test case: no last_sync_garmin in database -> should default to 7 days
    set_api_key("last_sync_garmin", "")
    
    sync_days = None
    def mock_enqueue_task(func, email, password, days):
        nonlocal sync_days
        sync_days = days
        
    monkeypatch.setattr("backend.services.task_queue.enqueue_task", mock_enqueue_task)
    
    # Ensure credentials exist so the route doesn't fail
    set_api_key("freja_garmin_email", "test@example.com")
    set_api_key("freja_garmin_password", "testpass")
    
    client = TestClient(app)
    response = client.get("/api/garmin/sync", headers=auth_headers)
    assert response.status_code == 200
    assert sync_days == 7 # Fallback default
    
    # 2. Test case: last sync was 5 days ago -> should calculate 6 days (max(1, 5 + 1))
    five_days_ago = (datetime.date.today() - datetime.timedelta(days=5)).strftime("%Y-%m-%d") + " 10:00:00"
    set_api_key("last_sync_garmin", five_days_ago)
    
    response = client.get("/api/garmin/sync", headers=auth_headers)
    assert response.status_code == 200
    assert sync_days == 6
    
    # 3. Test case: last sync was 45 days ago -> should cap at 30 days
    forty_five_days_ago = (datetime.date.today() - datetime.timedelta(days=45)).strftime("%Y-%m-%d") + " 10:00:00"
    set_api_key("last_sync_garmin", forty_five_days_ago)
    
    response = client.get("/api/garmin/sync", headers=auth_headers)
    assert response.status_code == 200
    assert sync_days == 30
