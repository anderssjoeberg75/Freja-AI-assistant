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

    monkeypatch.setattr(trainer_module, "shared_client", FakeAsyncClient)
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
    monkeypatch.setattr(trainer_module, "shared_client", _make_fake_gemini_client(plan_obj))

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
    monkeypatch.setattr(trainer_module, "shared_client", _make_fake_gemini_client(opt_obj))

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
    monkeypatch.setattr(trainer_module, "shared_client", _make_fake_gemini_client(opt_obj))

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


def test_ics_folds_long_lines():
    """Content lines are folded to 75 octets so strict parsers accept the file."""
    import datetime
    from backend.services import plan_export

    plan_data = {"workouts": [{
        "day": "Måndag", "activity_type": "Löpning", "title": "Långt pass",
        "description": "Ö" * 400, "duration_minutes": 45
    }]}
    ics = plan_export.build_ics({"id": 1, "goal": "Test"}, plan_data, datetime.date(2026, 7, 27))
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, f"unfolded line: {line[:40]}..."


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
