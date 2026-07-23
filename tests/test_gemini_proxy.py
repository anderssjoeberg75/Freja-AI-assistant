import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture(autouse=True)
def restore_gemini_key():
    backup = get_api_key("freja_gemini_apikey")
    yield
    set_api_key("freja_gemini_apikey", backup or "")


def test_gemini_generate_rejects_malformed_model_name(auth_headers):
    """A model identifier must match Gemini's own naming shape before being spliced into the
    outbound request URL - previously any string (including path-breaking characters) was
    forwarded to Google unchecked."""
    set_api_key("freja_gemini_apikey", "fake_key_for_test")
    client = TestClient(app)
    response = client.post(
        "/api/gemini/generate?model=../evil/path",
        json={"contents": []},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_gemini_generate_rejects_oversized_payload(auth_headers):
    """A single request must not be able to run up unbounded cost against the server's own
    Gemini API key - a request body over the sanity cap is rejected before being forwarded."""
    set_api_key("freja_gemini_apikey", "fake_key_for_test")
    client = TestClient(app)
    huge_payload = {"contents": [{"role": "user", "parts": [{"text": "x" * 2_100_000}]}]}
    response = client.post("/api/gemini/generate", json=huge_payload, headers=auth_headers)
    assert response.status_code == 413


def _stub_provider_status(monkeypatch, ollama_ok: bool, gemini_ok: bool = False):
    """Pins what the chat proxy sees as provider health, and empties the shared cache so a
    previous test's probe cannot answer for this one."""
    from backend.services import llm_client

    async def status(*args, **kwargs):
        return {
            "preference": "auto",
            "active": "ollama" if ollama_ok else ("gemini" if gemini_ok else None),
            "providers": {
                "ollama": {"ok": ollama_ok, "detail": "", "model": "qwen2.5:14b",
                           "base_url": "http://ollama.test:11434", "models": []},
                "gemini": {"ok": gemini_ok, "detail": "", "model": "gemini-2.5-flash"},
            },
        }

    llm_client._status_cache["payload"] = None
    llm_client._status_cache["expires_at"] = 0.0
    monkeypatch.setattr(llm_client, "check_providers", status)


def test_gemini_generate_requires_api_key(auth_headers, monkeypatch):
    """With no Gemini key AND no reachable Ollama server, there is nothing to answer with."""
    set_api_key("freja_gemini_apikey", "")
    _stub_provider_status(monkeypatch, ollama_ok=False)
    client = TestClient(app)
    response = client.post("/api/gemini/generate", json={"contents": []}, headers=auth_headers)
    assert response.status_code == 400


def test_missing_gemini_key_is_not_fatal_when_ollama_is_reachable(auth_headers, monkeypatch):
    """A self-hosted-only setup has no Google credentials at all. The proxy used to reject
    those requests with "Gemini API key is not configured" before ever trying Ollama,
    because the health lookup read a key that does not exist on the status payload and so
    was always falsy."""
    set_api_key("freja_gemini_apikey", "")
    _stub_provider_status(monkeypatch, ollama_ok=True)

    from backend.services import ollama_client

    async def fake_generate_text(*args, **kwargs):
        return "Hej Anders!"

    monkeypatch.setattr(ollama_client, "generate_text", fake_generate_text)

    client = TestClient(app)
    response = client.post(
        "/api/gemini/generate",
        json={"contents": [{"role": "user", "parts": [{"text": "Hej"}]}]},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["candidates"][0]["content"]["parts"][0]["text"] == "Hej Anders!"


def test_the_serving_engine_is_named_in_the_system_prompt(auth_headers, monkeypatch):
    """The block is injected before dispatch, so only the provider branch can state which
    engine actually answered - injecting a guess up front made Freja name the wrong engine
    whenever the fallback fired."""
    set_api_key("freja_gemini_apikey", "")
    _stub_provider_status(monkeypatch, ollama_ok=True)

    from backend.services import ollama_client
    seen = {}

    async def capture(prompt, system_instruction="", *args, **kwargs):
        seen["system_instruction"] = system_instruction
        return "ok"

    monkeypatch.setattr(ollama_client, "generate_text", capture)

    client = TestClient(app)
    client.post(
        "/api/gemini/generate",
        json={
            "contents": [{"role": "user", "parts": [{"text": "Vilken motor kör du på?"}]}],
            "systemInstruction": {"parts": [{"text": "You are FREJA."}]},
        },
        headers=auth_headers,
    )

    instruction = seen["system_instruction"]
    assert "ENGINE SERVING THIS REPLY: Ollama (self-hosted), model 'qwen2.5:14b'" in instruction
    assert "[BACKEND CONFIGURATION - LIVE AND AUTHORITATIVE]" in instruction
    assert "You are FREJA." in instruction
