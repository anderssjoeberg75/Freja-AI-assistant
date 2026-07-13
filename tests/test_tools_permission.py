"""Regression tests for the /api/tools/execute permission gate.

A previous bug wrapped the intentional 400/403 HTTPExceptions in a generic
`except Exception -> 500` handler, so a permission-denied call returned 500 and the
frontend's "allow once" prompt (which keys off a 403) never triggered.
"""

import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key


@pytest.fixture
def db_token():
    return get_api_key("freja_access_token") or "freja1234"


@pytest.fixture
def block_publish_ig():
    """Ensure the publish_instagram_post tool is NOT permanently allowed for the test."""
    key = "freja_tool_publish_instagram_post_allowed"
    backup = get_api_key(key)
    set_api_key(key, "false")
    yield
    set_api_key(key, backup or "")


def test_execute_gated_tool_returns_403(db_token, block_publish_ig):
    client = TestClient(app)
    resp = client.post(
        "/api/tools/execute",
        headers={"X-Freja-Token": db_token},
        json={"name": "publish_instagram_post", "args": {"media_url": "x", "caption": "y"}},
    )
    assert resp.status_code == 403
    assert "Permission is missing" in resp.json()["detail"]


def test_execute_missing_name_returns_400(db_token):
    client = TestClient(app)
    resp = client.post(
        "/api/tools/execute",
        headers={"X-Freja-Token": db_token},
        json={"args": {}},
    )
    assert resp.status_code == 400


def test_execute_allowed_tool_starts_task(db_token):
    """A permanently-allowed tool should be accepted (202-style processing), not gated."""
    key = "freja_tool_get_weather_allowed"
    backup = get_api_key(key)
    set_api_key(key, "true")
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/tools/execute",
            headers={"X-Freja-Token": db_token},
            json={"name": "get_weather", "args": {"location": "Stockholm"}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"
    finally:
        set_api_key(key, backup or "")
