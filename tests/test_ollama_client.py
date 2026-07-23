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
