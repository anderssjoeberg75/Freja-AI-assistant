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


def test_grant_once_is_scoped_to_the_approved_action(db_token):
    """A one-time grant issued for one action of a multi-action tool must not authorize a
    different, un-approved action of the same tool within the grant's TTL.

    _grant_key used to key ONE_TIME_GRANTS by tool name alone (with a special-cased
    exception only for codex_git_ops push), so approving e.g. `run_windows_command`
    action_type=open_url also silently authorized action_type=run_cmd for the following
    120 seconds - a materially riskier action the user never saw.
    """
    key = "freja_tool_run_windows_command_allowed"
    backup = get_api_key(key)
    set_api_key(key, "false")
    client = TestClient(app)
    try:
        grant_resp = client.post(
            "/api/tools/grant_once",
            headers={"X-Freja-Token": db_token},
            json={"name": "run_windows_command", "args": {"action_type": "open_url", "target": "https://example.com"}},
        )
        assert grant_resp.status_code == 200

        # The approved action must now be authorized...
        exec_resp = client.post(
            "/api/tools/execute",
            headers={"X-Freja-Token": db_token},
            json={"name": "run_windows_command", "args": {"action_type": "open_url", "target": "https://example.com"}},
        )
        assert exec_resp.status_code == 200
    finally:
        set_api_key(key, backup or "")

    set_api_key(key, "false")
    try:
        # ...but a DIFFERENT action of the same tool, never approved, must not be.
        grant_resp = client.post(
            "/api/tools/grant_once",
            headers={"X-Freja-Token": db_token},
            json={"name": "run_windows_command", "args": {"action_type": "open_url", "target": "https://example.com"}},
        )
        assert grant_resp.status_code == 200

        exec_resp = client.post(
            "/api/tools/execute",
            headers={"X-Freja-Token": db_token},
            json={"name": "run_windows_command", "args": {"action_type": "run_cmd", "target": "whoami"}},
        )
        assert exec_resp.status_code == 403
    finally:
        set_api_key(key, backup or "")


def test_metadata_endpoint_covers_every_registered_tool(db_token):
    """The gateway's tool list must come from the registry, not a hand-kept copy.

    The frontend used to hold its own whitelist of tool -> permission key, and any tool
    added to the registry without a matching entry there was refused client-side with
    "Tool '<name>' not recognized" - which is what made get_trainer_workouts (and the
    Instagram tools) unusable from the web UI while the backend could run them fine.
    """
    from backend.services.tool_registry import TOOL_PERMISSION_KEYS

    client = TestClient(app)
    resp = client.get("/api/tools/metadata", headers={"X-Freja-Token": db_token})
    assert resp.status_code == 200

    served = {entry["name"]: entry["permission_key"] for entry in resp.json()}
    assert served == TOOL_PERMISSION_KEYS
