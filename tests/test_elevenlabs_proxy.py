import os
import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key
from backend.routes.elevenlabs_proxy import MAX_TTS_CHARS


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture(autouse=True)
def restore_eleven_key():
    backup = get_api_key("freja_eleven_apikey")
    yield
    set_api_key("freja_eleven_apikey", backup or "")


def test_tts_rejects_oversized_text(auth_headers):
    """ElevenLabs bills per character - a single request must not be able to send unbounded
    text and run up cost with no server-side cap."""
    set_api_key("freja_eleven_apikey", "fake_key_for_test")
    client = TestClient(app)
    response = client.post(
        "/api/elevenlabs/tts/21m00Tcm4TlvDq8ikWAM",
        json={"text": "x" * (MAX_TTS_CHARS + 1)},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_tts_requires_api_key(auth_headers):
    set_api_key("freja_eleven_apikey", "")
    client = TestClient(app)
    response = client.post(
        "/api/elevenlabs/tts/21m00Tcm4TlvDq8ikWAM",
        json={"text": "hej"},
        headers=auth_headers,
    )
    assert response.status_code == 400


def test_cache_eviction_caps_the_number_of_cached_files(tmp_path, monkeypatch):
    """The voice cache must not grow without bound - once over the cap, the oldest files are
    evicted rather than accumulating forever (TTS text varies per response, so cache keys
    rarely repeat and nothing else ever cleaned this directory up)."""
    import time
    import backend.routes.elevenlabs_proxy as ep

    monkeypatch.setattr(ep, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(ep, "MAX_CACHE_FILES", 5)

    for i in range(15):
        path = tmp_path / f"clip_{i}.mp3"
        path.write_bytes(b"x")
        os.utime(path, (time.time() + i, time.time() + i))

    ep._evict_cache_if_needed()

    remaining = sorted(f for f in os.listdir(tmp_path) if f.endswith(".mp3"))
    assert len(remaining) == 5
    # The newest files must be the ones kept.
    assert remaining == [f"clip_{i}.mp3" for i in range(10, 15)]
