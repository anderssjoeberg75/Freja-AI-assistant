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
