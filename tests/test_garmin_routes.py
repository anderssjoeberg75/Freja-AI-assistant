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
                return {'activeCalories': 600}
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
        def get_body_battery(self, *a, **k):
            # Bulk/ranged call now (#178): returns an entry only for the day that has data,
            # so a day absent from the list naturally reads as None via .get() - the same
            # outcome the old per-day RuntimeError produced for a failing date.
            if good_date is None:
                return []
            return [{'date': good_date, 'bodyBatteryValuesArray': [[0, 90]]}]
        def get_daily_steps(self, *a, **k):
            if good_date is None:
                return []
            return [{'calendarDate': good_date, 'totalSteps': 12345}]
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
            return {'activeCalories': 600}
        def get_daily_steps(self, *a, **k):
            return [{'calendarDate': target_date, 'totalSteps': 12345}]
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep data")
        def get_heart_rates(self, d):
            raise RuntimeError("no heart rate data")
        def get_hrv_data(self, d):
            raise RuntimeError("Garmin unavailable for hrv this run")
        def get_body_battery(self, *a, **k):
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


def _stub_garmin_with_activities(monkeypatch, activities):
    """Installs a fake `garminconnect` whose only interesting behavior is the activity list;
    every per-day metric call raises except get_stats, which succeeds so the sync doesn't
    hit the "no health data for any day" guard."""
    import sys
    import types

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return activities
        def get_stats(self, d):
            return {'totalSteps': 5000, 'activeCalories': 300}
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep data")
        def get_heart_rates(self, d):
            raise RuntimeError("no heart rate data")
        def get_hrv_data(self, d):
            raise RuntimeError("no hrv")
        def get_body_battery(self, d):
            raise RuntimeError("no body battery")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            raise RuntimeError("no training status")
        def get_training_readiness(self, d):
            raise RuntimeError("no readiness")

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)


def test_multi_session_day_stores_both_and_rolls_up_correctly(monkeypatch):
    """Two Garmin activities on the same day must both land in garmin_activities, and the
    garmin_health rollup must reflect the dominant (longest) session's type plus the day's
    total minutes - not just the first activity found, which used to silently drop the
    second one (#177).
    """
    import datetime
    from backend.database import get_db_connection
    from backend.routes.garmin import run_garmin_sync_task_blocking

    today = datetime.date.today()
    target_date = today.strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_health WHERE date = ?", (target_date,))
        cursor.execute("DELETE FROM garmin_activities WHERE date = ?", (target_date,))
        conn.commit()

    activities = [
        {
            'activityId': 91001,
            'startTimeLocal': f'{target_date} 06:00:00',
            'activityType': {'typeKey': 'running'},
            'activityName': 'Morgonlöpning',
            'duration': 1800,  # 30 min
            'distance': 5000.0,
            'averageHR': 140,
            'maxHR': 160,
            'calories': 350,
            'activityTrainingLoad': 60.0,
            'aerobicTrainingEffect': 3.0,
            'anaerobicTrainingEffect': 1.0,
        },
        {
            'activityId': 91002,
            'startTimeLocal': f'{target_date} 18:00:00',
            'activityType': {'typeKey': 'fitness_equipment'},
            'activityName': 'Styrketräning',
            'duration': 2700,  # 45 min - the longer, dominant session
            'distance': 0.0,
            'averageHR': 110,
            'maxHR': 130,
            'calories': 250,
            'activityTrainingLoad': 40.0,
            'aerobicTrainingEffect': 1.0,
            'anaerobicTrainingEffect': 2.0,
        },
    ]
    _stub_garmin_with_activities(monkeypatch, activities)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1, end_date=today)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT activity_id, duration_minutes FROM garmin_activities WHERE date = ? ORDER BY activity_id",
            (target_date,)
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT workout_type, workout_duration FROM garmin_health WHERE date = ?",
            (target_date,)
        )
        rollup = cursor.fetchone()

    assert [r[0] for r in rows] == ['91001', '91002']
    assert rows[0][1] == 30.0
    assert rows[1][1] == 45.0
    # Dominant session (longest) sets the label; total minutes is the sum of both sessions.
    assert rollup == ('Styrketräning', 75)


def test_activity_upsert_is_idempotent_across_overlapping_syncs(monkeypatch):
    """The recent-window sync and a backfill chunk can cover the same day/activity - the
    upsert must not create a second row for the same Garmin activity_id (#177)."""
    import datetime
    from backend.database import get_db_connection
    from backend.routes.garmin import run_garmin_sync_task_blocking

    today = datetime.date.today()
    target_date = today.strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_health WHERE date = ?", (target_date,))
        cursor.execute("DELETE FROM garmin_activities WHERE date = ?", (target_date,))
        conn.commit()

    activity = {
        'activityId': 92001,
        'startTimeLocal': f'{target_date} 07:00:00',
        'activityType': {'typeKey': 'running'},
        'activityName': 'Löprunda',
        'duration': 1500,
        'distance': 4000.0,
        'averageHR': 138,
        'maxHR': 155,
        'calories': 300,
        'activityTrainingLoad': 50.0,
        'aerobicTrainingEffect': 2.5,
        'anaerobicTrainingEffect': 0.5,
    }
    _stub_garmin_with_activities(monkeypatch, [activity])

    # Simulate the recent-window sync, then a backfill chunk that re-covers the same day.
    run_garmin_sync_task_blocking('a@b.c', 'pw', 1, end_date=today)
    run_garmin_sync_task_blocking('a@b.c', 'pw', 1, end_date=today)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM garmin_activities WHERE activity_id = ?", ('92001',)
        )
        count = cursor.fetchone()[0]

    assert count == 1, "the same Garmin activity_id must not duplicate on a re-sync"


def test_ranged_endpoints_called_once_regardless_of_window_length(monkeypatch):
    """The whole point of #178: body battery and steps must be fetched once for the sync
    window, not once per day - a 3-day window used to make 3 calls to each endpoint."""
    import datetime
    import sys
    import types
    from backend.database import get_db_connection
    from backend.routes.garmin import run_garmin_sync_task_blocking

    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=i)).strftime('%Y-%m-%d') for i in range(3)]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany("DELETE FROM garmin_health WHERE date = ?", [(d,) for d in dates])
        conn.commit()

    call_counts = {'body_battery': 0, 'daily_steps': 0}

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return []
        def get_stats(self, d):
            return {'activeCalories': 500}
        def get_body_battery(self, *a, **k):
            call_counts['body_battery'] += 1
            return [{'date': d, 'bodyBatteryValuesArray': [[0, 80]]} for d in dates]
        def get_daily_steps(self, *a, **k):
            call_counts['daily_steps'] += 1
            return [{'calendarDate': d, 'totalSteps': 8000} for d in dates]
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep")
        def get_heart_rates(self, d):
            raise RuntimeError("no hr")
        def get_hrv_data(self, d):
            raise RuntimeError("no hrv")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            raise RuntimeError("no training status")
        def get_training_readiness(self, d):
            raise RuntimeError("no readiness")

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 3)

    assert call_counts['body_battery'] == 1
    assert call_counts['daily_steps'] == 1

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, steps, body_battery FROM garmin_health WHERE date IN (?, ?, ?)",
            tuple(dates)
        )
        rows = {r[0]: (r[1], r[2]) for r in cursor.fetchall()}

    for d in dates:
        assert rows[d] == (8000, 80), f"{d} did not get its bulk-fetched steps/body_battery"


def test_bulk_metric_failure_degrades_to_none_without_failing_the_sync(monkeypatch):
    """A failing bulk call (body battery) must blank that one metric for the whole window,
    not raise and abort a sync where other metrics succeeded (#178)."""
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
        conn.commit()

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return []
        def get_stats(self, d):
            return {'activeCalories': 500}
        def get_body_battery(self, *a, **k):
            raise RuntimeError("Garmin body battery endpoint unavailable")
        def get_daily_steps(self, *a, **k):
            return [{'calendarDate': target_date, 'totalSteps': 7000}]
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep")
        def get_heart_rates(self, d):
            raise RuntimeError("no hr")
        def get_hrv_data(self, d):
            raise RuntimeError("no hrv")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            raise RuntimeError("no training status")
        def get_training_readiness(self, d):
            raise RuntimeError("no readiness")

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    # Must not raise despite the body-battery bulk call failing entirely.
    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT steps, body_battery FROM garmin_health WHERE date = ?", (target_date,)
        )
        row = cursor.fetchone()

    assert row == (7000, None)


def _training_load_fake_garmin(ts_payload):
    """A FakeGarmin whose only interesting call is get_training_status; every other metric
    call fails so the test only exercises CTL/ATL/ACWR parsing (#179)."""
    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return []
        def get_stats(self, d):
            raise RuntimeError("no stats")
        def get_body_battery(self, *a, **k):
            raise RuntimeError("no bb")
        def get_daily_steps(self, *a, **k):
            raise RuntimeError("no steps")
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep")
        def get_heart_rates(self, d):
            raise RuntimeError("no hr")
        def get_hrv_data(self, d):
            raise RuntimeError("no hrv")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            return ts_payload
        def get_training_readiness(self, d):
            raise RuntimeError("no readiness")
    return FakeGarmin


def test_training_load_parses_device_keyed_payload(monkeypatch):
    """CTL/ATL/ACWR/load-balance parse from the same get_training_status response already
    fetched for training_status/recovery_time - no extra request (#179)."""
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
        conn.commit()

    ts_payload = {
        'trainingStatus': 'PRODUCTIVE',
        'recoveryTimeInHours': 12,
        'mostRecentTrainingStatus': {
            'latestTrainingStatusData': {
                'device1': {
                    'calendarDate': target_date,
                    'acuteTrainingLoadDTO': {
                        'dailyTrainingLoadAcute': 420.5,
                        'dailyTrainingLoadChronic': 380.2,
                        'dailyAcuteChronicWorkloadRatio': 1.11,
                        'acwrStatus': 'OPTIMAL',
                    },
                },
            },
        },
        'mostRecentTrainingLoadBalance': {
            'metricsTrainingLoadBalanceDTOMap': {
                'device1': {
                    'monthlyLoadAerobicLow': 300.0,
                    'monthlyLoadAerobicHigh': 100.0,
                    'monthlyLoadAnaerobic': 50.0,
                },
            },
        },
    }

    module = types.ModuleType('garminconnect')
    module.Garmin = _training_load_fake_garmin(ts_payload)
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT training_load_acute, training_load_chronic, acwr, acwr_status, "
            "load_aerobic_low, load_aerobic_high, load_anaerobic FROM garmin_health WHERE date = ?",
            (target_date,)
        )
        row = cursor.fetchone()

    assert row == (420.5, 380.2, 1.11, 'OPTIMAL', 300.0, 100.0, 50.0)


def test_training_load_missing_dto_stores_none_without_failing(monkeypatch):
    """A get_training_status response with neither DTO block must store None for all seven
    training-load columns without failing the day (#179)."""
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
        conn.commit()

    ts_payload = {'trainingStatus': 'MAINTAINING'}  # no load DTOs at all

    module = types.ModuleType('garminconnect')
    module.Garmin = _training_load_fake_garmin(ts_payload)
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)  # must not raise

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT training_load_acute, training_load_chronic, acwr, acwr_status, "
            "load_aerobic_low, load_aerobic_high, load_anaerobic FROM garmin_health WHERE date = ?",
            (target_date,)
        )
        row = cursor.fetchone()

    assert row == (None, None, None, None, None, None, None)


def test_training_load_multiple_devices_picks_latest_calendar_date(monkeypatch):
    """Two devices in the map must resolve deterministically - the one with the most
    recent calendarDate wins (#179)."""
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
        conn.commit()

    ts_payload = {
        'trainingStatus': 'PRODUCTIVE',
        'mostRecentTrainingStatus': {
            'latestTrainingStatusData': {
                'old_device': {
                    'calendarDate': '2020-01-01',
                    'acuteTrainingLoadDTO': {
                        'dailyTrainingLoadAcute': 1.0, 'dailyTrainingLoadChronic': 1.0,
                    },
                },
                'new_device': {
                    'calendarDate': '2099-01-01',
                    'acuteTrainingLoadDTO': {
                        'dailyTrainingLoadAcute': 999.0, 'dailyTrainingLoadChronic': 888.0,
                    },
                },
            },
        },
    }

    module = types.ModuleType('garminconnect')
    module.Garmin = _training_load_fake_garmin(ts_payload)
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT training_load_acute, training_load_chronic FROM garmin_health WHERE date = ?",
            (target_date,)
        )
        row = cursor.fetchone()

    assert row == (999.0, 888.0)


def test_training_readiness_parses_and_is_unconditional(monkeypatch):
    """The readiness score/level/feedback must be stored even on a day where
    get_training_status already supplied recovery_time - previously the readiness call was
    gated behind `if recovery_time is None`, so the score was never stored on those days
    (#180). Also covers the list-shaped payload some accounts return."""
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
        conn.commit()

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return []
        def get_stats(self, d):
            raise RuntimeError("no stats")
        def get_body_battery(self, *a, **k):
            raise RuntimeError("no bb")
        def get_daily_steps(self, *a, **k):
            raise RuntimeError("no steps")
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep")
        def get_heart_rates(self, d):
            raise RuntimeError("no hr")
        def get_hrv_data(self, d):
            raise RuntimeError("no hrv")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            # Already supplies recovery_time - the old code gated the readiness call behind
            # this being absent.
            return {'trainingStatus': 'PRODUCTIVE', 'recoveryTimeInHours': 18}
        def get_training_readiness(self, d):
            return [{'score': 72, 'level': 'HIGH', 'feedbackLong': 'Redo för ett hårt pass.'}]

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT training_readiness, training_readiness_level, training_readiness_feedback, recovery_time "
            "FROM garmin_health WHERE date = ?",
            (target_date,)
        )
        row = cursor.fetchone()

    # Readiness stored despite recovery_time already being set from training_status.
    assert row == (72, 'HIGH', 'Redo för ett hårt pass.', 18)


def test_training_readiness_missing_stores_none_without_failing(monkeypatch):
    """A day with no readiness record must store None and must not fail the sync (#180)."""
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
        conn.commit()

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            return True
        def get_activities_by_date(self, *a, **k):
            return []
        def get_stats(self, d):
            return {'activeCalories': 400}
        def get_body_battery(self, *a, **k):
            raise RuntimeError("no bb")
        def get_daily_steps(self, *a, **k):
            raise RuntimeError("no steps")
        def get_sleep_data(self, d):
            raise RuntimeError("no sleep")
        def get_heart_rates(self, d):
            raise RuntimeError("no hr")
        def get_hrv_data(self, d):
            raise RuntimeError("no hrv")
        def get_max_metrics(self, d):
            raise RuntimeError("no vo2max")
        def get_training_status(self, d):
            raise RuntimeError("no training status")
        def get_training_readiness(self, d):
            raise RuntimeError("Garmin unavailable for readiness")

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    run_garmin_sync_task_blocking('a@b.c', 'pw', 1)  # must not raise

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT training_readiness, training_readiness_level, training_readiness_feedback "
            "FROM garmin_health WHERE date = ?",
            (target_date,)
        )
        row = cursor.fetchone()

    assert row == (None, None, None)


@pytest.mark.asyncio
async def test_garmin_sync_flow_classifies_auth_error(monkeypatch):
    """An authentication failure must set the auth_required sync state, not a generic
    error, so the UI can say 'log in again' rather than a raw exception string (#181)."""
    import backend.routes.garmin as gm
    from backend.services.sync_status import get_sync_states

    def raise_auth_error(*a, **k):
        from garminconnect import GarminConnectAuthenticationError
        raise GarminConnectAuthenticationError("bad credentials")

    monkeypatch.setattr(gm, "run_garmin_sync_task_blocking", raise_auth_error)

    await gm.run_garmin_sync_flow('a@b.c', 'pw', 1)

    assert get_sync_states()["states"]["garmin"] == "auth_required"


@pytest.mark.asyncio
async def test_garmin_sync_flow_classifies_rate_limit_error(monkeypatch):
    """A rate-limit failure must not be presented as a credentials problem (#181)."""
    import backend.routes.garmin as gm
    from backend.services.sync_status import get_sync_states

    def raise_rate_limit(*a, **k):
        from garminconnect import GarminConnectTooManyRequestsError
        raise GarminConnectTooManyRequestsError("slow down")

    monkeypatch.setattr(gm, "run_garmin_sync_task_blocking", raise_rate_limit)

    await gm.run_garmin_sync_flow('a@b.c', 'pw', 1)

    assert get_sync_states()["states"]["garmin"] == "rate_limited"


@pytest.mark.asyncio
async def test_garmin_sync_flow_generic_error_stays_error(monkeypatch):
    """A plain, unclassified exception must still report the generic 'error' state -
    preserving existing behavior (#181)."""
    import backend.routes.garmin as gm
    from backend.services.sync_status import get_sync_states

    def raise_generic(*a, **k):
        raise RuntimeError("network blip")

    monkeypatch.setattr(gm, "run_garmin_sync_task_blocking", raise_generic)

    await gm.run_garmin_sync_flow('a@b.c', 'pw', 1)

    assert get_sync_states()["states"]["garmin"] == "error"


def test_reauth_requires_credentials(auth_headers):
    from backend.database import set_api_key
    set_api_key('freja_garmin_email', '')
    set_api_key('freja_garmin_password', '')
    client = TestClient(app)
    response = client.post("/api/garmin/reauth", headers=auth_headers)
    assert response.status_code == 400


def test_reauth_clears_tokenstore_and_logs_in(auth_headers, monkeypatch, tmp_path):
    """A successful reauth must clear any existing tokenstore and perform a fresh login (#181)."""
    import sys
    import types
    from backend.database import set_api_key
    import backend.routes.garmin as gm

    set_api_key('freja_garmin_email', 'a@b.c')
    set_api_key('freja_garmin_password', 'pw')

    fake_token_dir = tmp_path / ".garminconnect"
    fake_token_dir.mkdir()
    (fake_token_dir / "stale_token.json").write_text("{}")
    monkeypatch.setattr(gm, "_garmin_token_dir", lambda: str(fake_token_dir))

    login_calls = []

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            login_calls.append(k)
            return True

    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    client = TestClient(app)
    response = client.post("/api/garmin/reauth", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert len(login_calls) == 1


def test_reauth_failure_is_classified(auth_headers, monkeypatch, tmp_path):
    """A reauth that fails with an auth-specific exception must classify the sync state,
    not just report a generic error (#181)."""
    import sys
    import types
    from backend.database import set_api_key
    import backend.routes.garmin as gm
    from backend.services.sync_status import get_sync_states
    from garminconnect import GarminConnectAuthenticationError as RealAuthError

    set_api_key('freja_garmin_email', 'a@b.c')
    set_api_key('freja_garmin_password', 'pw')
    monkeypatch.setattr(gm, "_garmin_token_dir", lambda: str(tmp_path / ".garminconnect"))

    class FakeGarmin:
        def __init__(self, *a, **k):
            pass
        def login(self, **k):
            raise RealAuthError("still bad")

    # The fake module must still expose the real exception classes: both this test's
    # `raise` and _classify_garmin_error()'s lazy re-import resolve `garminconnect` through
    # sys.modules, which now points at this fake module rather than the real package.
    module = types.ModuleType('garminconnect')
    module.Garmin = FakeGarmin
    module.GarminConnectAuthenticationError = RealAuthError
    monkeypatch.setitem(sys.modules, 'garminconnect', module)

    client = TestClient(app)
    response = client.post("/api/garmin/reauth", headers=auth_headers)
    assert response.status_code == 400
    assert get_sync_states()["states"]["garmin"] == "auth_required"


def test_token_age_warning_reported_when_stale(auth_headers, monkeypatch, tmp_path):
    """A cached tokenstore older than the warning threshold must surface as stale (#181)."""
    import os
    import time
    import backend.routes.garmin as gm

    fake_token_dir = tmp_path / ".garminconnect"
    fake_token_dir.mkdir()
    token_file = fake_token_dir / "oauth2_token.json"
    token_file.write_text("{}")
    old_time = time.time() - (200 * 86400)  # 200 days old
    os.utime(token_file, (old_time, old_time))
    monkeypatch.setattr(gm, "_garmin_token_dir", lambda: str(fake_token_dir))

    client = TestClient(app)
    response = client.get("/api/garmin/credentials", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["token_age_days"] >= 199
    assert data["token_stale_warning"] is True


def test_token_age_no_warning_when_tokenstore_missing(auth_headers, monkeypatch, tmp_path):
    """No tokenstore yet (never connected) must not be reported as a stale one (#181)."""
    import backend.routes.garmin as gm

    monkeypatch.setattr(gm, "_garmin_token_dir", lambda: str(tmp_path / "does_not_exist"))

    client = TestClient(app)
    response = client.get("/api/garmin/credentials", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["token_age_days"] is None
    assert data["token_stale_warning"] is False


def _clear_garmin_activities_and_detail():
    """Full-table clear for the T-182 tests below - unlike the other Garmin tests, these
    are sensitive to any leftover unfetched-detail row from earlier tests in the same
    session, since fetch_activity_details() scans the whole table by default."""
    from backend.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activities")
        cursor.execute("DELETE FROM garmin_activity_detail")
        conn.commit()


def test_fetch_activity_details_skips_already_fetched():
    """An activity that already has detail_fetched_at must not trigger a get_activity call -
    a completed activity is immutable, so detail only needs fetching once, ever (#182)."""
    import datetime
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes, detail_fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t182-already', '2026-01-01', '2026-01-01 07:00:00', 'Löpning', 30.0,
             datetime.datetime.now().isoformat())
        )
        conn.commit()

    calls = []

    class FakeClient:
        def get_activity(self, activity_id):
            calls.append(activity_id)
            return {'summaryDTO': {}}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert calls == []
    assert result == {"fetched": 0, "failed": 0, "remaining": 0}


def test_fetch_activity_details_stores_summary_and_stamps_marker():
    """A successful fetch stores the summaryDTO fields and stamps detail_fetched_at (#182)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            ('t182-new', '2026-01-02', '2026-01-02 07:00:00', 'Löpning', 45.0)
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            return {
                'summaryDTO': {
                    'recoveryTimeInHours': 24,
                    'trainingEffectLabel': 'AEROBIC_BASE',
                    'trainingEffectMessage': 'Bra grundträning.',
                    'avgGroundContactTime': 250.0,
                    'vO2MaxValue': 52.0,
                }
            }

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert result == {"fetched": 1, "failed": 0, "remaining": 0}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT recovery_time_hours, training_effect_label, vo2max_value "
            "FROM garmin_activity_detail WHERE activity_id = ?",
            ('t182-new',)
        )
        detail_row = cursor.fetchone()
        cursor.execute(
            "SELECT detail_fetched_at FROM garmin_activities WHERE activity_id = ?", ('t182-new',)
        )
        marker = cursor.fetchone()[0]

    assert detail_row == (24, 'AEROBIC_BASE', 52.0)
    assert marker is not None


def test_fetch_activity_details_failure_leaves_marker_null_and_does_not_abort_others():
    """A failing detail fetch must leave detail_fetched_at NULL for retry, and must not
    abort the pass for the remaining activities (#182)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ('t182-fail', '2026-01-03', '2026-01-03 07:00:00', 'Löpning', 30.0),
                ('t182-ok', '2026-01-04', '2026-01-04 07:00:00', 'Löpning', 30.0),
            ]
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            if activity_id == 't182-fail':
                raise RuntimeError("Garmin unavailable")
            return {'summaryDTO': {'recoveryTimeInHours': 10}}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert result["fetched"] == 1
    assert result["failed"] == 1

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT detail_fetched_at FROM garmin_activities WHERE activity_id = ?", ('t182-fail',)
        )
        failed_marker = cursor.fetchone()[0]
        cursor.execute(
            "SELECT detail_fetched_at FROM garmin_activities WHERE activity_id = ?", ('t182-ok',)
        )
        ok_marker = cursor.fetchone()[0]

    assert failed_marker is None
    assert ok_marker is not None


def test_fetch_activity_details_cap_defers_remainder():
    """A window with more unfetched activities than the cap must defer the remainder and
    report it, rather than a capped pass silently reading as complete (#182)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (f't182-cap-{i}', f'2026-02-{i + 1:02d}', f'2026-02-{i + 1:02d} 07:00:00', 'Löpning', 30.0)
                for i in range(3)
            ]
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(FakeClient(), cursor, limit=2)
        conn.commit()

    assert result == {"fetched": 2, "failed": 0, "remaining": 1}


def test_fetch_activity_details_since_date_filter_excludes_older_activities():
    """The automatic per-sync pass's since_date filter must exclude activities predating it,
    leaving them for the deliberate backfill endpoint instead (#182)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ('t182-old', '2020-01-01', '2020-01-01 07:00:00', 'Löpning', 30.0),
                ('t182-recent', '2026-08-01', '2026-08-01 07:00:00', 'Löpning', 30.0),
            ]
        )
        conn.commit()

    calls = []

    class FakeClient:
        def get_activity(self, activity_id):
            calls.append(activity_id)
            return {'summaryDTO': {}}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(FakeClient(), cursor, since_date='2026-07-24')
        conn.commit()

    assert calls == ['t182-recent']
    assert result["fetched"] == 1


def test_strength_sets_import_two_exercises_excludes_rest():
    """A two-exercise strength activity must produce two trainer_strength_logs rows with
    correct set counts, with REST sets excluded (#183)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import _import_garmin_strength_sets

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_strength_logs WHERE activity_id = ?", ('t183-act1',))
        conn.commit()

    payload = {
        'exerciseSets': [
            {'exerciseName': 'BARBELL_BACK_SQUAT', 'repetitionCount': 5, 'weight': 100000, 'setType': 'ACTIVE'},
            {'exerciseName': 'BARBELL_BACK_SQUAT', 'repetitionCount': 5, 'weight': 100000, 'setType': 'ACTIVE'},
            {'exerciseName': None, 'setType': 'REST'},
            {'exerciseName': 'BENCH_PRESS', 'repetitionCount': 8, 'weight': 60000, 'setType': 'ACTIVE'},
        ]
    }

    with get_db_connection() as conn:
        cursor = conn.cursor()
        imported = _import_garmin_strength_sets(cursor, 't183-act1', '2026-03-01', payload)
        conn.commit()

    assert imported == 2

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT exercise_name, sets, reps, weight, source, activity_id FROM trainer_strength_logs "
            "WHERE activity_id = ? ORDER BY exercise_name",
            ('t183-act1',)
        )
        rows = {r[0]: r for r in cursor.fetchall()}

    assert rows['Knäböj'] == ('Knäböj', 2, 5, 100.0, 'garmin', 't183-act1')
    assert rows['Bänkpress'] == ('Bänkpress', 1, 8, 60.0, 'garmin', 't183-act1')


def test_strength_sets_reimport_is_idempotent():
    """Re-importing the same activity must replace its rows, not duplicate them (#183)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import _import_garmin_strength_sets

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_strength_logs WHERE activity_id = ?", ('t183-act2',))
        conn.commit()

    payload = {'exerciseSets': [
        {'exerciseName': 'DEADLIFT', 'repetitionCount': 5, 'weight': 120000, 'setType': 'ACTIVE'}
    ]}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        _import_garmin_strength_sets(cursor, 't183-act2', '2026-03-02', payload)
        _import_garmin_strength_sets(cursor, 't183-act2', '2026-03-02', payload)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM trainer_strength_logs WHERE activity_id = ?", ('t183-act2',)
        )
        count = cursor.fetchone()[0]

    assert count == 1


def test_strength_sets_import_does_not_touch_manual_rows():
    """A manual row must survive a Garmin import for a different activity on the same
    date (#183)."""
    import datetime
    from backend.database import get_db_connection
    from backend.routes.garmin import _import_garmin_strength_sets

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM trainer_strength_logs WHERE date = ? AND exercise_name = ?",
            ('2026-03-03', 'Knäböj (manuell)')
        )
        cursor.execute(
            "INSERT INTO trainer_strength_logs (date, exercise_name, sets, reps, weight, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'manual')",
            ('2026-03-03', 'Knäböj (manuell)', 3, 5, 90.0, datetime.datetime.now().isoformat())
        )
        conn.commit()

    payload = {'exerciseSets': [
        {'exerciseName': 'BENCH_PRESS', 'repetitionCount': 5, 'weight': 60000, 'setType': 'ACTIVE'}
    ]}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _import_garmin_strength_sets(cursor, 't183-act3', '2026-03-03', payload)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sets, source FROM trainer_strength_logs WHERE date = ? AND exercise_name = ?",
            ('2026-03-03', 'Knäböj (manuell)')
        )
        manual_row = cursor.fetchone()

    assert manual_row == (3, 'manual')


def test_strength_sets_unmapped_exercise_name_is_prettified_not_dropped():
    """An unmapped Garmin exercise name must still be stored, prettified rather than
    lost (#183)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import _import_garmin_strength_sets

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_strength_logs WHERE activity_id = ?", ('t183-act4',))
        conn.commit()

    payload = {'exerciseSets': [
        {'exerciseName': 'CABLE_FLY', 'repetitionCount': 12, 'weight': 15000, 'setType': 'ACTIVE'}
    ]}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _import_garmin_strength_sets(cursor, 't183-act4', '2026-03-04', payload)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT exercise_name FROM trainer_strength_logs WHERE activity_id = ?", ('t183-act4',)
        )
        name = cursor.fetchone()[0]

    assert name == 'Cable fly'


def test_non_strength_activity_does_not_fetch_exercise_sets():
    """A run must not trigger get_activity_exercise_sets - no point calling it (#183)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t183-run', '2026-03-05', '2026-03-05 07:00:00', 'Löpning', 'running', 30.0)
        )
        conn.commit()

    exercise_set_calls = []

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_exercise_sets(self, activity_id):
            exercise_set_calls.append(activity_id)
            return {'exerciseSets': []}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert exercise_set_calls == []


def test_strength_activity_triggers_exercise_sets_fetch_via_detail_pass():
    """A strength-type activity must trigger get_activity_exercise_sets in the same detail
    pass, and the resulting sets must land in trainer_strength_logs (#183)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_strength_logs WHERE activity_id = ?", ('t183-strength',))
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t183-strength', '2026-03-06', '2026-03-06 18:00:00', 'Styrketräning', 'fitness_equipment', 50.0)
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_exercise_sets(self, activity_id):
            return {'exerciseSets': [
                {'exerciseName': 'SQUAT', 'repetitionCount': 5, 'weight': 80000, 'setType': 'ACTIVE'}
            ]}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT exercise_name, weight FROM trainer_strength_logs WHERE activity_id = ?",
            ('t183-strength',)
        )
        row = cursor.fetchone()

    assert row == ('Knäböj', 80.0)


def test_zone_percentages_handles_zero_total_without_dividing_by_zero():
    """A session with no zone time at all must return None, not a ZeroDivisionError (#184)."""
    from backend.routes.garmin import zone_percentages
    assert zone_percentages(None, None, None, None, None) == {"easy_pct": None, "hard_pct": None}
    assert zone_percentages(0, 0, 0, 0, 0) == {"easy_pct": None, "hard_pct": None}


def test_zone_percentages_computes_easy_and_hard_shares():
    """easy_pct is zones 1-2, hard_pct is zones 4-5, as a share of total in-zone time (#184)."""
    from backend.routes.garmin import zone_percentages
    # 100 total: 40 easy (z1+z2), 30 moderate (z3), 30 hard (z4+z5).
    result = zone_percentages(20, 20, 30, 15, 15)
    assert result == {"easy_pct": 40.0, "hard_pct": 30.0}


def test_hr_zones_stored_for_a_session_with_all_five_zones():
    """A session with time in all five zones must store correctly via the detail pass (#184)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activity_zones WHERE activity_id = ?", ('t184-zones',))
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t184-zones', '2026-04-01', '2026-04-01 07:00:00', 'Löpning', 'running', 45.0)
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_hr_in_timezones(self, activity_id):
            return [
                {'zoneNumber': 1, 'secsInZone': 300},
                {'zoneNumber': 2, 'secsInZone': 900},
                {'zoneNumber': 3, 'secsInZone': 600},
                {'zoneNumber': 4, 'secsInZone': 300},
                {'zoneNumber': 5, 'secsInZone': 100},
            ]

    with get_db_connection() as conn:
        cursor = conn.cursor()
        fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT secs_zone_1, secs_zone_2, secs_zone_3, secs_zone_4, secs_zone_5 "
            "FROM garmin_activity_zones WHERE activity_id = ?",
            ('t184-zones',)
        )
        row = cursor.fetchone()

    assert row == (300, 900, 600, 300, 100)


def test_hr_zones_no_data_stores_no_row_and_does_not_fail():
    """An activity with no HR-zone data must store no row and must not fail the detail
    pass (#184)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activity_zones WHERE activity_id = ?", ('t184-nozones',))
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t184-nozones', '2026-04-02', '2026-04-02 07:00:00', 'Löpning', 'running', 20.0)
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_hr_in_timezones(self, activity_id):
            return []  # no HR data for this session

    with get_db_connection() as conn:
        cursor = conn.cursor()
        result = fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert result["failed"] == 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM garmin_activity_zones WHERE activity_id = ?", ('t184-nozones',)
        )
        count = cursor.fetchone()[0]

    assert count == 0


def test_strength_activity_skips_hr_zone_fetch():
    """A strength-type activity must not trigger get_activity_hr_in_timezones - zone time
    is not meaningful there (#184)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t184-strength', '2026-04-03', '2026-04-03 18:00:00', 'Styrketräning', 'fitness_equipment', 40.0)
        )
        conn.commit()

    zone_calls = []

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_exercise_sets(self, activity_id):
            return {'exerciseSets': []}
        def get_activity_hr_in_timezones(self, activity_id):
            zone_calls.append(activity_id)
            return []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert zone_calls == []


def test_garmin_zones_endpoint_returns_easy_hard_pct(auth_headers):
    """GET /api/garmin/zones must include the derived easy_pct/hard_pct per session (#184)."""
    import datetime
    from backend.database import get_db_connection

    recent_date = datetime.date.today().strftime('%Y-%m-%d')
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activities WHERE activity_id = ?", ('t184-endpoint',))
        cursor.execute("DELETE FROM garmin_activity_zones WHERE activity_id = ?", ('t184-endpoint',))
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            ('t184-endpoint', recent_date, f'{recent_date} 07:00:00', 'Löpning', 30.0)
        )
        cursor.execute(
            "INSERT INTO garmin_activity_zones (activity_id, secs_zone_1, secs_zone_2, secs_zone_3, secs_zone_4, secs_zone_5) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t184-endpoint', 600, 600, 0, 0, 0)
        )
        conn.commit()

    client = TestClient(app)
    response = client.get("/api/garmin/zones?days=30", headers=auth_headers)
    assert response.status_code == 200
    matching = [r for r in response.json() if r['activity_id'] == 't184-endpoint']
    assert len(matching) == 1
    assert matching[0]['easy_pct'] == 100.0
    assert matching[0]['hard_pct'] == 0.0


def test_laps_multi_lap_session_stores_one_row_per_lap_in_order():
    """A multi-lap session must produce one row per lap, preserving lap order (#185)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activity_laps WHERE activity_id = ?", ('t185-multi',))
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes, lap_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ('t185-multi', '2026-05-01', '2026-05-01 07:00:00', 'Löpning', 'running', 30.0, 3)
        )
        conn.commit()

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_hr_in_timezones(self, activity_id):
            return []
        def get_activity_splits(self, activity_id):
            return {'lapDTOs': [
                {'distance': 1000.0, 'duration': 240.0, 'averageHR': 150, 'intensityType': 'ACTIVE'},
                {'distance': 1000.0, 'duration': 245.0, 'averageHR': 155, 'intensityType': 'ACTIVE'},
                {'distance': 500.0, 'duration': 130.0, 'averageHR': 140, 'intensityType': 'COOLDOWN'},
            ]}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT lap_index, distance_m, intensity_type FROM garmin_activity_laps "
            "WHERE activity_id = ? ORDER BY lap_index",
            ('t185-multi',)
        )
        rows = cursor.fetchall()

    assert rows == [(0, 1000.0, 'ACTIVE'), (1, 1000.0, 'ACTIVE'), (2, 500.0, 'COOLDOWN')]


def test_laps_single_lap_activity_triggers_no_request():
    """An unstructured single-lap activity must not trigger get_activity_splits at
    all (#185)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import fetch_activity_details

    _clear_garmin_activities_and_detail()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, raw_type_key, duration_minutes, lap_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ('t185-single', '2026-05-02', '2026-05-02 07:00:00', 'Löpning', 'running', 20.0, 1)
        )
        conn.commit()

    splits_calls = []

    class FakeClient:
        def get_activity(self, activity_id):
            return {'summaryDTO': {}}
        def get_activity_hr_in_timezones(self, activity_id):
            return []
        def get_activity_splits(self, activity_id):
            splits_calls.append(activity_id)
            return {'lapDTOs': []}

    with get_db_connection() as conn:
        cursor = conn.cursor()
        fetch_activity_details(FakeClient(), cursor)
        conn.commit()

    assert splits_calls == []


def test_laps_reimport_replaces_not_duplicates():
    """Re-importing laps for the same activity must replace, not duplicate, its rows (#185)."""
    from backend.database import get_db_connection
    from backend.routes.garmin import _import_garmin_laps

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activity_laps WHERE activity_id = ?", ('t185-reimport',))
        conn.commit()

    payload = {'lapDTOs': [{'distance': 1000.0, 'duration': 240.0, 'intensityType': 'ACTIVE'}]}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _import_garmin_laps(cursor, 't185-reimport', '2026-05-03', payload)
        _import_garmin_laps(cursor, 't185-reimport', '2026-05-03', payload)
        conn.commit()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM garmin_activity_laps WHERE activity_id = ?", ('t185-reimport',)
        )
        count = cursor.fetchone()[0]

    assert count == 1


def test_laps_endpoint_returns_laps_in_order(auth_headers):
    """GET /api/garmin/activities/{id}/laps must return laps ordered by lap_index (#185)."""
    from backend.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activity_laps WHERE activity_id = ?", ('t185-endpoint',))
        cursor.executemany(
            "INSERT INTO garmin_activity_laps (activity_id, lap_index, date, distance_m, intensity_type) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ('t185-endpoint', 1, '2026-05-04', 1000.0, 'ACTIVE'),
                ('t185-endpoint', 0, '2026-05-04', 1000.0, 'ACTIVE'),
            ]
        )
        conn.commit()

    client = TestClient(app)
    response = client.get("/api/garmin/activities/t185-endpoint/laps", headers=auth_headers)
    assert response.status_code == 200
    laps = response.json()
    assert [l['lap_index'] for l in laps] == [0, 1]


def test_backfill_detail_endpoint_requires_credentials(auth_headers):
    from backend.database import set_api_key
    set_api_key('freja_garmin_email', '')
    set_api_key('freja_garmin_password', '')
    client = TestClient(app)
    response = client.post("/api/garmin/activities/backfill-detail", headers=auth_headers)
    assert response.status_code == 400


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
