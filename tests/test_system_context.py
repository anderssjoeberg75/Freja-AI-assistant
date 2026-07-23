"""Tests for the backend self-description injected into Freja's system prompt.

The block tells her what she is running on, so its facts have to be real ones - and it is
part of the prompt, which in Gemini mode is sent verbatim to Google, so no credential may
ever appear in it.
"""

import pytest

from backend.database import get_api_key, set_api_key
from backend.services import system_context


STATUS = {
    "preference": "ollama",
    "active": "ollama",
    "providers": {
        "ollama": {"ok": True, "detail": "Online.", "model": "qwen2.5:14b",
                   "base_url": "http://192.168.107.15:11434", "models": []},
        "gemini": {"ok": False, "detail": "No Gemini API key is configured.",
                   "model": "gemini-2.5-flash"},
    },
}

CLIENT_STATUS = {
    "active": True,
    "hostname": "freja-server",
    "client_hostname": "ANDERS-PC",
    "system": "Linux",
    "release": "6.8.0",
    "client_os": "Windows (likely Windows 11)",
}


def test_block_states_the_selected_provider_and_both_models():
    block = system_context.build_backend_context_block(STATUS, CLIENT_STATUS, "Windows 11")
    assert "ollama (pinned to the self-hosted server, no fallback)" in block
    assert "qwen2.5:14b" in block
    assert "http://192.168.107.15:11434" in block
    assert "gemini-2.5-flash" in block


def test_block_reports_each_provider_as_actually_probed():
    """The previous inline version read a key that does not exist on the status payload, so
    it reported both providers as offline no matter what the probe found."""
    block = system_context.build_backend_context_block(STATUS, CLIENT_STATUS)
    assert "Ollama (self-hosted): ONLINE" in block
    assert "Google Gemini API: OFFLINE (No Gemini API key is configured.)" in block


def test_offline_reason_is_trimmed_to_one_short_line():
    """httpx failure messages are multi-line and carry a URL plus a docs link. Prompt
    evaluation is the dominant cost when Ollama runs on CPU, so the reason is cut down."""
    noisy = dict(STATUS)
    noisy["providers"] = dict(STATUS["providers"])
    noisy["providers"]["gemini"] = {
        "ok": False,
        "detail": "API unreachable or key rejected: Client error '400 Bad Request' for url "
                  "'https://generativelanguage.googleapis.com/v1beta/models?key=***'\n"
                  "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400",
        "model": "gemini-2.5-flash",
    }
    block = system_context.build_backend_context_block(noisy, CLIENT_STATUS)
    gemini_line = next(line for line in block.splitlines() if "Google Gemini API:" in line)
    assert "developer.mozilla.org" not in gemini_line
    assert len(gemini_line) < 220
    assert "OFFLINE" in gemini_line


def test_block_names_both_machines():
    block = system_context.build_backend_context_block(STATUS, CLIENT_STATUS, "Windows 11")
    assert "'freja-server' (OS: Linux 6.8.0)" in block
    assert "'ANDERS-PC' (OS: Windows 11)" in block


def test_block_never_contains_a_credential():
    """Integrations are reported as configured / not configured - never by value."""
    secret = "SUPER_SECRET_VALUE_9f3a"
    backup = get_api_key("freja_strava_client_id")
    set_api_key("freja_strava_client_id", secret)
    try:
        block = system_context.build_backend_context_block(STATUS, CLIENT_STATUS)
        assert secret not in block
        assert "Strava configured" in block
    finally:
        set_api_key("freja_strava_client_id", backup or "")


def test_block_degrades_instead_of_raising_on_empty_input():
    """A failed probe must not take the whole chat turn down with it."""
    block = system_context.build_backend_context_block({}, {})
    assert "[BACKEND CONFIGURATION - LIVE AND AUTHORITATIVE]" in block
    assert "none - no provider is reachable" in block


@pytest.mark.parametrize("provider,model,expected", [
    ("ollama", "qwen2.5:14b", "Ollama (self-hosted), model 'qwen2.5:14b'"),
    ("gemini", "gemini-2.5-flash", "Google Gemini, model 'gemini-2.5-flash'"),
])
def test_runtime_line_names_the_engine_that_answered(provider, model, expected):
    assert expected in system_context.build_runtime_provider_line(provider, model)
