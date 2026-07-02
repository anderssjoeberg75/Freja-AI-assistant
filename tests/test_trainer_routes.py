import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, get_db_connection

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
