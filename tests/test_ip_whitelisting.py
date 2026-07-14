import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import set_api_key, get_api_key
import sqlite3
from backend.config import DB_FILE

@pytest.fixture
def clean_whitelists():
    # Store original allowed_ips if any
    original = get_api_key('freja_allowed_ips')
    yield
    # Restore allowed_ips
    if original is not None:
        set_api_key('freja_allowed_ips', original)
    else:
        # Delete key from database
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute("DELETE FROM api_keys WHERE key_name = 'freja_allowed_ips'")
                conn.commit()
        except Exception:
            pass

def test_ip_whitelist_allowed(clean_whitelists):
    set_api_key('freja_allowed_ips', '192.168.1.100, 10.0.0.5')
    token = get_api_key('freja_access_token') or "freja1234"
    
    # Remote client from whitelisted IP 192.168.1.100
    client = TestClient(app, client=("192.168.1.100", 50000))
    response = client.get("/api/keys", headers={"X-Freja-Token": token})
    assert response.status_code == 200

def test_ip_whitelist_blocked(clean_whitelists):
    set_api_key('freja_allowed_ips', '192.168.1.100, 10.0.0.5')
    token = get_api_key('freja_access_token') or "freja1234"
    
    # Remote client from non-whitelisted IP 192.168.1.200
    client = TestClient(app, client=("192.168.1.200", 50000))
    response = client.get("/api/keys", headers={"X-Freja-Token": token})
    assert response.status_code == 403
    assert "not in the allowed list" in response.json().get("detail", "")

def test_ip_whitelist_loopback_always_allowed(clean_whitelists):
    set_api_key('freja_allowed_ips', '192.168.1.100, 10.0.0.5')
    token = get_api_key('freja_access_token') or "freja1234"
    
    # Loopback client (localhost) must authenticate by default
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get("/api/keys")
    assert response.status_code == 401
    
    # Loopback client bypasses IP whitelisting when authenticated
    response = client.get("/api/keys", headers={"X-Freja-Token": token})
    assert response.status_code == 200
    
    # Loopback client bypasses authentication and IP whitelisting when FREJA_ALLOW_LOCALHOST_BYPASS=true is set
    import os
    os.environ["FREJA_ALLOW_LOCALHOST_BYPASS"] = "true"
    try:
        response = client.get("/api/keys")
        assert response.status_code == 200
    finally:
        del os.environ["FREJA_ALLOW_LOCALHOST_BYPASS"]
