"""Tests for the Ollama reachability probe behind the admin portal's provider indicator.

check_health() must never raise: the portal turns its result straight into a green or red
light, so an unreachable server has to come back as ok=False with a readable reason.
"""

import pytest

import backend.services.ollama_client as oc


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_client(monkeypatch, payload=None, error=None):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, timeout=None):
            if error:
                raise error
            return _FakeResponse(payload)

    monkeypatch.setattr(oc, "shared_client", lambda: FakeClient())


@pytest.mark.parametrize("stored,expected", [
    (None, oc.DEFAULT_NUM_CTX),
    ("", oc.DEFAULT_NUM_CTX),
    ("8192", 8192),
    ("  4096  ", 4096),
    ("not-a-number", oc.DEFAULT_NUM_CTX),   # a typo must not take the LLM down
    ("64", oc.DEFAULT_NUM_CTX),             # below MIN_NUM_CTX
    ("999999999", oc.DEFAULT_NUM_CTX),      # above MAX_NUM_CTX
])
def test_num_ctx_setting_falls_back_to_the_default_on_anything_unusable(monkeypatch, stored, expected):
    """Ollama rejects an out-of-range num_ctx outright, so a bad value in the settings field
    would cost every LLM call rather than just that field."""
    monkeypatch.setattr(oc, "get_api_key", lambda key: stored if key == "freja_ollama_num_ctx" else None)
    assert oc.get_ollama_num_ctx() == expected


@pytest.mark.parametrize("stored,expected", [
    (None, oc.DEFAULT_KEEP_ALIVE),
    ("", oc.DEFAULT_KEEP_ALIVE),
    ("24h", "24h"),
    (" -1 ", "-1"),
])
def test_keep_alive_setting(monkeypatch, stored, expected):
    monkeypatch.setattr(oc, "get_api_key", lambda key: stored if key == "freja_ollama_keep_alive" else None)
    assert oc.get_ollama_keep_alive() == expected


@pytest.mark.asyncio
async def test_requests_carry_keep_alive_and_the_configured_context(monkeypatch):
    """Both are per-request fields: without keep_alive Ollama evicts the model after its own
    5-minute default and the next call pays a full reload."""
    sent = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            sent.update(json)
            return _FakeResponse({"message": {"content": "ok"}})

    monkeypatch.setattr(oc, "shared_client", lambda: FakeClient())
    monkeypatch.setattr(oc, "get_ollama_num_ctx", lambda: 8192)
    monkeypatch.setattr(oc, "get_ollama_keep_alive", lambda: "30m")

    await oc.generate_text("hi")
    assert sent["keep_alive"] == "30m"
    assert sent["options"]["num_ctx"] == 8192
    assert sent["options"]["num_predict"] == oc.DEFAULT_TEXT_MAX_TOKENS


@pytest.mark.asyncio
async def test_health_is_ok_when_the_configured_model_is_installed(monkeypatch):
    monkeypatch.setattr(oc, "get_ollama_model", lambda: "qwen2.5:14b")
    _fake_client(monkeypatch, payload={"models": [{"name": "qwen2.5:14b"}, {"name": "llama3:8b"}]})

    status = await oc.check_health()
    assert status["ok"] is True
    assert status["models"] == ["llama3:8b", "qwen2.5:14b"]


@pytest.mark.asyncio
async def test_untagged_model_name_matches_the_latest_tag(monkeypatch):
    """A configured name without a tag means Ollama's implicit ":latest"."""
    monkeypatch.setattr(oc, "get_ollama_model", lambda: "llama3")
    _fake_client(monkeypatch, payload={"models": [{"name": "llama3:latest"}]})

    status = await oc.check_health()
    assert status["ok"] is True


@pytest.mark.asyncio
async def test_health_reports_a_reachable_server_missing_the_model(monkeypatch):
    monkeypatch.setattr(oc, "get_ollama_model", lambda: "qwen2.5:14b")
    _fake_client(monkeypatch, payload={"models": [{"name": "llama3:8b"}]})

    status = await oc.check_health()
    assert status["ok"] is False
    assert "qwen2.5:14b" in status["detail"]


@pytest.mark.asyncio
async def test_health_reports_an_unreachable_server_instead_of_raising(monkeypatch):
    _fake_client(monkeypatch, error=OSError("connection refused"))

    status = await oc.check_health()
    assert status["ok"] is False
    assert "unreachable" in status["detail"].lower()
    assert status["models"] == []
