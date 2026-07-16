import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key, get_db_connection

@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture
def _no_allowed_origins():
    """Run with an empty freja_allowed_origins allowlist; restore the original after."""
    original = get_api_key('freja_allowed_origins')
    set_api_key('freja_allowed_origins', '')
    yield
    if original:
        set_api_key('freja_allowed_origins', original)
    else:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM api_keys WHERE key_name = 'freja_allowed_origins'")
            conn.commit()

def test_get_google_calendar_data(auth_headers):
    client = TestClient(app)
    response = client.get("/api/google_calendar/data?days=30", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

def test_post_and_delete_google_calendar_event(auth_headers):
    client = TestClient(app)
    payload = {
        "summary": "Projektmöte F.R.E.J.A.",
        "description": "Genomgång av arkitektur och unit-tester",
        "start_time": "2026-07-02T14:00:00",
        "end_time": "2026-07-02T15:00:00",
        "location": "Virtual HUD Sector 09"
    }
    # Create event
    response = client.post("/api/google_calendar/data", json=payload, headers=auth_headers)
    assert response.status_code == 200
    res_json = response.json()
    assert res_json.get("status") == "success"
    event = res_json.get("event", {})
    event_id = event.get("id")
    assert event_id is not None

    # Delete created event
    del_response = client.get(f"/api/google_calendar/delete?id={event_id}", headers=auth_headers)
    assert del_response.status_code == 200
    assert del_response.json().get("status") == "success"

def test_google_calendar_sync_trigger(auth_headers):
    client = TestClient(app)
    response = client.get("/api/google_calendar/sync", headers=auth_headers)
    assert response.status_code == 200
    assert response.json().get("status") == "syncing"


# ---------------------------------------------------------------------------
# OAuth callback redirect target (open-redirect hardening)
# ---------------------------------------------------------------------------
class TestCallbackRedirectOrigin:
    @pytest.mark.parametrize("state", [
        "https://evil.example",
        "https://evil.example@testserver",   # userinfo smuggling
        "https://testserver.evil.example",   # prefix look-alike
        "//evil.example",                    # scheme-relative
        "javascript:alert(1)",
    ])
    def test_untrusted_state_is_refused(self, _no_allowed_origins, state):
        client = TestClient(app)
        res = client.get(
            "/api/google_calendar/callback",
            params={"code": "abc123", "state": state},
            follow_redirects=False,
        )
        assert res.status_code == 400, f"{state!r} should not be an accepted redirect target"
        # The authorization code must not leak to the untrusted host.
        assert "abc123" not in res.headers.get("location", "")

    def test_same_origin_state_redirects(self, _no_allowed_origins):
        client = TestClient(app)
        res = client.get(
            "/api/google_calendar/callback",
            params={"code": "abc123", "state": "http://testserver"},
            follow_redirects=False,
        )
        assert res.status_code == 307
        assert res.headers["location"] == "http://testserver/google_callback.html?code=abc123"

    @pytest.mark.parametrize("state", [
        "http://localhost:5000",     # the standalone HUD in the documented default setup
        "http://127.0.0.1:5000",
        "http://localhost:8000",
    ])
    def test_loopback_hud_origin_is_allowed_without_configuration(self, _no_allowed_origins, state):
        """The documented HUD-on-:5000 / backend-on-:8000 flow must work out of the box."""
        client = TestClient(app)
        res = client.get(
            "/api/google_calendar/callback",
            params={"code": "abc123", "state": state},
            follow_redirects=False,
        )
        assert res.status_code == 307
        assert res.headers["location"] == f"{state}/google_callback.html?code=abc123"

    def test_configured_origin_is_allowed(self, _no_allowed_origins):
        set_api_key('freja_allowed_origins', 'https://hud.example:8443, https://other.example')
        client = TestClient(app)
        res = client.get(
            "/api/google_calendar/callback",
            params={"code": "abc123", "state": "https://hud.example:8443/"},
            follow_redirects=False,
        )
        assert res.status_code == 307
        assert res.headers["location"] == "https://hud.example:8443/google_callback.html?code=abc123"

    def test_no_state_still_serves_exchange_page(self):
        client = TestClient(app)
        res = client.get("/api/google_calendar/callback", params={"code": "abc123"},
                         follow_redirects=False)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
