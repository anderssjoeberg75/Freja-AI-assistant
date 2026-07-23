"""Tests for GET /api/system/llm-status, which drives the admin portal's AI indicator."""

import pytest
from fastapi.testclient import TestClient

from server import app
from backend.database import get_api_key
from backend.services import llm_client


@pytest.fixture
def db_token():
    return get_api_key("freja_access_token") or "freja1234"


@pytest.fixture(autouse=True)
def clear_status_cache():
    """The probe result is cached in llm_client and shared with the chat proxy; each test
    starts from empty so one test's probe cannot answer another's request."""
    llm_client._status_cache["payload"] = None
    llm_client._status_cache["expires_at"] = 0.0
    yield


def _stub_probe(monkeypatch, payload, calls):
    async def check_providers(*args, **kwargs):
        calls.append(1)
        return payload

    monkeypatch.setattr(llm_client, "check_providers", check_providers)


PAYLOAD = {
    "preference": "auto",
    "active": "ollama",
    "providers": {
        "ollama": {"ok": True, "detail": "Online.", "model": "qwen2.5:14b", "base_url": "http://x", "models": []},
        "gemini": {"ok": False, "detail": "No Gemini API key is configured.", "model": "gemini-2.5-flash"},
    },
}


def test_llm_status_requires_a_token():
    assert TestClient(app).get("/api/system/llm-status").status_code == 401


def test_llm_status_returns_the_provider_snapshot(monkeypatch, db_token):
    calls = []
    _stub_probe(monkeypatch, PAYLOAD, calls)

    res = TestClient(app).get("/api/system/llm-status", headers={"X-Freja-Token": db_token})
    assert res.status_code == 200
    assert res.json() == PAYLOAD


def test_repeat_calls_are_served_from_the_cache(monkeypatch, db_token):
    calls = []
    _stub_probe(monkeypatch, PAYLOAD, calls)
    client = TestClient(app)

    client.get("/api/system/llm-status", headers={"X-Freja-Token": db_token})
    client.get("/api/system/llm-status", headers={"X-Freja-Token": db_token})
    assert len(calls) == 1, "the portal polls on a timer; each poll must not re-probe both providers"

    # Saving settings can change the answer, so the portal asks for a fresh probe.
    client.get("/api/system/llm-status?refresh=true", headers={"X-Freja-Token": db_token})
    assert len(calls) == 2


def test_get_ollama_models_endpoint(db_token):
    client = TestClient(app)
    res = client.get("/api/system/ollama-models", headers={"X-Freja-Token": db_token})
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "id" in data[0] and "name" in data[0]

