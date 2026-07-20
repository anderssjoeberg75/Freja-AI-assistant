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


def _stub_garminconnect(monkeypatch, good_date, failing_dates):
    """Installs a fake `garminconnect` where good_date returns data and failing_dates raise.

    Mirrors how the real client behaves when Garmin is unreachable for a given day: each
    per-day call raises independently, which is what used to leave the accumulators holding
    the previous day's numbers.
    """
    import sys
    import types

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities(self, *a):
            return []
        def get_stats(self, d):
            if d == good_date:
                return {'totalSteps': 12345, 'activeCalories': 600}
            raise RuntimeError(f"Garmin unavailable for {d}")
        def get_sleep_data(self, d):
            if d == good_date:
                return {'dailySleepDTO': {'sleepTimeSeconds': 27000}}
            raise RuntimeError(f"Garmin unavailable for {d}")
        def get_heart_rates(self, d):
            if d == good_date:
                return {'restingHeartRate': 52}
            raise RuntimeError(f"Garmin unavailable for {d}")
        def get_hrv_data(self, d):
            if d == good_date:
                return {'hrvSummary': {'lastNightAvg': 60}}
            raise RuntimeError(f"Garmin unavailable for {d}")
        def get_body_battery(self, d):
            if d == good_date:
                return [{'bodyBatteryValuesArray': [[0, 90]]}]
            raise RuntimeError(f"Garmin unavailable for {d}")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            raise RuntimeError("no training status")
        def get_training_readiness(self, d):
            raise RuntimeError("no readiness")

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)


def test_failed_day_stores_null_not_yesterdays_values(monkeypatch):
    """A day whose Garmin fetches all fail must store NULL, not the previous day's numbers.

    The per-day accumulators used to be initialised outside the loop and only reassigned
    inside their own try blocks, so a failed fetch silently wrote yesterday's steps, sleep,
    resting HR, body battery and HRV into today's row as if genuinely measured.
    """
    import datetime
    from backend.database import get_db_connection
    from backend.routes.garmin import run_garmin_sync_task_blocking

    today = datetime.date.today()
    good_date = (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    bad_date = today.strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_health WHERE date IN (?, ?)", (good_date, bad_date))
        conn.commit()

    _stub_garminconnect(monkeypatch, good_date, [bad_date])
    run_garmin_sync_task_blocking('a@b.c', 'pw', 2)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT steps, sleep_hours, resting_hr, body_battery, hrv FROM garmin_health WHERE date = ?",
            (good_date,)
        )
        good = cursor.fetchone()
        cursor.execute(
            "SELECT steps, sleep_hours, resting_hr, body_battery, hrv FROM garmin_health WHERE date = ?",
            (bad_date,)
        )
        bad = cursor.fetchone()

    # The good day landed as measured.
    assert good == (12345, 7.5, 52, 90, 60)
    # The failed day must be entirely NULL - not a copy, and not a misleading 0.
    assert bad is not None, "the failed day should still get a row"
    for column, value in zip(("steps", "sleep_hours", "resting_hr", "body_battery", "hrv"), bad):
        assert value is None, f"{column} carried over from the previous day: {value!r}"


@pytest.mark.asyncio
async def test_health_averages_ignore_days_without_a_reading(monkeypatch):
    """Averages must divide by the number of days that actually have a reading.

    Dividing by the full window counted unworn days as 0 and dragged the reported resting
    heart rate and step count well below the true values.
    """
    from backend.services.tool_registry import exec_garmin_health
    import backend.services.tool_registry as registry

    # Two days of data: one measured, one entirely missing.
    async def fake_get_garmin_data(days=1):
        return [
            {'date': '2026-07-20', 'steps': None, 'sleep_hours': None, 'resting_hr': None,
             'active_calories': None, 'workout_type': 'Ingen', 'body_battery': None, 'hrv': None},
            {'date': '2026-07-19', 'steps': 10000, 'sleep_hours': 8.0, 'resting_hr': 50,
             'active_calories': 500, 'workout_type': 'Ingen', 'body_battery': None, 'hrv': None},
        ]

    monkeypatch.setattr(registry, "get_garmin_data", fake_get_garmin_data)
    monkeypatch.setattr(registry, "get_api_key", lambda name: "")  # skip the sync path

    result = await exec_garmin_health({"days": 2})
    averages = result["averages"]

    # One measured day: the average is that day's value, not half of it.
    assert averages["avg_resting_heart_rate"] == 50
    assert averages["avg_daily_steps"] == 10000
    assert averages["avg_sleep_hours"] == 8.0
    assert averages["avg_active_calories"] == 500
