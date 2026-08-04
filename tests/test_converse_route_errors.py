"""Error mapping for POST /api/chat/converse: a Gemini quota/spend-cap hit (429) must surface
as a clear 503 (temporary, upstream) rather than a naked 500, so the mobile client can show
"quota reached" and stay online. Other failures stay 500."""

import pytest
from fastapi.testclient import TestClient

from server import app
from backend.database import get_api_key
from backend.services import converse_service


@pytest.fixture
def token():
    return get_api_key("freja_access_token") or "freja1234"


def test_gemini_quota_error_maps_to_503(monkeypatch, token):
    async def boom(*args, **kwargs):
        raise RuntimeError("Gemini API error (HTTP 429): RESOURCE_EXHAUSTED spending cap reached")

    monkeypatch.setattr(converse_service, "generate_freja_reply", boom)
    res = TestClient(app).post(
        "/api/chat/converse",
        headers={"X-Freja-Token": token},
        json={"text": "Vad ser du?", "channel": "debug", "image_base64": "ZmFrZQ=="},
    )
    assert res.status_code == 503
    assert "kvot" in res.json()["detail"].lower()


def test_other_converse_error_stays_500(monkeypatch, token):
    async def boom(*args, **kwargs):
        raise RuntimeError("some unrelated failure")

    monkeypatch.setattr(converse_service, "generate_freja_reply", boom)
    res = TestClient(app).post(
        "/api/chat/converse",
        headers={"X-Freja-Token": token},
        json={"text": "Hej", "channel": "debug"},
    )
    assert res.status_code == 500
