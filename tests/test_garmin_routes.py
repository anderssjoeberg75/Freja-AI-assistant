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
        def get_activities_by_date(self, *a, **k):
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


def test_sync_raises_when_every_day_fails(monkeypatch):
    """If every per-day metric call fails for every day in the window (expired session,
    Garmin rate-limiting/lockout mid-run), the run must raise instead of committing all-NULL
    rows and letting run_garmin_sync_flow report "success" - which would advance
    last_sync_garmin and permanently strand the gap, since no later sync would ever retry it.
    """
    import datetime
    from backend.database import get_db_connection
    from backend.routes.garmin import run_garmin_sync_task_blocking

    today = datetime.date.today()
    bad_date = today.strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_health WHERE date = ?", (bad_date,))
        conn.commit()

    # good_date=None means nothing ever matches -> every day fails every metric.
    _stub_garminconnect(monkeypatch, good_date=None, failing_dates=[bad_date])

    with pytest.raises(Exception, match="no health data"):
        run_garmin_sync_task_blocking('a@b.c', 'pw', 1)


def test_resync_preserves_existing_value_when_one_metric_fails(monkeypatch):
    """Resyncing an already-populated date (the window always re-covers at least yesterday)
    must not null out a previously-correct value when just one metric call fails on the
    resync - the per-day reset-to-None exists to stop cross-day carryover, but the UPSERT
    used to write that None straight over good historical data instead of preserving it.
    """
    import datetime
    import sys
    import types
    from backend.database import get_db_connection
    from backend.routes.garmin import run_garmin_sync_task_blocking

    today = datetime.date.today()
    target_date = today.strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_health WHERE date = ?", (target_date,))
        cursor.execute(
            "INSERT INTO garmin_health (date, steps, hrv) VALUES (?, ?, ?)",
            (target_date, 9999, 55)
        )
        conn.commit()

    class PartiallyFailingGarmin:
        """steps succeeds with a fresh value; hrv fails on this resync and must not be
        nulled - the previously-stored 55 must survive."""
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return []
        def get_stats(self, d):
            return {'totalSteps': 12345, 'activeCalories': 600}
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep data")
        def get_heart_rates(self, d):
            raise RuntimeError("no heart rate data")
        def get_hrv_data(self, d):
            raise RuntimeError("Garmin unavailable for hrv this run")
        def get_body_battery(self, d):
            raise RuntimeError("no body battery")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            raise RuntimeError("no training status")
        def get_training_readiness(self, d):
            raise RuntimeError("no readiness")

    module = types.ModuleType('garminconnect')
    module.Garmin = PartiallyFailingGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT steps, hrv FROM garmin_health WHERE date = ?", (target_date,))
        row = cursor.fetchone()
    # steps: overwritten with the freshly-measured value. hrv: its fetch failed on this
    # resync, so the previously-stored 55 must survive, not be nulled out.
    assert row == (12345, 55)


def test_explicit_days_over_the_cap_is_clamped(auth_headers, monkeypatch):
    """An explicitly-supplied `days` used to bypass MAX_SYNC_DAYS entirely (only the no-args
    branch clamped it), letting a single request queue a run hammering Garmin's API far past
    its rate limits and monopolizing the background task queue."""
    from backend.database import set_api_key
    import backend.routes.garmin as gm
    from fastapi.testclient import TestClient
    from server import app

    set_api_key('freja_garmin_email', 'a@b.c')
    set_api_key('freja_garmin_password', 'pw')

    captured = {}

    def fake_enqueue(fn, email, password, days):
        captured["days"] = days

    # get_garmin_sync imports enqueue_task locally inside the function body, so the source
    # module has to be patched for this to take effect.
    import backend.services.task_queue as task_queue_module
    monkeypatch.setattr(task_queue_module, "enqueue_task", fake_enqueue)

    client = TestClient(app)
    response = client.get("/api/garmin/sync?days=5000", headers=auth_headers)
    assert response.status_code == 200
    assert captured["days"] == gm.MAX_SYNC_DAYS


def test_delete_garmin_log_missing_date_returns_404(auth_headers):
    from fastapi.testclient import TestClient
    from server import app
    client = TestClient(app)
    response = client.get("/api/garmin/delete?date=2099-01-01", headers=auth_headers)
    assert response.status_code == 404


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

    monkeypatch.setattr(registry.health_data, "get_garmin_data", fake_get_garmin_data)
    monkeypatch.setattr(registry.health_data, "get_api_key", lambda name: "")  # skip the sync path

    result = await exec_garmin_health({"days": 2})
    averages = result["averages"]

    # One measured day: the average is that day's value, not half of it.
    assert averages["avg_resting_heart_rate"] == 50
    assert averages["avg_daily_steps"] == 10000
    assert averages["avg_sleep_hours"] == 8.0
    assert averages["avg_active_calories"] == 500


def test_long_absence_queues_the_uncovered_days(auth_headers, monkeypatch):
    """A gap longer than the cap must be remembered, not silently dropped.

    The window is anchored on today and clamped to MAX_SYNC_DAYS, while last_sync_garmin
    jumps to now on success - so without a queue the trimmed days are never fetched by any
    later sync and become a permanent hole in the history.
    """
    import datetime
    from backend.database import set_api_key
    import backend.routes.garmin as gm

    set_api_key(gm.BACKFILL_KEY, "")
    today = datetime.date.today()
    last_sync = today - datetime.timedelta(days=60)
    set_api_key("last_sync_garmin", last_sync.strftime("%Y-%m-%d %H:%M:%S"))
    set_api_key("freja_garmin_email", "a@b.c")
    set_api_key("freja_garmin_password", "pw")

    captured = {}

    def fake_enqueue(fn, *args, **kwargs):
        captured["days"] = args[2]

    monkeypatch.setattr("backend.services.task_queue.enqueue_task", fake_enqueue)

    client = TestClient(app)
    res = client.get("/api/garmin/sync", headers=auth_headers)
    assert res.status_code == 200

    # The run itself stays capped...
    assert captured["days"] == gm.MAX_SYNC_DAYS
    # ...and the days it could not reach are queued rather than forgotten.
    pending = gm._read_backfill_range()
    assert pending is not None, "the uncovered days were dropped"
    start, end = pending
    oldest_covered = today - datetime.timedelta(days=gm.MAX_SYNC_DAYS - 1)
    assert start == last_sync
    assert end == oldest_covered - datetime.timedelta(days=1)
    # No day is both covered by the run and left in the queue.
    assert end < oldest_covered

    set_api_key(gm.BACKFILL_KEY, "")


def test_short_absence_queues_nothing(auth_headers, monkeypatch):
    """A gap inside the cap is fully covered, so nothing should be queued."""
    import datetime
    from backend.database import set_api_key
    import backend.routes.garmin as gm

    set_api_key(gm.BACKFILL_KEY, "")
    set_api_key("last_sync_garmin", (datetime.date.today() - datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"))
    set_api_key("freja_garmin_email", "a@b.c")
    set_api_key("freja_garmin_password", "pw")

    def fake_enqueue(fn, *args, **kwargs):
        pass
    monkeypatch.setattr("backend.services.task_queue.enqueue_task", fake_enqueue)

    client = TestClient(app)
    assert client.get("/api/garmin/sync", headers=auth_headers).status_code == 200
    assert gm._read_backfill_range() is None


@pytest.mark.asyncio
async def test_backfill_drains_oldest_first_and_completes(monkeypatch):
    """Draining walks the queued window oldest-first and clears it when done."""
    import datetime
    from backend.database import set_api_key
    import backend.routes.garmin as gm

    today = datetime.date.today()
    start = today - datetime.timedelta(days=59)
    end = today - datetime.timedelta(days=30)
    gm._write_backfill_range(start, end)

    windows = []

    def fake_blocking(email, password, days, end_date=None):
        windows.append((end_date - datetime.timedelta(days=days - 1), end_date))

    monkeypatch.setattr(gm, "run_garmin_sync_task_blocking", fake_blocking)

    first = await gm.drain_garmin_backfill("a@b.c", "pw")
    assert first["status"] == "success"
    # Oldest days first, so history fills forwards from the gap's start.
    assert windows[0][0] == start

    # Keep draining until the queue empties; it must terminate.
    guard = 0
    while gm._read_backfill_range() is not None and guard < 10:
        await gm.drain_garmin_backfill("a@b.c", "pw")
        guard += 1
    assert guard < 10, "backfill did not terminate"
    assert gm._read_backfill_range() is None

    # Every queued day was covered exactly once, with no gaps between chunks.
    covered = set()
    for w_start, w_end in windows:
        d = w_start
        while d <= w_end:
            covered.add(d)
            d += datetime.timedelta(days=1)
    expected = set()
    d = start
    while d <= end:
        expected.add(d)
        d += datetime.timedelta(days=1)
    assert covered == expected


@pytest.mark.asyncio
async def test_failed_backfill_keeps_the_days_queued(monkeypatch):
    """If the chunk fails, its days stay queued for the next run."""
    import datetime
    from backend.database import set_api_key
    import backend.routes.garmin as gm

    today = datetime.date.today()
    start = today - datetime.timedelta(days=59)
    end = today - datetime.timedelta(days=30)
    gm._write_backfill_range(start, end)

    def failing_blocking(*a, **k):
        raise RuntimeError("Garmin unavailable")
    monkeypatch.setattr(gm, "run_garmin_sync_task_blocking", failing_blocking)

    with pytest.raises(RuntimeError):
        await gm.drain_garmin_backfill("a@b.c", "pw")

    assert gm._read_backfill_range() == (start, end), "a failed chunk lost its days"
    set_api_key(gm.BACKFILL_KEY, "")


def test_unparseable_last_sync_does_not_shrink_the_window(auth_headers, monkeypatch):
    """A corrupt timestamp must not narrow every future sync to a single day."""
    from backend.database import set_api_key
    import backend.routes.garmin as gm

    set_api_key(gm.BACKFILL_KEY, "")
    set_api_key("last_sync_garmin", "not-a-timestamp")
    set_api_key("freja_garmin_email", "a@b.c")
    set_api_key("freja_garmin_password", "pw")

    captured = {}

    def fake_enqueue(fn, *args, **kwargs):
        captured["days"] = args[2]
    monkeypatch.setattr("backend.services.task_queue.enqueue_task", fake_enqueue)

    client = TestClient(app)
    assert client.get("/api/garmin/sync", headers=auth_headers).status_code == 200
    assert captured["days"] == gm.DEFAULT_SYNC_DAYS
