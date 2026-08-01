import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key
from backend.services import converse_service


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


def _seed_token():
    set_api_key("freja_access_token", "freja1234")
    return {"X-Freja-Token": "freja1234"}


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


def test_converse_requires_auth():
    _seed_token()
    client = TestClient(app)
    response = client.post("/api/chat/converse", json={"text": "Hej"})  # no token header
    assert response.status_code == 401


def test_converse_returns_reply(monkeypatch):
    headers = _seed_token()

    async def fake_reply(text, *, channel="android", image_base64=None):
        assert text == "Hur ser dagens pass ut?"
        assert channel == "android"
        return "Dagens pass ar 40 minuter lugn lopning."

    monkeypatch.setattr(converse_service, "generate_freja_reply", fake_reply)

    client = TestClient(app)
    response = client.post(
        "/api/chat/converse",
        json={"text": "Hur ser dagens pass ut?"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"reply": "Dagens pass ar 40 minuter lugn lopning."}


def test_converse_provider_unavailable_returns_503(monkeypatch):
    headers = _seed_token()

    async def fake_reply(text, *, channel="android", image_base64=None):
        raise converse_service.ProviderUnavailable("no provider")

    monkeypatch.setattr(converse_service, "generate_freja_reply", fake_reply)

    client = TestClient(app)
    response = client.post("/api/chat/converse", json={"text": "Hej"}, headers=headers)
    assert response.status_code == 503
