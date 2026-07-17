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

def test_strava_data_limit_logic(auth_headers):
    from backend.database import get_db_connection
    client = TestClient(app)
    # Clear any previous test activities
    with get_db_connection() as conn:
        conn.cursor().execute("DELETE FROM strava_activities")
        conn.commit()

    # Save multiple test activities
    for i in range(5):
        payload = {
            "name": f"Run {i}",
            "type": "Run",
            "date": f"2026-07-0{i+1}",
            "distance": 5.0 + i,
            "moving_time": 1800,
            "elapsed_time": 1800,
            "total_elevation_gain": 50,
            "average_speed": 3.0,
            "max_speed": 4.0,
            "average_heartrate": 140,
            "max_heartrate": 160,
            "calories": 400
        }
        response = client.post("/api/strava/data", json=payload, headers=auth_headers)
        assert response.status_code == 200

    # Fetch with limit=3
    response = client.get("/api/strava/data?limit=3", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    # Check that they are ordered reverse-chronologically (newest date first)
    assert data[0]["name"] == "Run 4"
    assert data[1]["name"] == "Run 3"
    assert data[2]["name"] == "Run 2"

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

def test_strava_sync_endpoint_overwrite(auth_headers):
    from backend.database import set_api_key
    set_api_key('freja_strava_client_id', '123456')
    set_api_key('freja_strava_client_secret', 'mock_secret')
    set_api_key('freja_strava_refresh_token', 'MOCK_REFRESH_TOKEN')
    
    client = TestClient(app)
    response = client.get("/api/strava/sync?days=30&overwrite=true", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "syncing"

@pytest.mark.asyncio
async def test_run_strava_sync_task_overwrite():
    from backend.database import get_db_connection
    from backend.routes.strava import run_strava_sync_task
    
    # Seed an activity
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO strava_activities (id, name, type, date) VALUES (9999, 'To Delete', 'Run', '2026-06-01')")
        conn.commit()
        
    # Run sync task in mock mode with overwrite=True
    await run_strava_sync_task('123456', 'mock_secret', 'MOCK_REFRESH_TOKEN', days=14, overwrite=True)
    
    # Verify the old activity was deleted
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strava_activities WHERE id = 9999")
        assert cursor.fetchone()[0] == 0
