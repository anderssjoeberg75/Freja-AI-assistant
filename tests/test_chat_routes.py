import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


def test_chat_history_rejects_absurd_limit(auth_headers):
    client = TestClient(app)
    response = client.get("/api/chat/history?limit=999999", headers=auth_headers)
    assert response.status_code == 422


def test_save_chat_message_rejects_oversized_content(auth_headers):
    client = TestClient(app)
    response = client.post(
        "/api/chat/message",
        json={"sender": "user", "content": "x" * 50_001, "channel": "web"},
        headers=auth_headers,
    )
    assert response.status_code == 422
