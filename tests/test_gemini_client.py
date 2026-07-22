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
