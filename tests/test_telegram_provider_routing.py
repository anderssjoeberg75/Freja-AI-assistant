"""Tests for routing the Telegram bot's tool-calling loop through llm_client.

The Telegram bot must obey the freja_llm_provider selector (auto/ollama/gemini) for its
Python-side tool calling, exactly as the main HUD chat already does. These tests cover the
new Ollama arm: the Gemini->Ollama tool-schema translator, the low-level chat_with_tools
helper, the history translation, and the tool loop (execution + authorization gate).
"""

import pytest

from backend.services import ollama_client
import backend.services.telegram_service as tg


# --------------------------------------------------------------------------
# Task 1: Gemini functionDeclarations -> Ollama OpenAI-style tools
# --------------------------------------------------------------------------

def test_gemini_tools_to_ollama_lowercases_types_and_wraps_functions():
    decls = [{
        "name": "get_steps",
        "description": "Gets step count",
        "parameters": {
            "type": "OBJECT",
            "properties": {"days": {"type": "INTEGER"}},
            "required": ["days"],
        },
    }]

    out = ollama_client.gemini_tools_to_ollama(decls)

    assert out == [{
        "type": "function",
        "function": {
            "name": "get_steps",
            "description": "Gets step count",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer"}},
                "required": ["days"],
            },
        },
    }]


def test_gemini_tools_to_ollama_defaults_missing_parameters():
    out = ollama_client.gemini_tools_to_ollama([{"name": "ping", "description": "p"}])
    assert out[0]["function"]["parameters"] == {"type": "object", "properties": {}}


# --------------------------------------------------------------------------
# Task 2: low-level chat_with_tools
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_with_tools_sends_tools_and_returns_message(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            captured["url"] = url
            captured["payload"] = json

            class R:
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return {"message": {"role": "assistant", "content": "hej"}}

            return R()

    monkeypatch.setattr(ollama_client, "shared_client", FakeClient)

    tools = [{"type": "function", "function": {
        "name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    msg = await ollama_client.chat_with_tools([{"role": "user", "content": "hi"}], tools)

    assert msg == {"role": "assistant", "content": "hej"}
    assert captured["payload"]["tools"] == tools
    assert captured["payload"]["stream"] is False
    assert captured["url"].endswith("/api/chat")


# --------------------------------------------------------------------------
# Task 3: history translation + tool loop
# --------------------------------------------------------------------------

def test_contents_to_ollama_messages_maps_roles():
    contents = [
        {"role": "user", "parts": [{"text": "hej"}]},
        {"role": "model", "parts": [{"text": "svar"}]},
    ]

    msgs = tg._gemini_contents_to_ollama_messages(contents, "SYS")

    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hej"},
        {"role": "assistant", "content": "svar"},
    ]


@pytest.mark.asyncio
async def test_ollama_loop_executes_tool_then_returns_text(monkeypatch):
    chat_msgs = iter([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "get_steps", "arguments": {"days": 1}}}]},
        {"role": "assistant", "content": "Du gick 5000 steg."},
    ])

    async def fake_chat(messages, tools, **k):
        return next(chat_msgs)

    executed = []

    async def fake_execute(name, args, progress_callback=None):
        executed.append((name, args))
        return {"steps": 5000}

    monkeypatch.setattr(ollama_client, "chat_with_tools", fake_chat)
    monkeypatch.setattr(tg, "execute_tool", fake_execute)
    monkeypatch.setattr("backend.routes.tools.is_tool_execution_authorized", lambda n, a: True)

    contents = [{"role": "user", "parts": [{"text": "hur manga steg gick jag?"}]}]
    reply = await tg._telegram_tool_loop_ollama(contents, "SYS")

    assert reply == "Du gick 5000 steg."
    assert executed == [("get_steps", {"days": 1})]


@pytest.mark.asyncio
async def test_ollama_loop_denies_unauthorized_tool(monkeypatch):
    chat_msgs = iter([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "system_update", "arguments": {}}}]},
        {"role": "assistant", "content": "Det tillats inte."},
    ])

    async def fake_chat(messages, tools, **k):
        return next(chat_msgs)

    executed = []

    async def fake_execute(name, args, progress_callback=None):
        executed.append(name)
        return {}

    monkeypatch.setattr(ollama_client, "chat_with_tools", fake_chat)
    monkeypatch.setattr(tg, "execute_tool", fake_execute)
    monkeypatch.setattr("backend.routes.tools.is_tool_execution_authorized", lambda n, a: False)

    contents = [{"role": "user", "parts": [{"text": "uppdatera dig"}]}]
    reply = await tg._telegram_tool_loop_ollama(contents, "SYS")

    assert reply == "Det tillats inte."
    assert executed == []  # a denied tool is never executed


@pytest.mark.asyncio
async def test_ollama_loop_accepts_json_string_arguments(monkeypatch):
    """Some Ollama builds return tool-call arguments as a JSON string, not a dict."""
    chat_msgs = iter([
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "get_steps", "arguments": '{"days": 7}'}}]},
        {"role": "assistant", "content": "klart"},
    ])

    async def fake_chat(messages, tools, **k):
        return next(chat_msgs)

    seen = []

    async def fake_execute(name, args, progress_callback=None):
        seen.append(args)
        return {"ok": True}

    monkeypatch.setattr(ollama_client, "chat_with_tools", fake_chat)
    monkeypatch.setattr(tg, "execute_tool", fake_execute)
    monkeypatch.setattr("backend.routes.tools.is_tool_execution_authorized", lambda n, a: True)

    await tg._telegram_tool_loop_ollama([{"role": "user", "parts": [{"text": "x"}]}], "SYS")

    assert seen == [{"days": 7}]
