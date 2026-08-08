"""Tests for the Ollama path in gemini_proxy.py -- T-072 (tool forwarding) and T-073
(generationConfig forwarding). Mocks the Ollama client at the function level so no
network calls are made.

The gemini_proxy route handler uses lazy imports (inside the function body), so we
patch at the actual module paths (backend.services.ollama_client, etc.) rather than
through backend.routes.gemini_proxy.
"""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.routes.gemini_proxy import router
from fastapi import FastAPI

# Minimal app that mounts only the gemini_proxy router
_app = FastAPI()
_app.include_router(router)


# Common payload shape mirroring what the client (gemini.js) sends
def _make_payload(*, tools=None, temperature=0.65, max_tokens=2048, user_text="Hej Freja"):
    payload = {
        "contents": [
            {"role": "user", "parts": [{"text": user_text}]}
        ],
        "systemInstruction": {
            "parts": [{"text": "Du ar Freja, en AI-assistent."}]
        },
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if tools is not None:
        payload["tools"] = tools
    return payload


SAMPLE_TOOL_DECLARATIONS = [
    {
        "name": "get_weather",
        "description": "Get current weather for a location.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {"type": "STRING", "description": "City name"}
            },
            "required": ["location"],
        },
    }
]

SAMPLE_GEMINI_TOOLS = [{"functionDeclarations": SAMPLE_TOOL_DECLARATIONS}]


# ---------- Fixtures / mocks ----------

@pytest.fixture
def client():
    return TestClient(_app)


def _mock_provider_status():
    return {
        "providers": {
            "ollama": {"ok": True, "model": "qwen2.5:14b"},
            "gemini": {"ok": True, "model": "gemini-2.5-flash"},
        }
    }


async def _mock_dispatch_ollama_first(label, ollama_fn, gemini_fn):
    """Simulates _dispatch always choosing the Ollama arm."""
    return await ollama_fn()


# ---------- T-073: generationConfig forwarding ----------

def test_ollama_receives_client_temperature_and_max_tokens(client):
    """_call_ollama must pass the client's temperature and maxOutputTokens through to
    the Ollama call instead of hardcoding them."""
    captured_kwargs = {}

    async def fake_generate_text(prompt, system_instruction="", temperature=0.2,
                                 timeout=60.0, max_tokens=800):
        captured_kwargs["temperature"] = temperature
        captured_kwargs["max_tokens"] = max_tokens
        return "Hej! Jag ar Freja."

    with patch("backend.services.llm_client.get_provider_status",
               new_callable=AsyncMock, return_value=_mock_provider_status()), \
         patch("backend.services.ollama_client.generate_text",
               side_effect=fake_generate_text), \
         patch("backend.services.ollama_client.get_ollama_model",
               return_value="qwen2.5:14b"), \
         patch("backend.services.llm_client._dispatch",
               side_effect=_mock_dispatch_ollama_first), \
         patch("backend.services.gemini_client.get_gemini_api_key",
               return_value="fake-key"), \
         patch("backend.services.system_context.build_backend_context_block",
               return_value=""), \
         patch("backend.services.system_context.build_runtime_provider_line",
               return_value=""):

        payload = _make_payload(temperature=0.42, max_tokens=1500)
        resp = client.post("/api/gemini/generate", json=payload)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert captured_kwargs["temperature"] == 0.42
        assert captured_kwargs["max_tokens"] == 1500


# ---------- T-072: tool declaration forwarding ----------

def test_ollama_uses_chat_with_tools_when_tools_present(client):
    """When the payload includes tool declarations, _call_ollama must call
    chat_with_tools instead of generate_text."""
    chat_with_tools_called = {"called": False}

    async def fake_chat_with_tools(messages, tools, *, temperature=0.5,
                                   timeout=30.0, max_tokens=2048):
        chat_with_tools_called["called"] = True
        chat_with_tools_called["num_tools"] = len(tools)
        return {"content": "Det ar soligt i Stockholm.", "role": "assistant"}

    with patch("backend.services.llm_client.get_provider_status",
               new_callable=AsyncMock, return_value=_mock_provider_status()), \
         patch("backend.services.ollama_client.chat_with_tools",
               side_effect=fake_chat_with_tools), \
         patch("backend.services.ollama_client.gemini_tools_to_ollama",
               wraps=lambda decls: [{"type": "function", "function": {"name": d["name"], "description": d.get("description", ""), "parameters": d.get("parameters", {})}} for d in decls]), \
         patch("backend.services.ollama_client.get_ollama_model",
               return_value="qwen2.5:14b"), \
         patch("backend.services.llm_client._dispatch",
               side_effect=_mock_dispatch_ollama_first), \
         patch("backend.services.gemini_client.get_gemini_api_key",
               return_value="fake-key"), \
         patch("backend.services.system_context.build_backend_context_block",
               return_value=""), \
         patch("backend.services.system_context.build_runtime_provider_line",
               return_value=""):

        payload = _make_payload(tools=SAMPLE_GEMINI_TOOLS)
        resp = client.post("/api/gemini/generate", json=payload)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert chat_with_tools_called["called"]
        assert chat_with_tools_called["num_tools"] == 1


def test_ollama_tool_call_returned_in_gemini_format(client):
    """When Ollama's response includes tool_calls, the proxy must wrap them in
    Gemini's functionCall shape so the client's tool loop handles them."""

    async def fake_chat_with_tools(messages, tools, **kwargs):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "get_weather",
                    "arguments": {"location": "Stockholm"},
                }
            }]
        }

    with patch("backend.services.llm_client.get_provider_status",
               new_callable=AsyncMock, return_value=_mock_provider_status()), \
         patch("backend.services.ollama_client.chat_with_tools",
               side_effect=fake_chat_with_tools), \
         patch("backend.services.ollama_client.gemini_tools_to_ollama",
               return_value=[{"type": "function", "function": {"name": "get_weather"}}]), \
         patch("backend.services.ollama_client.get_ollama_model",
               return_value="qwen2.5:14b"), \
         patch("backend.services.llm_client._dispatch",
               side_effect=_mock_dispatch_ollama_first), \
         patch("backend.services.gemini_client.get_gemini_api_key",
               return_value="fake-key"), \
         patch("backend.services.system_context.build_backend_context_block",
               return_value=""), \
         patch("backend.services.system_context.build_runtime_provider_line",
               return_value=""):

        payload = _make_payload(tools=SAMPLE_GEMINI_TOOLS)
        resp = client.post("/api/gemini/generate", json=payload)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        candidate = data["candidates"][0]
        fc = candidate["content"]["parts"][0].get("functionCall")
        assert fc is not None, "Expected a functionCall in the response"
        assert fc["name"] == "get_weather"
        assert fc["args"]["location"] == "Stockholm"


def test_ollama_no_tools_uses_generate_text(client):
    """When no tools are in the payload, _call_ollama must use the plain
    generate_text path (not chat_with_tools)."""
    generate_text_called = {"called": False}

    async def fake_generate_text(**kwargs):
        generate_text_called["called"] = True
        return "Hej!"

    with patch("backend.services.llm_client.get_provider_status",
               new_callable=AsyncMock, return_value=_mock_provider_status()), \
         patch("backend.services.ollama_client.generate_text",
               side_effect=fake_generate_text), \
         patch("backend.services.ollama_client.get_ollama_model",
               return_value="qwen2.5:14b"), \
         patch("backend.services.llm_client._dispatch",
               side_effect=_mock_dispatch_ollama_first), \
         patch("backend.services.gemini_client.get_gemini_api_key",
               return_value="fake-key"), \
         patch("backend.services.system_context.build_backend_context_block",
               return_value=""), \
         patch("backend.services.system_context.build_runtime_provider_line",
               return_value=""):

        payload = _make_payload()  # no tools
        resp = client.post("/api/gemini/generate", json=payload)

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert generate_text_called["called"]
