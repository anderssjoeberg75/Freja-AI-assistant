"""Tests for the channel-neutral Freja conversation core (backend/services/converse_service.py).

The LLM arms are mocked, so no network or real key is needed. The provider is pinned to
Gemini so llm_client._dispatch calls the (patched) Gemini arm directly.
"""

import pytest

from backend.database import get_db_connection, set_api_key
from backend.services import converse_service


def _seed_provider_gemini():
    set_api_key("freja_llm_provider", "gemini")
    set_api_key("freja_gemini_apikey", "test-key")


@pytest.mark.asyncio
async def test_generate_freja_reply_returns_text_and_persists_history(monkeypatch):
    _seed_provider_gemini()

    captured = {}

    async def fake_gemini(contents, api_key, system_prompt):
        captured["contents"] = contents
        captured["system_prompt"] = system_prompt
        return "Hej Anders, allt ser bra ut."

    monkeypatch.setattr(converse_service, "query_gemini_with_tools", fake_gemini)
    monkeypatch.setattr(converse_service, "build_chat_context_block", lambda: "")

    reply = await converse_service.generate_freja_reply("Hur mar jag?", channel="android")

    assert reply == "Hej Anders, allt ser bra ut."
    # The user's text is the final user turn in the outgoing contents (appended exactly once).
    assert captured["contents"][-1]["role"] == "user"
    assert captured["contents"][-1]["parts"][0]["text"] == "Hur mar jag?"
    user_turns = [c for c in captured["contents"] if c["parts"][0]["text"] == "Hur mar jag?"]
    assert len(user_turns) == 1  # not duplicated by the history read
    # Persona + directives are present in the system prompt.
    assert "FREJA" in captured["system_prompt"]
    assert "PERSONAL TRAINER & WORKOUT DISCUSSION" in captured["system_prompt"]

    # Both messages were written to chat_history under the android channel, user then assistant.
    with get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT sender, content, channel FROM chat_history ORDER BY id"
        ).fetchall()
    assert tuple(rows[-2]) == ("user", "Hur mar jag?", "android")
    assert tuple(rows[-1]) == ("assistant", "Hej Anders, allt ser bra ut.", "android")


@pytest.mark.asyncio
async def test_generate_freja_reply_attaches_image_to_user_turn(monkeypatch):
    _seed_provider_gemini()

    captured = {}

    async def fake_gemini(contents, api_key, system_prompt):
        captured["contents"] = contents
        return "Jag ser en cykel."

    monkeypatch.setattr(converse_service, "query_gemini_with_tools", fake_gemini)
    monkeypatch.setattr(converse_service, "build_chat_context_block", lambda: "")

    reply = await converse_service.generate_freja_reply(
        "Vad ser du?", channel="android", image_base64="ZmFrZQ=="
    )

    assert reply == "Jag ser en cykel."
    parts = captured["contents"][-1]["parts"]
    assert parts[0]["text"] == "Vad ser du?"
    assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    assert parts[1]["inlineData"]["data"] == "ZmFrZQ=="


@pytest.mark.asyncio
async def test_image_turn_routes_to_gemini_even_when_provider_prefers_ollama(monkeypatch):
    # Vision is Gemini-only. Even when freja_llm_provider prefers Ollama for text, an image
    # turn must go to the Gemini arm (the multimodal one) instead of being silently dropped
    # by Ollama. A Gemini key is configured, so the image can be analysed.
    set_api_key("freja_llm_provider", "ollama")
    set_api_key("freja_gemini_apikey", "test-key")

    calls = []

    async def fake_gemini(contents, api_key, system_prompt):
        calls.append("gemini")
        return "Jag ser en cykel."

    async def fake_ollama(contents, system_prompt):
        calls.append("ollama")
        return "text only, image dropped"

    # Simulate the preference actually routing text to Ollama, so a broken image path would
    # end up there. The fix must bypass this for image turns.
    async def fake_dispatch(label, ollama_arm, gemini_arm):
        calls.append("dispatch")
        return await ollama_arm()

    monkeypatch.setattr(converse_service, "query_gemini_with_tools", fake_gemini)
    monkeypatch.setattr(converse_service, "_telegram_tool_loop_ollama", fake_ollama)
    monkeypatch.setattr(converse_service.llm_client, "_dispatch", fake_dispatch)
    monkeypatch.setattr(converse_service, "build_chat_context_block", lambda: "")

    reply = await converse_service.generate_freja_reply(
        "Vad ser du?", channel="android", image_base64="ZmFrZQ=="
    )

    assert reply == "Jag ser en cykel."
    assert calls == ["gemini"], f"image turn must use the Gemini vision arm, got {calls}"


@pytest.mark.asyncio
async def test_generate_freja_reply_no_image_means_text_only_turn(monkeypatch):
    _seed_provider_gemini()

    captured = {}

    async def fake_gemini(contents, api_key, system_prompt):
        captured["contents"] = contents
        return "ok"

    monkeypatch.setattr(converse_service, "query_gemini_with_tools", fake_gemini)
    monkeypatch.setattr(converse_service, "build_chat_context_block", lambda: "")

    await converse_service.generate_freja_reply("Hej", channel="android")

    parts = captured["contents"][-1]["parts"]
    assert len(parts) == 1 and parts[0]["text"] == "Hej"


@pytest.mark.asyncio
async def test_generate_freja_reply_raises_when_no_provider(monkeypatch):
    set_api_key("freja_gemini_apikey", "")   # no Gemini key
    set_api_key("freja_llm_provider", "auto")

    async def fake_status():
        return {"providers": {"ollama": {"ok": False}, "gemini": {"ok": False}}}

    monkeypatch.setattr(converse_service.llm_client, "get_provider_status", fake_status)

    with pytest.raises(converse_service.ProviderUnavailable):
        await converse_service.generate_freja_reply("Hej", channel="android")
