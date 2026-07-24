import json
import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, get_db_connection
import backend.routes.trainer as trainer_module

@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}

def test_trainer_plan_update(auth_headers):
    client = TestClient(app)
    # Insert a dummy plan to test PUT endpoint
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trainer_plans (date, goal, advice_text, limitations)
            VALUES (?, ?, ?, ?)
        ''', ("2026-07-02", "Minskt fett och bygg muskler", "Mock råd", "Inga"))
        conn.commit()
        plan_id = cursor.lastrowid

    update_payload = {
        "plan_id": plan_id,
        "advice_text": "Uppdaterat råd från PT AI"
    }

    response = client.put("/api/trainer/plans", json=update_payload, headers=auth_headers)
    assert response.status_code == 200
    assert response.json().get("status") == "success"

def test_trainer_plan_update_rejects_missing_plan(auth_headers):
    """Updating a non-existent plan_id must 404, not silently report success (no rowcount check
    previously meant the UPDATE matched zero rows and the endpoint still returned 200)."""
    client = TestClient(app)
    response = client.put(
        "/api/trainer/plans",
        json={"plan_id": 999999999, "advice_text": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 404

def test_trainer_plan_update_rejects_oversized_payload(auth_headers):
    """A gigantic advice_text (or a plan with an absurd number of workouts) must be rejected
    up front - plan_occurrences/build_ics/build_pdf iterate every workout synchronously inside
    the request handler with no cap, so an unbounded payload here is a same-request DoS vector."""
    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trainer_plans (date, goal, advice_text, limitations)
            VALUES (?, ?, ?, ?)
        ''', ("2026-07-02", "Cap test", "Mock", "Inga"))
        conn.commit()
        plan_id = cursor.lastrowid

    huge_text_response = client.put(
        "/api/trainer/plans",
        json={"plan_id": plan_id, "advice_text": "x" * 200_001},
        headers=auth_headers,
    )
    assert huge_text_response.status_code == 400

    huge_workouts = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Pass", "duration_minutes": 30}
        for _ in range(121)
    ]})
    huge_workouts_response = client.put(
        "/api/trainer/plans",
        json={"plan_id": plan_id, "advice_text": huge_workouts},
        headers=auth_headers,
    )
    assert huge_workouts_response.status_code == 400

def test_trainer_plan_booking(auth_headers):
    client = TestClient(app)
    mock_advice_json = '{"workouts": [{"day": "Måndag", "activity_type": "Löpning", "title": "Intervaller", "description": "Spring snabbt", "duration_minutes": 30}]}'
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trainer_plans (date, goal, advice_text, limitations)
            VALUES (?, ?, ?, ?)
        ''', ("2026-07-02", "Boka test", mock_advice_json, "Inga"))
        conn.commit()
        plan_id = cursor.lastrowid

    booking_payload = {
        "plan_id": plan_id,
        "start_date": "2026-07-06"
    }

    response = client.post("/api/trainer/plans/book", json=booking_payload, headers=auth_headers)
    assert response.status_code == 200
    assert response.json().get("status") == "success"

def test_trainer_checkin_requires_llm_provider(auth_headers, monkeypatch):
    # Force the "no provider available" branch (Ollama unreachable, no Gemini key either).
    async def _fake_no_provider(prompt, schema=None, **kwargs):
        raise Exception(
            "No LLM provider available: Ollama request failed (connection refused) and no "
            "Gemini API key is configured."
        )
    monkeypatch.setattr(trainer_module.checkin.llm_client, "generate_json", _fake_no_provider)
    client = TestClient(app)

    response = client.post("/api/trainer/checkin", json={}, headers=auth_headers)
    assert response.status_code == 500
    assert "Gemini" in response.json().get("detail", "")

def test_trainer_checkin_success(auth_headers, monkeypatch):
    # Provide a Gemini key and mock both the weather forecast and the Gemini call.
    monkeypatch.setattr(trainer_module.checkin, "get_api_key", lambda name: "MOCK_GEMINI_KEY")

    # The check-in now syncs the wearables first. get_api_key is stubbed truthy above, so
    # without this the real Garmin/Strava/Withings clients would fire on real credentials.
    async def fake_refresh(days=trainer_module.CHECKIN_SYNC_DAYS):
        return {"garmin": "synced", "strava": "synced", "withings": "synced"}
    monkeypatch.setattr(trainer_module.checkin, "refresh_health_sources_for_checkin", fake_refresh)

    async def fake_weather(location="Stockholm"):
        return f"Väderprognos för {location}: idag klart, 15°C till 22°C."
    monkeypatch.setattr(trainer_module.checkin, "fetch_7day_weather_forecast", fake_weather)

    checkin_obj = {
        "sleep_summary": "Du sov 7,5h – bra återhämtning.",
        "recovery_summary": "Vilopuls och HRV stabila, Body Battery laddade fullt.",
        "yesterday_status": "Du körde gårdagens pass, snyggt jobbat!",
        "todays_plan": "30 min lugnt löppass.",
        "recommendation": "Kör planen som den är – kroppen är redo.",
        "adjust_workout": False,
        "weather_note": "Klart väder, perfekt för utepass.",
        "closing_question": "Kör vi originalplanen idag?",
        "briefing": "**God morgon! ☀️** Allt ser bra ut – kör dagens 30 min lugnt. Redo?"
    }

    monkeypatch.setattr(trainer_module.checkin.llm_client, "generate_json", _fake_llm_json(checkin_obj))
    client = TestClient(app)

    response = client.post("/api/trainer/checkin", json={"location": "Stockholm"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "success"
    assert data.get("checkin", {}).get("briefing")
    assert data.get("checkin", {}).get("adjust_workout") is False
    assert data.get("calendar_updated") is False
    assert "adherence" in data
    # The freshness map from the pre-check-in wearable sync rides along in the response.
    assert data.get("sync") == {"garmin": "synced", "strava": "synced", "withings": "synced"}


def test_trainer_checkin_skips_sync_without_credentials(auth_headers, monkeypatch):
    # Only the Gemini key is configured; the wearable credentials are absent. The real
    # refresh must then skip every provider (no network) and the check-in must still brief.
    def fake_key(name):
        return "MOCK_GEMINI_KEY" if name == "freja_gemini_apikey" else ""
    monkeypatch.setattr(trainer_module.checkin, "get_api_key", fake_key)

    async def fake_weather(location="Stockholm"):
        return f"Väderprognos för {location}: idag klart, 15°C."
    monkeypatch.setattr(trainer_module.checkin, "fetch_7day_weather_forecast", fake_weather)

    checkin_obj = {
        "sleep_summary": "s", "recovery_summary": "r", "yesterday_status": "y",
        "todays_plan": "p", "recommendation": "rec", "adjust_workout": False,
        "closing_question": "q", "briefing": "**Morgon!**",
    }
    monkeypatch.setattr(trainer_module.checkin.llm_client, "generate_json", _fake_llm_json(checkin_obj))
    client = TestClient(app)

    response = client.post("/api/trainer/checkin", json={}, headers=auth_headers)
    assert response.status_code == 200
    sync = response.json().get("sync", {})
    assert set(sync.keys()) == {"garmin", "strava", "withings"}
    assert all(v == "skipped (no credentials)" for v in sync.values())


def test_refresh_health_sources_reports_per_provider(monkeypatch):
    # Each provider's sync helper is stubbed; refresh_health_sources_for_checkin must run
    # them concurrently and return one status per provider.
    async def ok(days):
        return "synced"
    async def boom(days):
        return "failed: token expired"
    monkeypatch.setattr(trainer_module.checkin, "_sync_garmin_for_checkin", ok)
    monkeypatch.setattr(trainer_module.checkin, "_sync_strava_for_checkin", boom)
    monkeypatch.setattr(trainer_module.checkin, "_sync_withings_for_checkin", ok)

    import asyncio
    result = asyncio.run(trainer_module.refresh_health_sources_for_checkin())
    assert result == {
        "garmin": "synced",
        "strava": "failed: token expired",
        "withings": "synced",
    }


def _fake_llm_json(payload_obj):
    """Async stand-in for llm_client.generate_json that always answers payload_obj,
    regardless of the prompt/schema passed in."""
    async def _fake(prompt, schema=None, **kwargs):
        return payload_obj
    return _fake


def test_trainer_profile_put_and_get(auth_headers):
    client = TestClient(app)
    payload = {
        "event": "Göteborgsvarvet halvmara",
        "event_date": "2026-05-16",
        "fitness_level": "Motionär",
        "availability": "3 dagar/vecka, 45 min",
        "goals": "Klara loppet under 2 timmar",
        "limitations": "Ansträngningsastma",
        "location": "Göteborg",
        "baseline_resting_hr": 54
    }
    put_res = client.put("/api/trainer/profile", json=payload, headers=auth_headers)
    assert put_res.status_code == 200
    assert put_res.json().get("status") == "success"

    get_res = client.get("/api/trainer/profile", headers=auth_headers)
    assert get_res.status_code == 200
    profile = get_res.json()
    assert profile.get("location") == "Göteborg"
    assert profile.get("limitations") == "Ansträngningsastma"


def test_trainer_generate_success(auth_headers, monkeypatch):
    async def fake_weather(location="Stockholm"):
        return f"Väderprognos för {location}: idag mulet, 12°C."
    monkeypatch.setattr(trainer_module.generation, "fetch_7day_weather_forecast", fake_weather)

    plan_obj = {
        "summary": "Stabil grund, god återhämtning.",
        "resting_hr_trend": "Stabil.",
        "hrv_trend": "God.",
        "weekly_focus": "Bygg uthållighet lugnt.",
        "workouts": [
            {"day": "Måndag", "activity_type": "Löpning", "title": "Lugnt pass",
             "description": "30 min i samtalstempo.", "duration_minutes": 30}
        ]
    }
    monkeypatch.setattr(trainer_module.generation.llm_client, "generate_json", _fake_llm_json(plan_obj))

    client = TestClient(app)
    response = client.post(
        "/api/trainer/generate",
        json={"goal": "Bygg löpvana", "limitations": ""},
        headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "success"
    assert data.get("plan_id")
    assert "workouts" in data.get("advice_text", "")


def test_trainer_adherence_endpoint(auth_headers):
    client = TestClient(app)
    response = client.get("/api/trainer/adherence?days=14", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("window_days") == 14
    assert "planned" in data and "completed" in data


def _insert_workout_event(summary, start_time, end_time, google_event_id):
    """Insert a workout calendar event and return its DB id.

    The test DB persists between runs, so clear any prior row with the same
    google_event_id first to keep the insert idempotent (UNIQUE constraint).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM google_calendar_events WHERE google_event_id = ?", (google_event_id,))
        cursor.execute(
            '''INSERT INTO google_calendar_events
               (google_event_id, summary, description, start_time, end_time, location)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (google_event_id, summary, "Ursprunglig beskrivning", start_time, end_time, "F.R.E.J.A. PT")
        )
        conn.commit()
        return cursor.lastrowid


def test_trainer_optimize_requires_llm_provider(auth_headers, monkeypatch):
    import datetime

    async def _fake_no_provider(prompt, schema=None, **kwargs):
        raise Exception(
            "No LLM provider available: Ollama request failed (connection refused) and no "
            "Gemini API key is configured."
        )
    monkeypatch.setattr(trainer_module.optimize.llm_client, "generate_json", _fake_no_provider)
    today = datetime.date.today().strftime('%Y-%m-%d')
    _insert_workout_event("💪 Löpning: Tempo", f"{today}T08:00", f"{today}T09:00", "evt-optim-nokey")

    client = TestClient(app)
    response = client.post("/api/trainer/optimize", json={}, headers=auth_headers)
    assert response.status_code == 500
    assert "Gemini" in response.json().get("detail", "")


def test_trainer_optimize_reduces_upcoming_workout(auth_headers, monkeypatch):
    import datetime

    today = datetime.date.today().strftime('%Y-%m-%d')
    event_id = _insert_workout_event(
        "💪 Löpning: Intervaller", f"{today}T08:00", f"{today}T09:00", "evt-optim-reduce"
    )

    opt_obj = {
        "assessment": "Låg HRV och kort sömn – kroppen behöver avlastning.",
        "briefing": "**Justerat:** Jag kortade intervallpasset då din HRV sjunkit.",
        "adjustments": [
            {"event_id": event_id, "action": "reduce", "new_duration_minutes": 20,
             "new_title": "", "reason": "Låg HRV, sänkt belastning."}
        ]
    }
    monkeypatch.setattr(trainer_module.optimize.llm_client, "generate_json", _fake_llm_json(opt_obj))

    client = TestClient(app)
    response = client.post("/api/trainer/optimize", json={}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "success"
    assert data.get("changes_count") == 1
    assert data.get("considered") >= 1

    # The stored event should now end 20 minutes after it starts.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT end_time FROM google_calendar_events WHERE id = ?", (event_id,))
        end_time = cursor.fetchone()[0]
    assert end_time.startswith(f"{today}T08:20")


def test_trainer_optimize_keeps_when_recovered(auth_headers, monkeypatch):
    import datetime

    today = datetime.date.today().strftime('%Y-%m-%d')
    event_id = _insert_workout_event(
        "🏃 Löpning: Lugnt pass", f"{today}T08:00", f"{today}T08:40", "evt-optim-keep"
    )

    opt_obj = {
        "assessment": "God återhämtning – kör planen.",
        "briefing": "**Allt ser bra ut** – passen får stå kvar oförändrade.",
        "adjustments": [
            {"event_id": event_id, "action": "keep", "new_duration_minutes": 40,
             "new_title": "", "reason": "God återhämtning."}
        ]
    }
    monkeypatch.setattr(trainer_module.optimize.llm_client, "generate_json", _fake_llm_json(opt_obj))

    client = TestClient(app)
    response = client.post("/api/trainer/optimize", json={}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "success"
    assert data.get("changes_count") == 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT end_time FROM google_calendar_events WHERE id = ?", (event_id,))
        end_time = cursor.fetchone()[0]
    assert end_time.startswith(f"{today}T08:40")  # unchanged


def test_trainer_baselines_refresh(auth_headers):
    """Forcing a baseline refresh averages the seeded Garmin data into the profile."""
    client = TestClient(app)
    response = client.post(
        "/api/trainer/baselines/refresh", json={"force": True}, headers=auth_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "success"
    updated = data.get("updated", {})
    # The seed data has resting_hr, sleep_hours and hrv for several days.
    assert "baseline_resting_hr" in updated
    assert updated["baseline_resting_hr"] > 0

    # The profile now carries the recomputed baseline and a refresh timestamp.
    profile = client.get("/api/trainer/profile", headers=auth_headers).json()
    assert profile.get("baseline_resting_hr") is not None
    assert profile.get("baselines_updated_at")


def test_trainer_baselines_weekly_cadence(auth_headers):
    """A non-forced recompute right after a forced one is skipped (weekly cadence)."""
    import backend.routes.trainer as tm
    # Force once so baselines_updated_at is fresh.
    tm.recompute_health_baselines(force=True)
    # A non-forced call within the window must be a no-op.
    result = tm.recompute_health_baselines(force=False)
    assert result.get("status") == "skipped"
    assert result.get("reason") == "refreshed_recently"


def test_trainer_strength_log_roundtrip(auth_headers):
    """A logged strength set can be created, listed and deleted."""
    client = TestClient(app)
    payload = {
        "exercise_name": "Knäböj",
        "sets": 3,
        "reps": 8,
        "weight": 80.0,
        "rpe": 8,
    }
    add_res = client.post("/api/trainer/strength/log", json=payload, headers=auth_headers)
    assert add_res.status_code == 200
    log_id = add_res.json().get("id")
    assert log_id

    get_res = client.get("/api/trainer/strength/log?limit=50", headers=auth_headers)
    assert get_res.status_code == 200
    logs = get_res.json().get("logs", [])
    assert any(l["id"] == log_id and l["exercise_name"] == "Knäböj" for l in logs)

    del_res = client.delete(f"/api/trainer/strength/log?log_id={log_id}", headers=auth_headers)
    assert del_res.status_code == 200
    assert del_res.json().get("status") == "success"


def test_trainer_strength_log_requires_name(auth_headers):
    client = TestClient(app)
    res = client.post("/api/trainer/strength/log", json={"sets": 3}, headers=auth_headers)
    assert res.status_code == 400


def test_trainer_booking_includes_exercises(auth_headers):
    """A strength workout with an exercises block books its exercises into the event."""
    client = TestClient(app)
    plan_json = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Styrketräning", "title": "Underkropp",
         "description": "Tunga baslyft", "duration_minutes": 50,
         "exercises": [
             {"name": "Knäböj", "sets": 4, "reps": 6, "target_weight": 90, "rpe": 8},
             {"name": "Marklyft", "sets": 3, "reps": 5, "target_weight": 110}
         ]}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-07-06", "Styrketest", plan_json, "Inga")
        )
        conn.commit()
        plan_id = cursor.lastrowid

    payload = {"plan_id": plan_id, "start_date": "2026-07-06"}
    res = client.post("/api/trainer/plans/book", json=payload, headers=auth_headers)
    assert res.status_code == 200
    assert res.json().get("booked_count") == 1

    # The booked calendar event description should carry the exercise block.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_id FROM trainer_bookings WHERE plan_id = ? ORDER BY id DESC LIMIT 1",
            (plan_id,)
        )
        event_row = cursor.fetchone()
        cursor.execute(
            "SELECT description FROM google_calendar_events WHERE id = ?", (event_row[0],)
        )
        desc = cursor.fetchone()[0]
    assert "Knäböj" in desc
    assert "90 kg" in desc


def test_trainer_trends_endpoint(auth_headers):
    """The chart endpoint returns a plottable series plus baselines and adherence (Issue #36)."""
    client = TestClient(app)
    response = client.get("/api/trainer/trends?days=28", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("window_days") == 28
    assert isinstance(data.get("series"), list)
    # The seeded Garmin/Withings rows give at least a few plottable days.
    assert len(data["series"]) >= 2
    for point in data["series"]:
        assert "date" in point
        # A day is only included when it carries at least one reading.
        assert point.get("rhr") is not None or point.get("hrv") is not None
    assert "rhr_change_pct" in data.get("trends", {})
    assert "resting_hr" in data.get("baselines", {})
    assert "planned" in data.get("adherence", {})


def test_trainer_trends_window_is_clamped(auth_headers):
    """An absurd window is clamped rather than scanning the whole table."""
    client = TestClient(app)
    response = client.get("/api/trainer/trends?days=9999", headers=auth_headers)
    assert response.status_code == 200
    assert response.json().get("window_days") == trainer_module.MAX_TREND_DAYS


def test_zero_readings_are_not_plotted_or_averaged(auth_headers):
    """A device writing 0 on a day it recorded nothing must not count as a real reading.

    Garmin stores 0 rather than NULL when the watch was not worn. Treating those as data
    drags the plotted line and the trend percentages towards zero, which reads as a huge
    (fictional) improvement in resting heart rate.
    """
    import datetime
    client = TestClient(app)
    zero_day = (datetime.date.today() - datetime.timedelta(days=2)).strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT resting_hr, hrv FROM garmin_health WHERE date = ?", (zero_day,))
        previous = cursor.fetchone()
        cursor.execute(
            "INSERT INTO garmin_health (date, resting_hr, hrv) VALUES (?, 0, 0) "
            "ON CONFLICT(date) DO UPDATE SET resting_hr = 0, hrv = 0",
            (zero_day,)
        )
        conn.commit()

    try:
        series = client.get("/api/trainer/trends?days=14", headers=auth_headers).json()["series"]
        zero_points = [p for p in series if p["date"] == zero_day]
        # The day may drop out entirely, but it must never report 0 as a measurement.
        for point in zero_points:
            assert point["rhr"] != 0
            assert point["hrv"] != 0

        trends = trainer_module.calculate_trends()
        # Averages stay in a physiologically possible range instead of being pulled to 0.
        for key in ("rhr_recent_avg", "rhr_baseline_avg", "hrv_recent_avg", "hrv_baseline_avg"):
            if trends[key] is not None:
                assert trends[key] > 0
    finally:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if previous is None:
                cursor.execute("DELETE FROM garmin_health WHERE date = ?", (zero_day,))
            else:
                cursor.execute(
                    "UPDATE garmin_health SET resting_hr = ?, hrv = ? WHERE date = ?",
                    (previous[0], previous[1], zero_day)
                )
            conn.commit()


def test_trainer_injury_log_roundtrip(auth_headers):
    """An injury can be logged, listed, resolved and deleted (Issue #38)."""
    client = TestClient(app)
    add_res = client.post(
        "/api/trainer/injuries",
        json={"area": "Höger knä", "severity": 6, "note": "Ömt efter löpning"},
        headers=auth_headers
    )
    assert add_res.status_code == 200
    injury_id = add_res.json().get("id")
    assert injury_id

    active = client.get("/api/trainer/injuries?status=active", headers=auth_headers).json()["injuries"]
    assert any(i["id"] == injury_id and i["area"] == "Höger knä" for i in active)

    # Resolving stamps a date and takes the entry out of the active set...
    put_res = client.put(
        "/api/trainer/injuries", json={"id": injury_id, "status": "resolved"}, headers=auth_headers
    )
    assert put_res.status_code == 200
    active_after = client.get("/api/trainer/injuries?status=active", headers=auth_headers).json()["injuries"]
    assert all(i["id"] != injury_id for i in active_after)

    # ...but keeps it in the log as history.
    resolved = client.get("/api/trainer/injuries?status=resolved", headers=auth_headers).json()["injuries"]
    entry = next(i for i in resolved if i["id"] == injury_id)
    assert entry["resolved_date"]

    del_res = client.delete(f"/api/trainer/injuries?injury_id={injury_id}", headers=auth_headers)
    assert del_res.status_code == 200


def test_trainer_injury_requires_area(auth_headers):
    client = TestClient(app)
    res = client.post("/api/trainer/injuries", json={"severity": 5}, headers=auth_headers)
    assert res.status_code == 400


def test_trainer_injury_update_rejects_unknown_id(auth_headers):
    client = TestClient(app)
    res = client.put("/api/trainer/injuries", json={"id": 999999, "status": "resolved"}, headers=auth_headers)
    assert res.status_code == 404


def test_active_injuries_reach_the_coach_prompt(auth_headers):
    """Only active entries are pasted into the coach prompts."""
    client = TestClient(app)
    add = client.post(
        "/api/trainer/injuries",
        json={"area": "Vänster hälsena", "severity": 8, "note": "Stel på morgonen"},
        headers=auth_headers
    ).json()

    block = trainer_module.format_active_injuries()
    assert "Vänster hälsena" in block
    assert "8/10" in block

    client.put("/api/trainer/injuries", json={"id": add["id"], "status": "resolved"}, headers=auth_headers)
    assert "Vänster hälsena" not in trainer_module.format_active_injuries()

    client.delete(f"/api/trainer/injuries?injury_id={add['id']}", headers=auth_headers)


def _insert_export_plan(goal="Exporttest"):
    """Stores a structured two-session plan and returns its id."""
    plan_json = json.dumps({
        "summary": "Stabil grund.",
        "weekly_focus": "Bygg uthållighet.",
        "resting_hr_trend": "Stabil.",
        "hrv_trend": "God.",
        "workouts": [
            {"day": "Måndag", "activity_type": "Löpning", "title": "Lugnt pass",
             "description": "40 min i samtalstempo.", "duration_minutes": 40},
            {"day": "Onsdag", "activity_type": "Styrketräning", "title": "Underkropp",
             "description": "Tunga baslyft.", "duration_minutes": 50,
             "exercises": [{"name": "Knäböj", "sets": 4, "reps": 6, "target_weight": 90}]},
            {"day": "Söndag", "activity_type": "Vila", "title": "Vila",
             "description": "Full återhämtning.", "duration_minutes": 0},
        ]
    })
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-07-20", goal, plan_json, "")
        )
        conn.commit()
        return cursor.lastrowid


def test_trainer_plan_export_ics(auth_headers):
    """A plan exports as an importable calendar file (Issue #39)."""
    client = TestClient(app)
    plan_id = _insert_export_plan()

    res = client.get(
        f"/api/trainer/plans/export?plan_id={plan_id}&format=ics&start_date=2026-07-27",
        headers=auth_headers
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar")
    assert ".ics" in res.headers.get("content-disposition", "")

    body = res.content.decode("utf-8")
    assert body.startswith("BEGIN:VCALENDAR")
    assert body.rstrip().endswith("END:VCALENDAR")
    # Two sessions: the Sunday rest day carries no duration and is skipped.
    assert body.count("BEGIN:VEVENT") == 2
    assert "DTSTART:20260727T080000" in body   # Monday of the given start week
    assert "DTEND:20260727T084000" in body     # 40 minutes later
    assert "DTSTART:20260729T080000" in body   # Wednesday


def test_trainer_plan_export_pdf(auth_headers):
    """The PDF export is a real PDF file, not an error page."""
    client = TestClient(app)
    plan_id = _insert_export_plan("PDF-test")

    res = client.get(f"/api/trainer/plans/export?plan_id={plan_id}&format=pdf", headers=auth_headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")
    assert res.content.rstrip().endswith(b"%%EOF")
    # A valid xref table is what makes the file openable.
    assert b"xref" in res.content and b"/Type /Catalog" in res.content


def test_trainer_plan_export_handles_non_ascii_goals(auth_headers):
    """A Swedish (or any non-ASCII) goal must not break the download header.

    Response headers are latin-1 encoded, and `str.isalnum()` is Unicode-aware, so an
    unfiltered slug from a goal like "Träna inför Göteborgsvarvet" - entirely typical for
    this app - used to blow up mid-response instead of returning a file.
    """
    client = TestClient(app)
    plan_json = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Pass",
         "description": "Lugnt.", "duration_minutes": 30}
    ]})

    for goal, expected in [
        ("Träna inför Göteborgsvarvet", "trana-infor-goteborgsvarvet"),
        ("Бег 10 км", "freja-10-"),  # Cyrillic dropped; the ASCII digits survive
        ("走る", None),               # CJK: nothing ASCII left, must fall back
        ("Bli stark 💪", "bli-stark"),
    ]:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
                ("2026-07-20", goal, plan_json, "")
            )
            conn.commit()
            plan_id = cursor.lastrowid

        for fmt in ("ics", "pdf"):
            res = client.get(
                f"/api/trainer/plans/export?plan_id={plan_id}&format={fmt}", headers=auth_headers
            )
            assert res.status_code == 200, f"{goal} / {fmt}"
            disposition = res.headers.get("content-disposition", "")
            # The header must be pure ASCII or the response cannot be encoded at all.
            disposition.encode("ascii")
            if expected:
                assert expected in disposition, f"{goal}: got {disposition}"
            else:
                assert "traningsplan" in disposition, f"{goal}: got {disposition}"


def test_trainer_plan_export_rejects_bad_input(auth_headers):
    client = TestClient(app)
    plan_id = _insert_export_plan("Valideringstest")

    assert client.get(
        f"/api/trainer/plans/export?plan_id={plan_id}&format=xlsx", headers=auth_headers
    ).status_code == 400
    assert client.get(
        f"/api/trainer/plans/export?plan_id={plan_id}&format=ics&start_date=inte-ett-datum",
        headers=auth_headers
    ).status_code == 400
    assert client.get(
        "/api/trainer/plans/export?plan_id=999999&format=ics", headers=auth_headers
    ).status_code == 404


def test_trainer_plan_export_freetext_plan(auth_headers):
    """A pre-schema plan (plain text, no JSON) still exports to PDF but not to a calendar."""
    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-01-01", "Gammal plan", "Bara löpande text utan struktur.", "")
        )
        conn.commit()
        plan_id = cursor.lastrowid

    pdf_res = client.get(f"/api/trainer/plans/export?plan_id={plan_id}&format=pdf", headers=auth_headers)
    assert pdf_res.status_code == 200
    assert pdf_res.content.startswith(b"%PDF-")

    ics_res = client.get(f"/api/trainer/plans/export?plan_id={plan_id}&format=ics", headers=auth_headers)
    assert ics_res.status_code == 400


def test_export_and_booking_schedule_a_plan_identically():
    """The export and the calendar booking must place a plan on the same dates."""
    import datetime
    from backend.services import plan_export

    # Both paths read the same weekday map, which is what keeps them in step.
    assert plan_export.SWEDISH_DAY_OFFSETS["måndag"] == 0
    assert plan_export.SWEDISH_DAY_OFFSETS["söndag"] == 6

    plan_data = {"workouts": [
        {"day": "Torsdag", "activity_type": "Löpning", "title": "Pass", "description": "",
         "duration_minutes": 30},
        {"day": "Måndag", "activity_type": "Löpning", "title": "Vecka 2", "description": "",
         "duration_minutes": 30, "week": 1},
    ]}
    occurrences = plan_export.plan_occurrences(plan_data, datetime.date(2026, 7, 27))
    # Sorted chronologically: Thursday of week 1, then Monday of week 2.
    assert [o["date"] for o in occurrences] == [
        datetime.date(2026, 7, 30), datetime.date(2026, 8, 3)
    ]


def test_build_pdf_labels_each_week_with_its_own_date():
    """A workout on the same weekday in different weeks must show that week's own date, not
    always the first week's - dates_by_day used to be keyed by weekday name alone, so
    setdefault() kept only the earliest occurrence's date for every later week's session."""
    import datetime
    from backend.services import plan_export

    plan_data = {"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Vecka 1", "duration_minutes": 30, "week": 0},
        {"day": "Måndag", "activity_type": "Löpning", "title": "Vecka 2", "duration_minutes": 30, "week": 1},
    ]}
    pdf_bytes = plan_export.build_pdf({"id": 1, "goal": "Test"}, plan_data, datetime.date(2026, 7, 27))
    pdf_text = pdf_bytes.decode("latin-1")
    assert "2026-07-27" in pdf_text  # week 0's Monday
    assert "2026-08-03" in pdf_text  # week 1's Monday - previously mislabeled as 2026-07-27 too


def test_build_pdf_and_ics_treat_explicit_null_fields_as_missing():
    """An explicit JSON null for activity_type/title (possible via the unrestricted plan-update
    endpoint) must fall back to the Swedish default, not render the literal string "None" -
    dict.get(key, default) only substitutes when the key is absent, not when it's null."""
    import datetime
    from backend.services import plan_export

    plan_data = {"workouts": [
        {"day": "Måndag", "activity_type": None, "title": None, "description": None, "duration_minutes": 30}
    ]}
    ics = plan_export.build_ics({"id": 1, "goal": "Test"}, plan_data, datetime.date(2026, 7, 27))
    assert "None" not in ics

    pdf_text = plan_export.build_pdf({"id": 1, "goal": "Test"}, plan_data, datetime.date(2026, 7, 27)).decode("latin-1")
    assert "None" not in pdf_text


def test_ics_folds_long_lines():
    """Content lines are folded to 75 octets so strict parsers accept the file, and
    unfolding (undoing the RFC 5545 continuation) reproduces the original text exactly -
    the fold length check alone wouldn't catch a regression that corrupts UTF-8 content
    while still respecting the 75-octet limit."""
    import datetime
    from backend.services import plan_export

    long_desc = "Ö" * 400
    plan_data = {"workouts": [{
        "day": "Måndag", "activity_type": "Löpning", "title": "Långt pass",
        "description": long_desc, "duration_minutes": 45
    }]}
    ics = plan_export.build_ics({"id": 1, "goal": "Test"}, plan_data, datetime.date(2026, 7, 27))
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, f"unfolded line: {line[:40]}..."

    # RFC 5545 unfolding: a CRLF followed by a single leading space is removed.
    unfolded = ics.replace("\r\n ", "")
    description_line = next(l for l in unfolded.split("\r\n") if l.startswith("DESCRIPTION:"))
    assert long_desc in description_line


def test_trainer_booking_is_idempotent(auth_headers):
    client = TestClient(app)
    plan_json = json.dumps({"workouts": [
        {"day": "Tisdag", "activity_type": "Löpning", "title": "Intervaller",
         "description": "Spring snabbt", "duration_minutes": 30},
        {"day": "Torsdag", "activity_type": "Styrketräning", "title": "Ben",
         "description": "Knäböj", "duration_minutes": 40}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-07-06", "Idempotens-test", plan_json, "Inga")
        )
        conn.commit()
        plan_id = cursor.lastrowid

    # Other booking tests share this session's DB and also book onto 2026-07-06, and booking
    # now replaces *every* PT session in the window (not just the same plan_id). Clear the
    # window so this test's replaced-count assertions reflect only its own plan.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE workout_date >= '2026-07-06' AND workout_date <= '2026-07-09'")
        conn.commit()

    payload = {"plan_id": plan_id, "start_date": "2026-07-06"}

    first = client.post("/api/trainer/plans/book", json=payload, headers=auth_headers)
    assert first.status_code == 200
    assert first.json().get("booked_count") == 2
    assert first.json().get("replaced_count") == 0

    second = client.post("/api/trainer/plans/book", json=payload, headers=auth_headers)
    assert second.status_code == 200
    assert second.json().get("booked_count") == 2
    # The second booking must replace the first, not stack duplicates.
    assert second.json().get("replaced_count") == 2

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        booking_count = cursor.fetchone()[0]
    assert booking_count == 2


def test_trainer_booking_replaces_longer_plan_with_shorter(auth_headers):
    """Booking a shorter plan must clear a longer existing plan's later weeks too - the
    replace window used to be bounded by the *new* plan's own span, so a shorter plan
    replacing a longer one left the old plan's tail dangling (issue #61)."""
    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE workout_date >= '2026-08-03' AND workout_date <= '2026-08-24'")
        conn.commit()

    long_plan = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Vecka 1", "description": "",
         "duration_minutes": 30, "week": 0},
        {"day": "Måndag", "activity_type": "Löpning", "title": "Vecka 3", "description": "",
         "duration_minutes": 30, "week": 2},
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-08-03", "Lång plan", long_plan, "Inga")
        )
        conn.commit()
        long_plan_id = cursor.lastrowid

    first = client.post("/api/trainer/plans/book", json={"plan_id": long_plan_id, "start_date": "2026-08-03"}, headers=auth_headers)
    assert first.status_code == 200
    assert first.json().get("booked_count") == 2

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE workout_date = '2026-08-17'")
        assert cursor.fetchone()[0] == 1  # week-3 Monday = 2026-08-03 + 14 days

    short_plan = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Ersätter", "description": "",
         "duration_minutes": 20, "week": 0},
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-08-03", "Kort plan", short_plan, "Inga")
        )
        conn.commit()
        short_plan_id = cursor.lastrowid

    second = client.post("/api/trainer/plans/book", json={"plan_id": short_plan_id, "start_date": "2026-08-03"}, headers=auth_headers)
    assert second.status_code == 200
    assert second.json().get("booked_count") == 1
    assert second.json().get("replaced_count") == 2  # both of the long plan's sessions

    # The long plan's week-3 session must be gone too, not just the overlapping week 1.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE workout_date = '2026-08-17'")
        assert cursor.fetchone()[0] == 0


def test_trainer_booking_with_no_bookable_workouts_leaves_prior_booking(auth_headers):
    """A plan whose every workout has an unparseable day (or is all rest days) must not wipe
    an existing booking with nothing to replace it (issue #63)."""
    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE workout_date = '2026-09-07'")
        conn.commit()

    good_plan = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Bra pass", "description": "",
         "duration_minutes": 30}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-09-07", "Bra plan", good_plan, "Inga")
        )
        conn.commit()
        good_plan_id = cursor.lastrowid

    first = client.post("/api/trainer/plans/book", json={"plan_id": good_plan_id, "start_date": "2026-09-07"}, headers=auth_headers)
    assert first.status_code == 200
    assert first.json().get("booked_count") == 1

    bad_plan = json.dumps({"workouts": [
        {"day": "Blurdag", "activity_type": "Löpning", "title": "Trasigt pass", "description": "",
         "duration_minutes": 30}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-09-07", "Trasig plan", bad_plan, "Inga")
        )
        conn.commit()
        bad_plan_id = cursor.lastrowid

    second = client.post("/api/trainer/plans/book", json={"plan_id": bad_plan_id, "start_date": "2026-09-07"}, headers=auth_headers)
    assert second.status_code == 200
    assert second.json().get("booked_count") == 0
    assert second.json().get("replaced_count") == 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT plan_id FROM trainer_bookings WHERE workout_date = '2026-09-07'")
        row = cursor.fetchone()
    assert row is not None and row[0] == good_plan_id


def test_trainer_plan_delete_clears_its_bookings(auth_headers):
    """Deleting a plan must clear its booked sessions too, not leave them as invisible
    orphans that still occupy the user's calendar (issue #60)."""
    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE workout_date = '2026-09-14'")
        conn.commit()

    plan_json = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Pass", "description": "",
         "duration_minutes": 30}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-09-14", "Raderas", plan_json, "Inga")
        )
        conn.commit()
        plan_id = cursor.lastrowid

    booked = client.post("/api/trainer/plans/book", json={"plan_id": plan_id, "start_date": "2026-09-14"}, headers=auth_headers)
    assert booked.status_code == 200
    assert booked.json().get("booked_count") == 1

    delete_res = client.delete(f"/api/trainer/plans?plan_id={plan_id}", headers=auth_headers)
    assert delete_res.status_code == 200

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        assert cursor.fetchone()[0] == 0


def test_trainer_booking_skips_workout_on_calendar_sync_failure(auth_headers, monkeypatch):
    """A Google Calendar failure for one session must not crash the whole booking, and must
    not leave a phantom 'booked' row with nothing on the real calendar (issues #58/#59/#62).
    `core_book_plan_internal` imports core_save_calendar_event with a function-local
    `from ... import ...`, so the source module has to be patched for this to take effect."""
    import backend.routes.google_calendar as gcal_module

    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE workout_date = '2026-09-21'")
        conn.commit()

    plan_json = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Pass", "description": "",
         "duration_minutes": 30}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-09-21", "Synkfel", plan_json, "Inga")
        )
        conn.commit()
        plan_id = cursor.lastrowid

    async def failing_save(*args, **kwargs):
        raise RuntimeError("simulated Google Calendar outage")
    monkeypatch.setattr(gcal_module, "core_save_calendar_event", failing_save)

    res = client.post("/api/trainer/plans/book", json={"plan_id": plan_id, "start_date": "2026-09-21"}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body.get("booked_count") == 0
    assert body.get("sync_failed_count") == 1

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        assert cursor.fetchone()[0] == 0


def test_trainer_plan_delete_keeps_booking_when_calendar_delete_fails(auth_headers, monkeypatch):
    """If the Google-side delete fails, the booking row must stay in place - it still
    represents a live calendar event, so dropping it would create an untracked orphan
    (issue #58)."""
    import backend.routes.google_calendar as gcal_module

    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-09-28", "Raderas ej", "{}", "Inga")
        )
        conn.commit()
        plan_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (plan_id, 424242, "2026-09-28", 0)
        )
        conn.commit()

    async def failing_delete(*args, **kwargs):
        raise RuntimeError("simulated Google Calendar outage")
    monkeypatch.setattr(gcal_module, "core_delete_calendar_event", failing_delete)

    delete_res = client.delete(f"/api/trainer/plans?plan_id={plan_id}", headers=auth_headers)
    assert delete_res.status_code == 200

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        assert cursor.fetchone()[0] == 1  # still tracked - the event is still live


def test_trainer_plan_delete_clears_booking_when_calendar_event_already_gone(auth_headers, monkeypatch):
    """core_delete_calendar_event raises ValueError specifically for "event not found" (the
    local google_calendar_events row is already gone) - that must be treated as "nothing
    left to protect" and clear the booking row, not as a genuine failure that keeps it. The
    two used to be handled identically, which left the booking stuck forever: it would fail
    with the same "not found" on every future rebook while no longer representing anything
    real."""
    import backend.routes.google_calendar as gcal_module

    client = TestClient(app)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-10-05", "Event redan borta", "{}", "Inga")
        )
        conn.commit()
        plan_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (plan_id, 999999, "2026-10-05", 0)
        )
        conn.commit()

    async def not_found_delete(*args, **kwargs):
        raise ValueError("The event was not found.")
    monkeypatch.setattr(gcal_module, "core_delete_calendar_event", not_found_delete)

    delete_res = client.delete(f"/api/trainer/plans?plan_id={plan_id}", headers=auth_headers)
    assert delete_res.status_code == 200

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM trainer_bookings WHERE plan_id = ?", (plan_id,))
        assert cursor.fetchone()[0] == 0  # cleared - nothing left on the calendar to protect


# --- Training-load history, booking anchor and chat context -------------------
# The three regressions below all produced silently wrong coaching rather than an error:
# the workouts endpoint 500'd on every call, plans were booked onto the wrong weekdays, and
# a plan could jump from a 20-minute jog to an hour because only 7 days of data were seen.

def test_trainer_workouts_endpoint_returns_a_list(auth_headers):
    """Regression: `today_local()` returns a date, so `.date()` on it raised AttributeError
    and this endpoint answered 500 on every single request - the PT panel's weekly workout
    list was dead, and the get_trainer_workouts tool had nothing to read."""
    client = TestClient(app)
    response = client.get("/api/trainer/workouts?days=14", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_trainer_workouts_end_time_follows_duration(auth_headers):
    """A fixed 09:00 end time made every session render as 60 minutes in the HUD."""
    mock_plan = json.dumps({"workouts": [
        {"day": "Måndag", "activity_type": "Löpning", "title": "Kort pass",
         "description": "20 min lugnt", "duration_minutes": 20}
    ]})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_plans (date, goal, advice_text, limitations) VALUES (?, ?, ?, ?)",
            ("2026-07-20", "Sluttid-test", mock_plan, ""),
        )
        plan_id = cursor.lastrowid
        monday = trainer_module.today_local() - __import__("datetime").timedelta(
            days=trainer_module.today_local().weekday()
        )
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (plan_id, None, monday.isoformat(), 0),
        )
        conn.commit()

    client = TestClient(app)
    workouts = client.get("/api/trainer/workouts?days=14", headers=auth_headers).json()
    match = [w for w in workouts if w.get("plan_id") == plan_id]
    assert match, "the freshly booked workout was not returned"
    assert match[0]["start_time"].endswith("T08:00:00")
    assert match[0]["end_time"].endswith("T08:20:00")


def test_current_week_monday_anchors_booking():
    """Plan weekdays are offsets from the start date, so it has to BE a Monday."""
    import datetime as dt
    for day in range(7):
        d = dt.date(2026, 7, 20) + dt.timedelta(days=day)   # 2026-07-20 is a Monday
        assert trainer_module._current_week_monday(d).weekday() == 0
        assert trainer_module._current_week_monday(d) <= d


def test_training_load_summary_caps_progression():
    """The plan prompt must carry hard minute ceilings derived from real training.

    Without them a plan happily jumped from the user's habitual 20-minute jog straight to a
    one-hour run, which is the injury risk this window exists to prevent.
    """
    load = trainer_module.build_training_load_summary(30)
    assert load["window_days"] == 30
    assert "session_count" in load
    if load["session_count"]:
        assert load["max_session_minutes"] >= load["longest_session_minutes"]
        # A 20% step ceiling must never allow tripling the longest recent session.
        assert load["max_session_minutes"] < load["longest_session_minutes"] * 2
        rules = trainer_module._format_progression_rules(load)
        assert str(load["max_session_minutes"]) in rules
        assert "HARD CEILING" in rules


def test_generate_prompt_includes_thirty_day_history(auth_headers, monkeypatch):
    """The generated plan must be built on a month of completed training, not just 7 days."""
    captured = {}

    async def fake_weather(location="Stockholm"):
        return "Väderprognos: mulet."
    monkeypatch.setattr(trainer_module.generation, "fetch_7day_weather_forecast", fake_weather)

    plan_obj = {"summary": "s", "resting_hr_trend": "r", "hrv_trend": "h", "weekly_focus": "f",
                "workouts": [{"day": "Tisdag", "activity_type": "Löpning", "title": "Pass",
                              "description": "d", "duration_minutes": 25}]}

    async def _fake_capture(prompt, schema=None, **kwargs):
        captured["prompt"] = prompt
        return plan_obj
    monkeypatch.setattr(trainer_module.generation.llm_client, "generate_json", _fake_capture)

    client = TestClient(app)
    response = client.post(
        "/api/trainer/generate",
        json={"goal": "Bygg löpvana", "limitations": ""},
        headers=auth_headers,
    )
    assert response.status_code == 200

    prompt = captured["prompt"]
    assert "TRAINING LOAD" in prompt
    assert "LAST 30 DAYS" in prompt
    assert "PROGRESSION LIMITS" in prompt


def test_chat_context_block_exposes_the_program(auth_headers):
    """Freja must know the booked program in ordinary chat, without a tool call."""
    client = TestClient(app)
    response = client.get("/api/trainer/context", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    if data["has_context"]:
        assert "Dagens datum" in data["context"]


def test_onboarding_profile_coercion_rejects_junk():
    """Onboarding writes straight into the profile, so the model's output is filtered."""
    coerced = trainer_module._coerce_onboarding_profile({
        "goals": "  Springa 10 km  ",
        "fitness_level": "superhuman",        # not one of the three allowed levels
        "event_date": "nästa vår",            # unparseable - would break the date input
        "baseline_resting_hr": 0,             # the schema's "unknown", not a real baseline
        "baseline_hrv": 46.27,
        "location": "Stockholm",
    })
    assert coerced["goals"] == "Springa 10 km"
    assert "fitness_level" not in coerced
    assert "event_date" not in coerced
    assert "baseline_resting_hr" not in coerced
    assert coerced["baseline_hrv"] == 46.3
    assert coerced["location"] == "Stockholm"


def test_recent_strength_logs_prefers_garmin_over_manual_duplicate():
    """The same session logged both manually and imported from Garmin must appear once in
    get_recent_strength_logs, preferring the Garmin row as the more accurate record (#183)."""
    import datetime
    from backend.routes.trainer.shared import get_recent_strength_logs

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM trainer_strength_logs WHERE date = ? AND exercise_name = ?",
            ('2026-04-01', 'Marklyft')
        )
        now_str = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO trainer_strength_logs (date, exercise_name, sets, reps, weight, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'manual')",
            ('2026-04-01', 'Marklyft', 3, 5, 100.0, now_str)
        )
        cursor.execute(
            "INSERT INTO trainer_strength_logs (date, exercise_name, sets, reps, weight, created_at, source, activity_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 'garmin', ?)",
            ('2026-04-01', 'Marklyft', 4, 5, 105.0, now_str, 't183-dedup')
        )
        conn.commit()

    logs = get_recent_strength_logs(limit=100)
    matching = [l for l in logs if l['date'] == '2026-04-01' and l['exercise_name'] == 'Marklyft']

    assert len(matching) == 1
    assert matching[0]['source'] == 'garmin'
    assert matching[0]['sets'] == 4


def test_training_load_summary_includes_weekly_zone_split():
    """build_training_load_summary()'s weekly_zone_split must aggregate HR-zone seconds
    across sessions in the same week (#184)."""
    import datetime
    from backend.database import get_db_connection
    from backend.routes.trainer.shared import build_training_load_summary

    today = datetime.date.today()
    recent_date = today.strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activities WHERE activity_id = ?", ('t184-load-1',))
        cursor.execute("DELETE FROM garmin_activity_zones WHERE activity_id = ?", ('t184-load-1',))
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            ('t184-load-1', recent_date, f'{recent_date} 07:00:00', 'Löpning', 30.0)
        )
        cursor.execute(
            "INSERT INTO garmin_activity_zones (activity_id, secs_zone_1, secs_zone_2, secs_zone_3, secs_zone_4, secs_zone_5) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ('t184-load-1', 60, 60, 300, 300, 300)  # heavily skewed hard
        )
        conn.commit()

    load = build_training_load_summary(days=30)
    this_week = next((w for w in load["weekly_zone_split"] if w["weeks_ago"] == 0), None)

    assert this_week is not None
    assert this_week["easy_pct"] < 80


def _clear_adherence_fixture(date_str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM trainer_bookings WHERE workout_date = ?", (date_str,))
        cursor.execute("DELETE FROM strava_activities WHERE SUBSTR(date, 1, 10) = ?", (date_str,))
        cursor.execute("DELETE FROM garmin_activities WHERE date = ?", (date_str,))
        conn.commit()


def test_adherence_strava_empty_garmin_has_it_reports_real_adherence():
    """A booked session recorded only on Garmin (Strava never saw it) must still count as
    completed - Garmin is unioned in exactly like build_training_load_summary() (#187)."""
    import datetime
    from backend.services.sync_status import set_sync_state
    from backend.routes.trainer.shared import compute_adherence

    set_sync_state("strava", "success")
    set_sync_state("garmin", "success")

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_adherence_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (None, None, target_date, 0)
        )
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            ('t187-garmin-only', target_date, f'{target_date} 07:00:00', 'Löpning', 30.0)
        )
        conn.commit()

    result = compute_adherence(days=1)

    assert result["reliable"] is True
    assert result["completed"] == 1
    assert result["adherence_pct"] == 100.0


def test_adherence_both_sources_unreliable_reports_none_not_zero():
    """Both sources in an error state must report adherence_pct: None, reliable: False -
    not a misleading 0.0 (#187)."""
    import datetime
    from backend.services.sync_status import set_sync_state
    from backend.routes.trainer.shared import compute_adherence

    set_sync_state("strava", "error", "token expired")
    set_sync_state("garmin", "error", "token expired")

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_adherence_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (None, None, target_date, 0)
        )
        conn.commit()

    result = compute_adherence(days=1)

    assert result["adherence_pct"] is None
    assert result["reliable"] is False
    assert "strava" in result["reason"] and "garmin" in result["reason"]

    # Reset to healthy for the tests that follow in this session.
    set_sync_state("strava", "success")
    set_sync_state("garmin", "success")


def test_adherence_query_failure_does_not_produce_zero(monkeypatch):
    """A query that raises (not just an empty result) must not produce a 0.0 - it must be
    treated the same as an unreliable source (#187)."""
    import contextlib
    import datetime
    from backend.database import get_db_connection as real_get_db_connection
    import backend.routes.trainer.shared as shared_module
    from backend.services.sync_status import set_sync_state

    set_sync_state("strava", "success")
    set_sync_state("garmin", "success")

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_adherence_fixture(target_date)
    with real_get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (None, None, target_date, 0)
        )
        conn.commit()

    class RaisingCursor:
        def __init__(self, real_cursor):
            self._real = real_cursor
        def execute(self, sql, params=()):
            if 'strava_activities' in sql or 'garmin_activities' in sql:
                raise RuntimeError("simulated DB failure")
            return self._real.execute(sql, params)
        def fetchall(self):
            return self._real.fetchall()

    class RaisingConnection:
        def __init__(self, real_conn):
            self._real = real_conn
        def cursor(self):
            return RaisingCursor(self._real.cursor())

    @contextlib.contextmanager
    def fake_get_db_connection():
        with real_get_db_connection() as real_conn:
            yield RaisingConnection(real_conn)

    monkeypatch.setattr(shared_module, "get_db_connection", fake_get_db_connection)

    result = shared_module.compute_adherence(days=1)

    assert result["adherence_pct"] is None
    assert result["reliable"] is False


def test_adherence_genuine_zero_with_healthy_sync_stays_reliable():
    """A booked session that really was skipped, with both sources healthy, must still
    report a real 0.0 - not everything unreliable turns into None (#187)."""
    import datetime
    from backend.services.sync_status import set_sync_state
    from backend.routes.trainer.shared import compute_adherence

    set_sync_state("strava", "success")
    set_sync_state("garmin", "success")

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_adherence_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trainer_bookings (plan_id, event_id, workout_date, week) VALUES (?, ?, ?, ?)",
            (None, None, target_date, 0)
        )
        conn.commit()

    result = compute_adherence(days=1)

    assert result["reliable"] is True
    assert result["adherence_pct"] == 0.0


def _clear_unified_sessions_fixture(date_str, activity_ids=(), strava_ids=()):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM garmin_activities WHERE date = ?", (date_str,))
        cursor.execute("DELETE FROM strava_activities WHERE SUBSTR(date, 1, 10) = ?", (date_str,))
        conn.commit()


def test_unified_sessions_same_session_counts_once_keeps_garmin_fields():
    """A session recorded on both Garmin and Strava (matched by start time + duration)
    must count once, keeping Garmin's richer fields rather than Strava's (#188)."""
    import datetime
    from backend.routes.trainer.shared import unified_sessions

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_unified_sessions_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes, distance_m, avg_hr, max_hr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ('t188-dup', target_date, f'{target_date}T07:00:00', 'Löpning', 30.0, 5000.0, 145, 165)
        )
        cursor.execute(
            "INSERT INTO strava_activities (name, type, date, distance, moving_time) VALUES (?, ?, ?, ?, ?)",
            ('Morgonlöpning', 'Run', f'{target_date}T07:02:00', 5010.0, 1810)  # ~30.2 min, 2 min later
        )
        conn.commit()

    sessions = unified_sessions(target_date, target_date)

    assert len(sessions) == 1
    assert sessions[0]['source'] == 'garmin'
    assert sessions[0]['garmin_activity_id'] == 't188-dup'
    assert sessions[0]['avg_hr'] == 145  # Garmin's field, not lost to the Strava row


def test_unified_sessions_strava_only_activity_is_included():
    """An activity with no Garmin counterpart (e.g. Zwift, phone app, manual entry) is
    Strava's real contribution and must survive (#188)."""
    import datetime
    from backend.routes.trainer.shared import unified_sessions

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_unified_sessions_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO strava_activities (name, type, date, distance, moving_time) VALUES (?, ?, ?, ?, ?)",
            ('Zwift-pass', 'Ride', f'{target_date}T18:00:00', 20000.0, 3600)
        )
        conn.commit()

    sessions = unified_sessions(target_date, target_date)

    assert len(sessions) == 1
    assert sessions[0]['source'] == 'strava'


def test_unified_sessions_two_genuine_sessions_one_day_both_survive():
    """Two distinct sessions on the same day (an easy run + strength, say) must not be
    collapsed into one just because they share a date (#188)."""
    import datetime
    from backend.routes.trainer.shared import unified_sessions

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_unified_sessions_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            ('t188-morning', target_date, f'{target_date}T06:00:00', 'Löpning', 30.0)
        )
        # A genuinely different Strava session much later the same day - far outside the
        # ±10 minute / ±10% duplicate-matching window.
        cursor.execute(
            "INSERT INTO strava_activities (name, type, date, distance, moving_time) VALUES (?, ?, ?, ?, ?)",
            ('Kvällspass', 'WeightTraining', f'{target_date}T18:00:00', 0.0, 2700)
        )
        conn.commit()

    sessions = unified_sessions(target_date, target_date)

    assert len(sessions) == 2
    assert {s['source'] for s in sessions} == {'garmin', 'strava'}


def test_unified_sessions_garmin_outage_falls_back_to_strava_and_vice_versa():
    """If Garmin has nothing for the window, Strava carries it alone - and vice versa - so
    an outage in either source doesn't blind the coach (#188)."""
    import datetime
    from backend.routes.trainer.shared import unified_sessions

    target_date = datetime.date.today().strftime('%Y-%m-%d')
    _clear_unified_sessions_fixture(target_date)

    # Garmin outage: only Strava has data for the window.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO strava_activities (name, type, date, distance, moving_time) VALUES (?, ?, ?, ?, ?)",
            ('Utan Garmin', 'Run', f'{target_date}T07:00:00', 8000.0, 2400)
        )
        conn.commit()

    sessions = unified_sessions(target_date, target_date)
    assert len(sessions) == 1
    assert sessions[0]['source'] == 'strava'

    # Now the reverse: Strava outage, only Garmin has data.
    _clear_unified_sessions_fixture(target_date)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO garmin_activities (activity_id, date, start_time_local, type, duration_minutes) "
            "VALUES (?, ?, ?, ?, ?)",
            ('t188-strava-out', target_date, f'{target_date}T07:00:00', 'Löpning', 40.0)
        )
        conn.commit()

    sessions = unified_sessions(target_date, target_date)
    assert len(sessions) == 1
    assert sessions[0]['source'] == 'garmin'
