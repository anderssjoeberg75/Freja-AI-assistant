import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, get_db_connection, set_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


def test_get_fitbit_health_data(auth_headers):
    client = TestClient(app)
    response = client.get("/api/fitbit/health?days=30", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_post_fitbit_data(auth_headers):
    client = TestClient(app)
    date_str = "2026-06-01"

    with get_db_connection() as conn:
        conn.execute("DELETE FROM fitbit_health WHERE date = ?", (date_str,))
        conn.commit()

    try:
        response = client.post(
            "/api/fitbit/data",
            json={
                "date": date_str,
                "steps": 10500,
                "sleep_hours": 7.8,
                "resting_hr": 55,
                "active_calories": 450
            },
            headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json().get("status") == "success"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT steps, sleep_hours, resting_hr, active_calories FROM fitbit_health WHERE date = ?", (date_str,))
            row = cursor.fetchone()
        assert row == (10500, 7.8, 55, 450)
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM fitbit_health WHERE date = ?", (date_str,))
            conn.commit()


def test_fitbit_credentials(auth_headers):
    set_api_key('freja_fitbit_client_id', 'test_client_id_123')
    client = TestClient(app)
    response = client.get("/api/fitbit/credentials", headers=auth_headers)
    assert response.status_code == 200
    assert response.json().get("client_id") == 'test_client_id_123'


def test_fitbit_callback_no_code(auth_headers):
    client = TestClient(app)
    response = client.get("/api/fitbit/callback", headers=auth_headers)
    assert response.status_code == 400
    assert "No authorization code" in response.text


def test_sync_days_query_param_is_clamped(auth_headers):
    import backend.routes.fitbit as fitbit_module

    set_api_key('freja_fitbit_client_id', 'fitbit123')
    set_api_key('freja_fitbit_client_secret', 'mock_secret')
    set_api_key('freja_fitbit_refresh_token', 'refreshtokentoken')

    captured = {}

    async def fake_sync_task(client_id, client_secret, refresh_token, days):
        captured["days"] = days

    monkeypatch_target = fitbit_module.run_fitbit_sync_task
    fitbit_module.run_fitbit_sync_task = fake_sync_task
    try:
        client = TestClient(app)
        response = client.get("/api/fitbit/sync?days=999999", headers=auth_headers)
        assert response.status_code == 200
    finally:
        fitbit_module.run_fitbit_sync_task = monkeypatch_target

    assert captured.get("days") == fitbit_module.MAX_SYNC_DAYS


@pytest.mark.asyncio
async def test_run_fitbit_sync_task_mock_credentials():
    import backend.routes.fitbit as fitbit_module
    from backend.services.sync_status import sync_states

    await fitbit_module.run_fitbit_sync_task('fitbit123', 'mock_secret', 'MOCK_REFRESH_TOKEN', 1)
    assert sync_states.get("fitbit") == "success"


@pytest.mark.asyncio
async def test_run_fitbit_sync_task_failed_api_calls_preserves_db():
    import backend.routes.fitbit as fitbit_module
    from backend.services.sync_status import sync_states
    from unittest.mock import AsyncMock, patch

    date_str = "2026-06-15"

    with get_db_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO fitbit_health (date, steps) VALUES (?, ?)", (date_str, 9999))
        conn.commit()

    fake_response = AsyncMock()
    fake_response.status_code = 401

    mock_client = AsyncMock()
    mock_client.get.return_value = fake_response

    class MockContextManager:
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, exc_type, exc, tb):
            pass

    try:
        with patch("backend.routes.fitbit.shared_client", return_value=MockContextManager()), \
             patch("backend.routes.fitbit.get_fitbit_access_token", AsyncMock(return_value="valid_token")), \
             patch("backend.routes.fitbit.today_local") as mock_today:
            from datetime import date
            mock_today.return_value = date(2026, 6, 15)

            await fitbit_module.run_fitbit_sync_task('valid_client', 'secret', 'refresh_token', 1)
            assert sync_states.get("fitbit") == "error"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT steps FROM fitbit_health WHERE date = ?", (date_str,))
            row = cursor.fetchone()
        assert row is not None and row[0] == 9999
    finally:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM fitbit_health WHERE date = ?", (date_str,))
            conn.commit()

