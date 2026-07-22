import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


def test_delete_learned_entry_missing_id_returns_404(auth_headers):
    client = TestClient(app)
    response = client.delete("/api/learning/delete/999999999", headers=auth_headers)
    assert response.status_code == 404


def test_delete_learning_credentials_missing_domain_returns_404(auth_headers):
    client = TestClient(app)
    response = client.delete("/api/learning/credentials/no_such_domain_xyz", headers=auth_headers)
    assert response.status_code == 404


class _FakePage:
    def __init__(self):
        self.url = "https://example.com/article"

    async def goto(self, *a, **k):
        return None

    async def query_selector(self, *a, **k):
        return None

    async def evaluate(self, *a, **k):
        return "Some genuine scraped page text about the topic."

    async def close(self):
        return None


class _FakeBrowser:
    async def new_page(self):
        return _FakePage()

    async def close(self):
        return None


class _FakeChromium:
    async def launch(self, **k):
        return _FakeBrowser()


class _FakePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _fake_perform_search(topic):
    return [{"title": "A source", "link": "https://example.com/article"}]


@pytest.mark.asyncio
async def test_learn_topic_trims_whitespace_for_dedup(monkeypatch):
    """"growing onions" and " growing onions " must upsert the same row - the topic column
    is the ON CONFLICT dedup key, so untrimmed whitespace silently created duplicate rows."""
    import json
    from backend.database import get_db_connection
    from backend.services import learning_service

    monkeypatch.setattr(learning_service, "perform_search", _fake_perform_search)
    monkeypatch.setattr(learning_service, "async_playwright", lambda: _FakePlaywright())

    async def fake_gemini(prompt, system_instruction=""):
        return json.dumps({"summary": "En sammanfattning.", "detailed_notes": "Detaljer."})
    monkeypatch.setattr(learning_service, "call_gemini_learning_api", fake_gemini)

    with get_db_connection() as conn:
        conn.execute("DELETE FROM learned_knowledge WHERE topic = 'growing onions test'")
        conn.commit()

    try:
        result = await learning_service.learn_topic_impl("  growing onions test  ")
        assert result["status"] == "success"
        assert result["topic"] == "growing onions test"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM learned_knowledge WHERE topic = 'growing onions test'")
            assert cursor.fetchone()[0] == 1
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM learned_knowledge WHERE topic = 'growing onions test'")
            conn.commit()


@pytest.mark.asyncio
async def test_learn_topic_does_not_persist_when_cancelled_during_synthesis(monkeypatch):
    """A cancel that arrives while Gemini is synthesizing (the longest remaining phase) must
    not let the run finish and write to the DB anyway - only the pre-scrape and
    post-scrape abort checks existed before, so a cancel here was silently ignored and the
    run completed and persisted despite the UI already showing "cancelled"."""
    import json
    from backend.database import get_db_connection
    from backend.services import learning_service
    from backend.services.learning_service import LearningRun

    monkeypatch.setattr(learning_service, "perform_search", _fake_perform_search)
    monkeypatch.setattr(learning_service, "async_playwright", lambda: _FakePlaywright())

    run = LearningRun()

    async def fake_gemini_that_cancels_mid_call(prompt, system_instruction=""):
        run.aborted = True  # simulate a cancel request arriving while Gemini is running
        return json.dumps({"summary": "En sammanfattning.", "detailed_notes": "Detaljer."})
    monkeypatch.setattr(learning_service, "call_gemini_learning_api", fake_gemini_that_cancels_mid_call)

    with get_db_connection() as conn:
        conn.execute("DELETE FROM learned_knowledge WHERE topic = 'cancelled mid synthesis test'")
        conn.commit()

    try:
        result = await learning_service.learn_topic_impl("cancelled mid synthesis test", run=run)
        assert result["status"] == "cancelled"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM learned_knowledge WHERE topic = 'cancelled mid synthesis test'")
            assert cursor.fetchone()[0] == 0, "a cancelled run persisted to the database anyway"
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM learned_knowledge WHERE topic = 'cancelled mid synthesis test'")
            conn.commit()
