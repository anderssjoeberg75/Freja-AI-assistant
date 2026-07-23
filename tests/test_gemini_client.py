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
