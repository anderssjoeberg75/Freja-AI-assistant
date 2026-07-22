import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture(autouse=True)
def restore_telegram_keys():
    backup = {
        "freja_telegram_bot_token": get_api_key("freja_telegram_bot_token"),
        "freja_telegram_chat_id": get_api_key("freja_telegram_chat_id"),
    }
    yield
    for key, value in backup.items():
        set_api_key(key, value or "")


def test_markdown_to_html_does_not_nest_tags_inside_code_spans():
    """A code span containing markdown-like characters (e.g. an asterisk) must not have
    <i>/<b> tags injected inside it - Telegram's HTML parse_mode rejects nested tags in
    <code>/<pre>, and the whole sendMessage call used to fail with HTTP 400 while the reply
    had already been recorded as sent in chat history."""
    from backend.services.telegram_service import markdown_to_html

    result = markdown_to_html("Use `a*b*c` and **bold** and *italic* text.")
    assert result == "Use <code>a*b*c</code> and <b>bold</b> and <i>italic</i> text."
    assert "<i>" not in result.split("<code>")[1].split("</code>")[0]


def test_markdown_to_html_preserves_html_special_chars_in_code():
    from backend.services.telegram_service import markdown_to_html

    result = markdown_to_html("`if a < b:`")
    assert result == "<code>if a &lt; b:</code>"


@pytest.mark.asyncio
async def test_send_telegram_message_falls_back_to_plain_text_on_html_rejection(monkeypatch):
    """When Telegram rejects the HTML payload (400), the message must still be retried as
    plain text instead of silently vanishing."""
    import backend.services.telegram_service as telegram_module

    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None, **k):
            calls.append(json)
            class R:
                def __init__(self, code):
                    self.status_code = code
                    self.text = "Bad Request: can't parse entities"
            # First call (HTML) fails; the plain-text retry succeeds.
            return R(400) if json.get("parse_mode") == "HTML" else R(200)

    monkeypatch.setattr(telegram_module, "shared_client", FakeClient)

    ok = await telegram_module.send_telegram_message(
        "fake_token", "123", "<code>a<i>b</i>c</code>", _plain_text_fallback="a*b*c"
    )
    assert ok is True
    assert len(calls) == 2
    assert calls[1]["text"] == "a*b*c"
    assert "parse_mode" not in calls[1]


def test_post_telegram_config_warns_when_env_var_shadows_db_value(auth_headers, monkeypatch):
    """If TELEGRAM_BOT_TOKEN is set in the server environment, get_telegram_config() prefers
    it over the DB - saving a new token here must not silently report plain success while the
    bot keeps using the old (possibly leaked/compromised) env-var token."""
    client = TestClient(app)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-fixed-token")
    try:
        response = client.post(
            "/api/telegram/config",
            json={"token": "new_token_from_ui", "chat_id": "12345"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert "token" in body.get("env_shadowed", [])
    finally:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


def test_post_telegram_config_no_warning_without_env_vars(auth_headers, monkeypatch):
    client = TestClient(app)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    response = client.post(
        "/api/telegram/config",
        json={"token": "some_token", "chat_id": "12345"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert "env_shadowed" not in response.json()
