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


@pytest.mark.asyncio
async def test_overwrite_sync_keeps_history_when_strava_fails(monkeypatch):
    """A failed overwrite sync must not leave the user with an empty activity table.

    Overwrite used to DELETE every row before contacting Strava, so an expired refresh
    token or a network blip wiped the entire history with nothing to put back.
    """
    from backend.database import get_db_connection
    import backend.routes.strava as strava_module

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM strava_activities")
        cursor.execute(
            "INSERT INTO strava_activities (id, name, type, date) VALUES (9001, 'Precious history', 'Run', '2020-01-01')"
        )
        conn.commit()

    class FailingClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            raise RuntimeError("Strava OAuth is down")
        async def get(self, *a, **k):
            raise RuntimeError("Strava API is down")

    monkeypatch.setattr(strava_module, "shared_client", FailingClient)

    # Real (non-demo) credentials so the task takes the network path, which then fails.
    await strava_module.run_strava_sync_task(
        "999999", "real_secret", "real_refresh_token", days=3650, overwrite=True
    )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strava_activities WHERE id = 9001")
        survived = cursor.fetchone()[0]
    assert survived == 1, "a failed overwrite sync destroyed the existing activity history"


@pytest.mark.asyncio
async def test_failed_sync_reports_error_and_fabricates_nothing(monkeypatch):
    """A failed sync must report 'error' and must not invent activities.

    It previously seeded five demo workouts and reported 'success', so an expired token
    looked like a healthy sync while feeding fabricated sessions to the PT coach.
    """
    from backend.database import get_db_connection
    from backend.services.sync_status import get_sync_states
    import backend.routes.strava as strava_module

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM strava_activities")
        conn.commit()

    class FailingClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            raise RuntimeError("invalid refresh token")
        async def get(self, *a, **k):
            raise RuntimeError("invalid refresh token")

    monkeypatch.setattr(strava_module, "shared_client", FailingClient)
    await strava_module.run_strava_sync_task(
        "999999", "real_secret", "real_refresh_token", days=30, overwrite=False
    )

    states = get_sync_states()
    assert states["states"]["strava"] == "error"
    assert "invalid refresh token" in states["errors"]["strava"]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strava_activities")
        assert cursor.fetchone()[0] == 0, "a failed sync invented activities"


@pytest.mark.asyncio
async def test_overwrite_sync_does_not_truncate_beyond_page_cap(monkeypatch):
    """Overwrite must not delete more history than the capped fetch can restore.

    The activities fetch stops after 5 pages, so on an account with more activities than
    that, deleting everything first and then re-inserting a capped page set silently
    destroyed the remainder.
    """
    from backend.database import get_db_connection
    import backend.routes.strava as strava_module

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM strava_activities")
        cursor.execute(
            "INSERT INTO strava_activities (id, name, type, date) VALUES (9002, 'Old ride', 'Ride', '2019-05-05')"
        )
        conn.commit()

    class TokenThenTruncatedClient:
        """Refresh succeeds; the activity fetch returns a full page then dies."""
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            class R:
                def raise_for_status(self): return None
                def json(self): return {"access_token": "tok", "refresh_token": "real_refresh_token"}
            return R()
        async def get(self, *a, **k):
            raise RuntimeError("Strava API rate limit")

    monkeypatch.setattr(strava_module, "shared_client", TokenThenTruncatedClient)

    await strava_module.run_strava_sync_task(
        "999999", "real_secret", "real_refresh_token", days=3650, overwrite=True
    )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM strava_activities WHERE id = 9002")
        survived = cursor.fetchone()[0]
    assert survived == 1, "a partially-failed overwrite sync destroyed existing activities"


@pytest.mark.asyncio
async def test_get_strava_access_token_raises_on_real_refresh_failure(monkeypatch):
    """A configured-but-broken connection (network error, 429, revoked/expired refresh
    token) must raise, not silently return the same MOCK_ACCESS_TOKEN sentinel used for
    the deliberate demo-mode path - callers used to be unable to tell "broken" from
    "not connected" and served fabricated data with a 200 OK either way."""
    from backend.database import set_api_key
    import backend.services.strava_service as strava_service_module

    set_api_key('freja_strava_client_id', 'real_client_id')
    set_api_key('freja_strava_client_secret', 'real_secret')
    set_api_key('freja_strava_refresh_token', 'real_refresh_token')

    class FailingClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            raise RuntimeError("Strava OAuth is down")

    monkeypatch.setattr(strava_service_module, "shared_client", FailingClient)

    with pytest.raises(Exception):
        await strava_service_module.get_strava_access_token()


@pytest.mark.asyncio
async def test_get_strava_access_token_caches_between_calls(monkeypatch):
    """Every call used to refresh from scratch, burning through Strava's 100/15-min rate
    limit unnecessarily (activity details alone issues 3 data calls plus a token refresh)."""
    from backend.database import set_api_key
    import backend.services.strava_service as strava_service_module

    strava_service_module._token_cache.update({"refresh_token": None, "access_token": None, "expires_at": 0.0})
    set_api_key('freja_strava_client_id', 'real_client_id')
    set_api_key('freja_strava_client_secret', 'real_secret')
    set_api_key('freja_strava_refresh_token', 'cache_test_refresh_token')

    call_count = {"n": 0}

    class CountingClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            call_count["n"] += 1
            class R:
                def raise_for_status(self): return None
                def json(self): return {"access_token": "tok-1", "refresh_token": "cache_test_refresh_token", "expires_in": 21600}
            return R()

    monkeypatch.setattr(strava_service_module, "shared_client", CountingClient)

    first = await strava_service_module.get_strava_access_token()
    second = await strava_service_module.get_strava_access_token()
    assert first == second == "tok-1"
    assert call_count["n"] == 1, "the cached token should not trigger a second refresh call"


@pytest.mark.asyncio
async def test_activity_details_raises_on_real_api_failure_instead_of_faking_data(monkeypatch):
    """A real-token API failure (429, network blip) must not be masked as a fabricated
    activity with a 200 OK - that fake-data fallback is for the explicit demo-mode token
    only."""
    import backend.routes.strava as strava_module

    async def fake_real_token():
        return "REAL_LOOKING_TOKEN"
    monkeypatch.setattr(strava_module, "get_strava_access_token", fake_real_token)

    class FailingClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(strava_module, "shared_client", FailingClient)

    client = TestClient(app)
    response = client.get("/api/strava/activity_details?id=123456789", headers={"X-Freja-Token": get_api_key('freja_access_token') or "freja1234"})
    assert response.status_code == 400
    assert "Strava" in response.json().get("detail", "")


def test_delete_strava_log_missing_id_returns_404(auth_headers):
    client = TestClient(app)
    response = client.get("/api/strava/delete?id=99999999999", headers=auth_headers)
    assert response.status_code == 404
