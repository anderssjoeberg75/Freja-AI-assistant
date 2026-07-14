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

def test_trainer_checkin_requires_gemini_key(auth_headers, monkeypatch):
    # Force the "no Gemini key" branch so the endpoint fails fast (no external calls).
    monkeypatch.setattr(trainer_module, "get_api_key", lambda name: "")
    client = TestClient(app)

    response = client.post("/api/trainer/checkin", json={}, headers=auth_headers)
    assert response.status_code == 400
    assert "Gemini" in response.json().get("detail", "")

def test_trainer_checkin_success(auth_headers, monkeypatch):
    # Provide a Gemini key and mock both the weather forecast and the Gemini call.
    monkeypatch.setattr(trainer_module, "get_api_key", lambda name: "MOCK_GEMINI_KEY")

    async def fake_weather(location="Stockholm"):
        return f"Väderprognos för {location}: idag klart, 15°C till 22°C."
    monkeypatch.setattr(trainer_module, "fetch_7day_weather_forecast", fake_weather)

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

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, **kwargs):
            gemini_payload = {
                "candidates": [
                    {"content": {"parts": [{"text": json.dumps(checkin_obj)}]}}
                ]
            }
            return FakeResponse(gemini_payload)

    monkeypatch.setattr(trainer_module.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.post("/api/trainer/checkin", json={"location": "Stockholm"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "success"
    assert data.get("checkin", {}).get("briefing")
    assert data.get("checkin", {}).get("adjust_workout") is False
    assert data.get("calendar_updated") is False
    assert "adherence" in data


def _make_fake_gemini_client(payload_obj):
    """Returns a fake httpx.AsyncClient class that always answers with payload_obj
    wrapped in Gemini's candidates/parts envelope."""
    class FakeResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload_obj)}]}}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, **kwargs):
            return FakeResponse()

    return FakeAsyncClient


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
    monkeypatch.setattr(trainer_module, "get_api_key", lambda name: "MOCK_GEMINI_KEY")

    async def fake_weather(location="Stockholm"):
        return f"Väderprognos för {location}: idag mulet, 12°C."
    monkeypatch.setattr(trainer_module, "fetch_7day_weather_forecast", fake_weather)

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
    monkeypatch.setattr(trainer_module.httpx, "AsyncClient", _make_fake_gemini_client(plan_obj))

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


def test_trainer_optimize_requires_gemini_key(auth_headers, monkeypatch):
    import datetime
    monkeypatch.setattr(trainer_module, "get_api_key", lambda name: "")
    today = datetime.date.today().strftime('%Y-%m-%d')
    _insert_workout_event("💪 Löpning: Tempo", f"{today}T08:00", f"{today}T09:00", "evt-optim-nokey")

    client = TestClient(app)
    response = client.post("/api/trainer/optimize", json={}, headers=auth_headers)
    assert response.status_code == 400
    assert "Gemini" in response.json().get("detail", "")


def test_trainer_optimize_reduces_upcoming_workout(auth_headers, monkeypatch):
    import datetime
    monkeypatch.setattr(trainer_module, "get_api_key", lambda name: "MOCK_GEMINI_KEY")

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
    monkeypatch.setattr(trainer_module.httpx, "AsyncClient", _make_fake_gemini_client(opt_obj))

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
    monkeypatch.setattr(trainer_module, "get_api_key", lambda name: "MOCK_GEMINI_KEY")

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
    monkeypatch.setattr(trainer_module.httpx, "AsyncClient", _make_fake_gemini_client(opt_obj))

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
