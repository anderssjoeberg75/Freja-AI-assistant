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
