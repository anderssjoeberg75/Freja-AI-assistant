"""Tests for the provider-neutral llm_client facade.

Covers T-001 (Ollama-first with Gemini fallback), the T-003 decision to surface which
provider actually answered via get_active_provider(), and T-005's operator-selectable
provider (`freja_llm_provider` = auto/ollama/gemini) plus the reachability probe behind
the admin portal's indicator. The active provider lives on a ContextVar, so it is read
inside the same coroutine that made the call - exactly how the real endpoints use it.
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


def _set_preference(monkeypatch, value):
    """Pins what the admin portal has stored in `freja_llm_provider`."""
    monkeypatch.setattr(llm_client, "get_api_key", lambda key_name: value if key_name == "freja_llm_provider" else None)


@pytest.mark.parametrize("stored", [None, "", "  ", "banana"])
def test_unset_or_unknown_preference_means_auto(monkeypatch, stored):
    _set_preference(monkeypatch, stored)
    assert llm_client.get_provider_preference() == "auto"


def test_preference_is_case_insensitive(monkeypatch):
    _set_preference(monkeypatch, " Gemini ")
    assert llm_client.get_provider_preference() == "gemini"


def test_pinned_ollama_does_not_fall_back_to_gemini(monkeypatch):
    async def fail_ollama(*args, **kwargs):
        raise Exception("connection refused")

    async def unexpected_gemini(*args, **kwargs):
        raise AssertionError("Gemini must not be called when the provider is pinned to Ollama.")

    _set_preference(monkeypatch, "ollama")
    monkeypatch.setattr(llm_client.ollama_client, "generate_json", fail_ollama)
    monkeypatch.setattr(llm_client.gemini_client, "get_gemini_api_key", lambda: "MOCK_KEY")
    monkeypatch.setattr(llm_client.gemini_client, "generate_json", unexpected_gemini)

    with pytest.raises(Exception) as exc:
        asyncio.run(llm_client.generate_json("hi"))
    assert "pinned to Ollama" in str(exc.value)


def test_pinned_gemini_skips_ollama_entirely(monkeypatch):
    async def unexpected_ollama(*args, **kwargs):
        raise AssertionError("Ollama must not be called when the provider is pinned to Gemini.")

    async def ok_gemini(*args, **kwargs):
        return "from gemini"

    _set_preference(monkeypatch, "gemini")
    monkeypatch.setattr(llm_client.ollama_client, "generate_text", unexpected_ollama)
    monkeypatch.setattr(llm_client.gemini_client, "generate_text", ok_gemini)

    async def run():
        result = await llm_client.generate_text("hi")
        return result, llm_client.get_active_provider()

    result, provider = asyncio.run(run())
    assert result == "from gemini"
    assert provider == "gemini"


def test_auto_gemini_mode_uses_gemini_first_and_falls_back_to_ollama(monkeypatch):
    gemini_called = []
    ollama_called = []

    async def fail_gemini(*args, **kwargs):
        gemini_called.append(True)
        raise Exception("Gemini API quota exceeded")

    async def ok_ollama(*args, **kwargs):
        ollama_called.append(True)
        return "from ollama fallback"

    _set_preference(monkeypatch, "auto_gemini")
    monkeypatch.setattr(llm_client.gemini_client, "generate_text", fail_gemini)
    monkeypatch.setattr(llm_client.ollama_client, "generate_text", ok_ollama)

    async def run():
        result = await llm_client.generate_text("hi")
        return result, llm_client.get_active_provider()

    result, provider = asyncio.run(run())
    assert result == "from ollama fallback"
    assert provider == "ollama"
    assert len(gemini_called) == 1
    assert len(ollama_called) == 1


def _stub_health(monkeypatch, ollama_ok: bool, gemini_ok: bool):
    async def ollama_health(*args, **kwargs):
        return {"ok": ollama_ok, "detail": "", "model": "qwen2.5:14b", "base_url": "http://x", "models": []}

    async def gemini_health(*args, **kwargs):
        return {"ok": gemini_ok, "detail": "", "model": "gemini-2.5-flash"}

    monkeypatch.setattr(llm_client.ollama_client, "check_health", ollama_health)
    monkeypatch.setattr(llm_client.gemini_client, "check_health", gemini_health)


@pytest.mark.parametrize("preference,ollama_ok,gemini_ok,expected_active", [
    ("auto", True, True, "ollama"),        # Ollama wins while it is reachable
    ("auto", False, True, "gemini"),       # fallback is what would actually serve
    ("auto", False, False, None),          # nothing can serve - the card goes red
    ("auto_gemini", True, True, "gemini"),  # Gemini wins while it is reachable
    ("auto_gemini", True, False, "ollama"), # fallback to Ollama when Gemini down
    ("auto_gemini", False, False, None),    # neither reachable
    ("ollama", True, False, "ollama"),
    ("ollama", False, True, None),         # pinned: a healthy Gemini does not rescue it
    ("gemini", False, True, "gemini"),
    ("gemini", True, False, None),
])
def test_check_providers_resolves_the_serving_provider(monkeypatch, preference, ollama_ok, gemini_ok, expected_active):
    _set_preference(monkeypatch, preference)
    _stub_health(monkeypatch, ollama_ok, gemini_ok)

    status = asyncio.run(llm_client.check_providers())
    assert status["preference"] == preference
    assert status["active"] == expected_active
    assert status["providers"]["ollama"]["ok"] is ollama_ok
    assert status["providers"]["gemini"]["ok"] is gemini_ok

