"""Tests for the provider-neutral llm_client facade.

Covers T-001 (Ollama-first with Gemini fallback) and the T-003 decision to keep that
automatic failover while surfacing which provider actually answered via
get_active_provider(). The active provider lives on a ContextVar, so it is read inside
the same coroutine that made the call - exactly how the real endpoints use it.
"""

import asyncio

import pytest

from backend.services import llm_client


def test_generate_json_reports_ollama_when_ollama_succeeds(monkeypatch):
    async def ok_ollama(*args, **kwargs):
        return {"answer": "from ollama"}
    monkeypatch.setattr(llm_client.ollama_client, "generate_json", ok_ollama)

    async def run():
        result = await llm_client.generate_json("hi")
        return result, llm_client.get_active_provider()

    result, provider = asyncio.run(run())
    assert result == {"answer": "from ollama"}
    assert provider == "ollama"


def test_generate_json_falls_back_to_gemini_and_reports_it(monkeypatch):
    async def fail_ollama(*args, **kwargs):
        raise Exception("connection refused")

    async def ok_gemini(*args, **kwargs):
        return {"answer": "from gemini"}

    monkeypatch.setattr(llm_client.ollama_client, "generate_json", fail_ollama)
    monkeypatch.setattr(llm_client.gemini_client, "get_gemini_api_key", lambda: "MOCK_KEY")
    monkeypatch.setattr(llm_client.gemini_client, "generate_json", ok_gemini)

    async def run():
        result = await llm_client.generate_json("hi")
        return result, llm_client.get_active_provider()

    result, provider = asyncio.run(run())
    assert result == {"answer": "from gemini"}
    assert provider == "gemini"


def test_generate_text_falls_back_to_gemini_and_reports_it(monkeypatch):
    async def fail_ollama(*args, **kwargs):
        raise Exception("connection refused")

    async def ok_gemini(*args, **kwargs):
        return "from gemini"

    monkeypatch.setattr(llm_client.ollama_client, "generate_text", fail_ollama)
    monkeypatch.setattr(llm_client.gemini_client, "get_gemini_api_key", lambda: "MOCK_KEY")
    monkeypatch.setattr(llm_client.gemini_client, "generate_text", ok_gemini)

    async def run():
        result = await llm_client.generate_text("hi")
        return result, llm_client.get_active_provider()

    result, provider = asyncio.run(run())
    assert result == "from gemini"
    assert provider == "gemini"


def test_generate_json_raises_when_no_provider_available(monkeypatch):
    async def fail_ollama(*args, **kwargs):
        raise Exception("connection refused")

    monkeypatch.setattr(llm_client.ollama_client, "generate_json", fail_ollama)
    monkeypatch.setattr(llm_client.gemini_client, "get_gemini_api_key", lambda: "")

    with pytest.raises(Exception) as exc:
        asyncio.run(llm_client.generate_json("hi"))
    assert "No LLM provider available" in str(exc.value)
