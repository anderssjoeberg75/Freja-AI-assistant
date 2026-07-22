import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, get_db_connection


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


def test_get_withings_data(auth_headers):
    client = TestClient(app)
    response = client.get("/api/withings/data?days=30", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_post_withings_data_partial_update_preserves_other_fields(auth_headers):
    """A manual weight-only correction must not null out fat_ratio/bone_mass/heart_pulse
    already synced for that date - the upsert used to overwrite every omitted field with
    NULL instead of preserving it."""
    client = TestClient(app)
    date_str = "2026-05-01"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM withings_measurements WHERE date = ?", (date_str,))
        cursor.execute(
            "INSERT INTO withings_measurements (date, weight, fat_ratio, bone_mass, heart_pulse) "
            "VALUES (?, ?, ?, ?, ?)",
            (date_str, 79.0, 18.5, 3.3, 58.0)
        )
        conn.commit()

    try:
        response = client.post(
            "/api/withings/data",
            json={"date": date_str, "weight": 80.5},
            headers=auth_headers
        )
        assert response.status_code == 200

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT weight, fat_ratio, bone_mass, heart_pulse FROM withings_measurements WHERE date = ?",
                (date_str,)
            )
            row = cursor.fetchone()
        assert row == (80.5, 18.5, 3.3, 58.0)
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM withings_measurements WHERE date = ?", (date_str,))
            conn.commit()


def test_delete_withings_log_missing_date_returns_404(auth_headers):
    client = TestClient(app)
    response = client.get("/api/withings/delete?date=2099-01-01", headers=auth_headers)
    assert response.status_code == 404


def test_sync_days_query_param_is_clamped(auth_headers):
    """An unbounded `days` must be clamped before it reaches the sync window math - an
    arbitrarily large value produces pathological date windows with no guardrail."""
    from backend.database import set_api_key
    import backend.routes.withings as withings_module

    set_api_key('freja_withings_client_id', 'withings123')
    set_api_key('freja_withings_client_secret', 'mock_secret')
    set_api_key('freja_withings_refresh_token', 'refreshtokentoken')

    captured = {}

    async def fake_sync_task(client_id, client_secret, refresh_token, days):
        captured["days"] = days

    monkeypatch_target = withings_module.run_withings_sync_task
    withings_module.run_withings_sync_task = fake_sync_task
    try:
        client = TestClient(app)
        response = client.get("/api/withings/sync?days=999999", headers=auth_headers)
        assert response.status_code == 200
    finally:
        withings_module.run_withings_sync_task = monkeypatch_target

    assert captured.get("days") == withings_module.MAX_SYNC_DAYS


@pytest.mark.asyncio
async def test_sync_raises_when_every_endpoint_fails(monkeypatch):
    """If measurements comes back with a non-zero application-level status (rate limit,
    revoked scope - Withings returns HTTP 200 for these) and sleep/activity both raise, the
    run must not report "success" - that hid a totally broken Withings connection
    indefinitely, since nothing here otherwise stops all-empty results from committing
    cleanly."""
    import backend.routes.withings as withings_module

    class FailingClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, *a, **k):
            if "oauth2" in url:
                class TokenResp:
                    def raise_for_status(self): return None
                    def json(self): return {"status": 0, "body": {"access_token": "tok", "refresh_token": "rt"}}
                return TokenResp()
            raise RuntimeError("Withings API is down")
        async def get(self, *a, **k):
            class MeasResp:
                def raise_for_status(self): return None
                def json(self): return {"status": 601}  # app-level failure, not an HTTP error
            return MeasResp()

    monkeypatch.setattr(withings_module, "shared_client", FailingClient)

    from backend.services.sync_status import get_sync_states
    await withings_module.run_withings_sync_task("real_id", "real_secret", "real_refresh", days=7)
    states = get_sync_states()
    assert states["states"]["withings"] == "error"
