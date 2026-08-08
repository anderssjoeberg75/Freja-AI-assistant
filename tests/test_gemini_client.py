import pytest
from backend.database import get_api_key, set_api_key


@pytest.fixture(autouse=True)
def restore_gemini_key():
    backup = get_api_key("freja_gemini_apikey")
    yield
    set_api_key("freja_gemini_apikey", backup or "")


@pytest.mark.asyncio
async def test_generate_text_handles_empty_candidates_without_crashing(monkeypatch):
    """Gemini can return HTTP 200 with an explicitly empty "candidates" list (e.g. a
    safety-blocked prompt) - candidates[0] must not raise IndexError in that case."""
    import backend.services.gemini_client as gc

    set_api_key("freja_gemini_apikey", "fake_key_for_test")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(gc, "shared_client", lambda: FakeClient())

    result = await gc.generate_text("hello")
    assert result == ""


@pytest.mark.asyncio
async def test_check_health_keeps_the_api_key_out_of_the_failure_detail(monkeypatch):
    """The probe URL carries the API key as a query parameter and httpx echoes the URL in
    its error messages - that detail string is rendered in the admin portal and written to
    the system log, so the key must be stripped from it."""
    import backend.services.gemini_client as gc

    set_api_key("freja_gemini_apikey", "SECRET_TEST_KEY")

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, timeout=None):
            raise Exception(f"401 Unauthorized for url {url}")

    monkeypatch.setattr(gc, "shared_client", lambda: FakeClient())

    status = await gc.check_health()
    assert status["ok"] is False
    assert "SECRET_TEST_KEY" not in status["detail"]
    assert "***" in status["detail"]


@pytest.mark.asyncio
async def test_check_health_reports_a_missing_api_key(monkeypatch):
    import backend.services.gemini_client as gc

    monkeypatch.setattr(gc, "get_gemini_api_key", lambda: "")

    status = await gc.check_health()
    assert status["ok"] is False
    assert "No Gemini API key" in status["detail"]


@pytest.mark.asyncio
async def test_generate_json_success(monkeypatch):
    """T-086: Verify generate_json builds payload with responseMimeType and responseSchema,
    sends POST request via shared_client, and parses JSON output."""
    import backend.services.gemini_client as gc

    set_api_key("freja_gemini_apikey", "test_gemini_key")

    captured_url = None
    captured_payload = None

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "candidates": [{
                    "content": {
                        "parts": [{"text": '{"result": "success", "count": 42}'}]
                    }
                }]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            nonlocal captured_url, captured_payload
            captured_url = url
            captured_payload = json
            return FakeResponse()

    monkeypatch.setattr(gc, "shared_client", lambda: FakeClient())

    schema = {"type": "OBJECT", "properties": {"result": {"type": "STRING"}, "count": {"type": "INTEGER"}}}
    result = await gc.generate_json("Generate test JSON", schema=schema, system_instruction="System prompt")

    assert result == {"result": "success", "count": 42}
    assert "test_gemini_key" in captured_url
    assert captured_payload["generationConfig"]["responseMimeType"] == "application/json"
    assert captured_payload["generationConfig"]["responseSchema"] == schema
    assert captured_payload["systemInstruction"]["parts"][0]["text"] == "System prompt"


@pytest.mark.asyncio
async def test_generate_json_empty_response(monkeypatch):
    """Verify generate_json raises Exception when candidates text is empty."""
    import backend.services.gemini_client as gc

    set_api_key("freja_gemini_apikey", "test_gemini_key")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"candidates": []}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(gc, "shared_client", lambda: FakeClient())

    with pytest.raises(Exception, match="Gemini returned an empty response"):
        await gc.generate_json("hello")

