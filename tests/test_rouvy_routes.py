import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from server import app
from backend.database import get_db_connection, set_api_key, get_api_key
from backend.routes.rouvy import run_rouvy_sync_task_blocking, init_rouvy_db
from backend.services.rouvy_client.models import UserProfile, TrainingZones, ActivitySummary, Activity

client = TestClient(app)

def get_auth_headers():
    token = get_api_key('freja_access_token') or ""
    return {"X-Freja-Token": token} if token else {}

@pytest.fixture(autouse=True)
def setup_rouvy_db():
    init_rouvy_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rouvy_profile")
        cursor.execute("DELETE FROM rouvy_activities")
        cursor.execute("DELETE FROM rouvy_zones")
        cursor.execute("DELETE FROM rouvy_career")
        conn.commit()
    yield

def test_rouvy_endpoints_empty():
    """Verify Rouvy GET endpoints return empty/null structures when no data exists."""
    headers = get_auth_headers()
    resp = client.get("/api/rouvy/profile", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["profile"] is None

    resp = client.get("/api/rouvy/activities", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["activities"] == []

    resp = client.get("/api/rouvy/zones", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["zones"] is None

def test_rouvy_sync_missing_credentials():
    """Verify GET /api/rouvy/sync returns 400 when credentials are not configured."""
    set_api_key("freja_rouvy_email", "")
    set_api_key("freja_rouvy_password", "")
    headers = get_auth_headers()
    resp = client.get("/api/rouvy/sync", headers=headers)
    assert resp.status_code == 400
    assert "missing" in resp.json()["detail"].lower()

@patch("backend.routes.rouvy.RouvyClient")
def test_rouvy_sync_task_success(mock_client_cls):
    """Test successful Rouvy sync populating database tables."""
    set_api_key("freja_rouvy_email", "user@example.com")
    set_api_key("freja_rouvy_password", "secret123")

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    mock_prof = UserProfile(
        username="cyclist123",
        first_name="Test",
        last_name="Cyclist",
        gender="male",
        height_cm=180,
        weight_kg=75.5,
        ftp_watts=280,
        max_heart_rate=190
    )
    mock_zones = TrainingZones(
        ftp_watts=280,
        max_heart_rate=190,
        power_zone_values=[150, 210, 250],
        hr_zone_values=[120, 145, 165]
    )
    mock_summary = ActivitySummary(
        recent_activities=[
            Activity(
                activity_id="act_001",
                title="Passo dello Stelvio",
                start_utc="2026-07-28",
                distance_m=24500.0,
                moving_time_seconds=3600,
                elevation_m=1500.0
            )
        ]
    )

    mock_client.get_user_profile.return_value = mock_prof
    mock_client.get_training_zones.return_value = mock_zones
    mock_client.get_activity_summary.return_value = mock_summary

    run_rouvy_sync_task_blocking("user@example.com", "secret123")

    headers = get_auth_headers()
    prof_resp = client.get("/api/rouvy/profile", headers=headers)
    assert prof_resp.status_code == 200
    pdata = prof_resp.json()["profile"]
    assert pdata["username"] == "cyclist123"
    assert pdata["ftp"] == 280

    act_resp = client.get("/api/rouvy/activities", headers=headers)
    assert act_resp.status_code == 200
    acts = act_resp.json()["activities"]
    assert len(acts) == 1
    assert acts[0]["id"] == "act_001"
    assert acts[0]["title"] == "Passo dello Stelvio"

    zone_resp = client.get("/api/rouvy/zones", headers=headers)
    assert zone_resp.status_code == 200
    zdata = zone_resp.json()["zones"]
    assert zdata is not None
