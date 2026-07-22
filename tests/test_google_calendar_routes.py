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

@pytest.mark.asyncio
async def test_sync_keeps_events_outside_the_fetched_window(monkeypatch):
    """A sync must not delete events that lie outside the ±30-day window it fetched.

    The cleanup pass removes mapped events missing from the API response. It used to load
    every mapped row regardless of date, so a session booked more than 30 days ahead - PT
    plans run several weeks out - was deleted locally on the next sync while still living
    in Google Calendar, leaving its trainer_bookings row dangling.
    """
    import datetime
    import backend.routes.google_calendar as gcal

    far_future = (datetime.date.today() + datetime.timedelta(days=75)).strftime('%Y-%m-%d')
    in_window = (datetime.date.today() + datetime.timedelta(days=3)).strftime('%Y-%m-%d')

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for gid in ("evt-far-future", "evt-in-window"):
            cursor.execute("DELETE FROM google_calendar_events WHERE google_event_id = ?", (gid,))
        cursor.execute(
            """INSERT INTO google_calendar_events
               (google_event_id, summary, description, start_time, end_time, location)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("evt-far-future", "💪 Löpning: långt fram", "", f"{far_future}T08:00:00",
             f"{far_future}T09:00:00", "F.R.E.J.A. PT")
        )
        cursor.execute(
            """INSERT INTO google_calendar_events
               (google_event_id, summary, description, start_time, end_time, location)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("evt-in-window", "Möte", "", f"{in_window}T10:00:00", f"{in_window}T11:00:00", "")
        )
        conn.commit()

    async def fake_token():
        return "REAL_LOOKING_TOKEN"
    monkeypatch.setattr(gcal, "get_google_access_token", fake_token)

    class EmptyCalendarClient:
        """Google answers 200 with no events in the window."""
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **k):
            class R:
                status_code = 200
                text = ""
                def json(self):
                    return {"items": []}
            return R()

    monkeypatch.setattr(gcal, "shared_client", EmptyCalendarClient)
    await gcal.run_google_calendar_sync_task()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM google_calendar_events WHERE google_event_id = 'evt-far-future'")
        far_survived = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM google_calendar_events WHERE google_event_id = 'evt-in-window'")
        in_window_removed = cursor.fetchone()[0]

    assert far_survived == 1, "a sync deleted an event booked beyond the fetched window"
    # An event inside the window that Google no longer returns is genuinely gone.
    assert in_window_removed == 0


def test_delete_google_calendar_event_via_delete_verb(auth_headers):
    """The DELETE verb must work too, not just the legacy GET (kept for older clients) -
    a GET-based destructive endpoint risks the access token ending up in a query string
    (server logs, browser history, Referer) if ever called via ?token=... instead of the
    header."""
    client = TestClient(app)
    payload = {
        "summary": "DELETE-verb test event",
        "description": "",
        "start_time": "2026-07-03T14:00:00",
        "end_time": "2026-07-03T15:00:00",
        "location": ""
    }
    response = client.post("/api/google_calendar/data", json=payload, headers=auth_headers)
    assert response.status_code == 200
    event_id = response.json()["event"]["id"]

    del_response = client.delete(f"/api/google_calendar/delete?id={event_id}", headers=auth_headers)
    assert del_response.status_code == 200
    assert del_response.json().get("status") == "success"


@pytest.mark.asyncio
async def test_save_and_delete_raise_when_google_configured_but_unreachable(monkeypatch):
    """A configured-but-broken Google connection (revoked/expired grant, transient network
    failure) must not be treated the same as "no account connected" - that silently
    downgraded a save/delete to a local-only no-op that still reported success, which is
    exactly how a booked PT session could exist in the DB with nothing on the real calendar.
    """
    import backend.routes.google_calendar as gcal

    set_api_key('freja_google_calendar_client_id', 'real-client-id')
    set_api_key('freja_google_calendar_refresh_token', 'real-refresh-token')
    try:
        async def broken_token():
            return None
        monkeypatch.setattr(gcal, "get_google_access_token", broken_token)

        with pytest.raises(RuntimeError):
            await gcal.core_save_calendar_event(
                summary="x", start_time="2026-07-04T10:00", end_time="2026-07-04T11:00"
            )

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO google_calendar_events (google_event_id, summary, start_time, end_time) "
                "VALUES (?, ?, ?, ?)",
                ("some-real-google-id", "still live", "2026-07-04T10:00:00", "2026-07-04T11:00:00")
            )
            db_id = cursor.lastrowid
            conn.commit()

        try:
            with pytest.raises(RuntimeError):
                await gcal.core_delete_calendar_event(db_id)
        finally:
            with get_db_connection() as conn:
                conn.execute("DELETE FROM google_calendar_events WHERE id = ?", (db_id,))
                conn.commit()
    finally:
        # set_api_key also writes the legacy unprefixed alias (google_calendar_client_id /
        # google_calendar_refresh_token, see backend/database.py's KEY_ALIASES) - deleting
        # only the freja_-prefixed name left the alias row in place, and get_api_key's
        # alias fallback then kept resolving the fake credentials for every later test.
        with get_db_connection() as conn:
            conn.execute(
                "DELETE FROM api_keys WHERE key_name IN ("
                "'freja_google_calendar_client_id', 'freja_google_calendar_refresh_token', "
                "'google_calendar_client_id', 'google_calendar_refresh_token')"
            )
            conn.commit()


@pytest.mark.asyncio
async def test_sync_follows_pagination(monkeypatch):
    """A truncated single-page fetch didn't just miss new data - the cleanup pass deletes
    every locally-mapped event missing from the response, so page 2+ events were actively
    deleted locally while still live on Google."""
    import datetime
    import backend.routes.google_calendar as gcal

    in_window = (datetime.date.today() + datetime.timedelta(days=3)).strftime('%Y-%m-%d')

    async def fake_token():
        return "REAL_LOOKING_TOKEN"
    monkeypatch.setattr(gcal, "get_google_access_token", fake_token)

    class PagedCalendarClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url, *a, **k):
            class R:
                status_code = 200
                text = ""
                def __init__(self, body):
                    self._body = body
                def json(self):
                    return self._body
            if "pageToken" not in url:
                return R({
                    "items": [{"id": "evt-page1", "summary": "Page 1", "start": {"dateTime": f"{in_window}T09:00:00Z"}, "end": {"dateTime": f"{in_window}T10:00:00Z"}}],
                    "nextPageToken": "page2",
                })
            return R({
                "items": [{"id": "evt-page2", "summary": "Page 2", "start": {"dateTime": f"{in_window}T11:00:00Z"}, "end": {"dateTime": f"{in_window}T12:00:00Z"}}],
            })

    monkeypatch.setattr(gcal, "shared_client", PagedCalendarClient)
    try:
        await gcal.run_google_calendar_sync_task()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM google_calendar_events WHERE google_event_id IN ('evt-page1', 'evt-page2')")
            assert cursor.fetchone()[0] == 2
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM google_calendar_events WHERE google_event_id IN ('evt-page1', 'evt-page2')")
            conn.commit()


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
