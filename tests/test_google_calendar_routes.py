import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key

@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}

def test_get_google_calendar_data(auth_headers):
    client = TestClient(app)
    response = client.get("/api/google_calendar/data?days=30", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

def test_post_and_delete_google_calendar_event(auth_headers):
    client = TestClient(app)
    payload = {
        "summary": "Projektmöte F.R.E.J.A.",
        "description": "Genomgång av arkitektur och unit-tester",
        "start_time": "2026-07-02T14:00:00",
        "end_time": "2026-07-02T15:00:00",
        "location": "Virtual HUD Sector 09"
    }
    # Create event
    response = client.post("/api/google_calendar/data", json=payload, headers=auth_headers)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json.get("status") == "success"
    event = res_json.get("event", {})
    event_id = event.get("id")
    assert event_id is not None

    # Delete created event
    del_response = client.get(f"/api/google_calendar/delete?id={event_id}", headers=auth_headers)
    assert del_response.status_code == 200
    assert del_response.json().get("status") == "success"

def test_google_calendar_sync_trigger(auth_headers):
    client = TestClient(app)
    response = client.get("/api/google_calendar/sync", headers=auth_headers)
    assert response.status_code == 200
    assert response.json().get("status") == "syncing"
