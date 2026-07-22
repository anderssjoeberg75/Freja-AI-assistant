import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, set_api_key


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture(autouse=True)
def restore_mem0_key():
    backup = get_api_key("freja_mem0_apikey")
    yield
    set_api_key("freja_mem0_apikey", backup or "")


def test_mem0_delete_rejects_malformed_memory_id(auth_headers):
    """A crafted memory_id like "abc?foo=bar" must be rejected before it ever reaches an
    f-string-built outbound URL to mem0.ai, where it could append attacker-chosen query
    parameters to the real API request. Percent-encoded so the literal characters reach the
    path parameter instead of being parsed as URL/query delimiters by the test client."""
    import urllib.parse
    set_api_key("freja_mem0_apikey", "fake_key_for_test")
    client = TestClient(app)
    for bad_id in ["abc?foo=bar", "abc#frag", "abc def"]:
        encoded = urllib.parse.quote(bad_id, safe="")
        response = client.delete(f"/api/mem0/delete/{encoded}", headers=auth_headers)
        assert response.status_code == 400


def test_mem0_delete_accepts_well_formed_uuid_shaped_id(auth_headers, monkeypatch):
    """A normal mem0 UUID-shaped ID must still pass validation and reach the proxy call."""
    import backend.routes.mem0_proxy as mem0_module
    set_api_key("freja_mem0_apikey", "fake_key_for_test")

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def delete(self, url, headers=None, **k):
            assert "memories/123e4567-e89b-12d3-a456-426614174000/" in url
            class R:
                status_code = 200
                content = b'{"status": "ok"}'
            return R()

    monkeypatch.setattr(mem0_module, "shared_client", FakeClient)
    client = TestClient(app)
    response = client.delete("/api/mem0/delete/123e4567-e89b-12d3-a456-426614174000", headers=auth_headers)
    assert response.status_code == 200


def test_mem0_wipe_passes_user_id_via_params_not_url_string(auth_headers, monkeypatch):
    """user_id must be passed through httpx's params= (percent-encoded), not spliced into an
    f-string URL, so a crafted value can't inject extra query parameters into the request
    sent to mem0.ai."""
    import backend.routes.mem0_proxy as mem0_module
    set_api_key("freja_mem0_apikey", "fake_key_for_test")

    captured = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def delete(self, url, params=None, headers=None, **k):
            captured["url"] = url
            captured["params"] = params
            class R:
                status_code = 200
                content = b'{"status": "ok"}'
            return R()

    monkeypatch.setattr(mem0_module, "shared_client", FakeClient)
    client = TestClient(app)
    response = client.delete("/api/mem0/wipe?user_id=freja_user%26evil%3D1", headers=auth_headers)
    assert response.status_code == 200
    assert captured["params"] == {"user_id": "freja_user&evil=1"}
    assert "?" not in captured["url"]


def test_mem0_routes_require_api_key_configured(auth_headers):
    set_api_key("freja_mem0_apikey", "")
    client = TestClient(app)
    assert client.post("/api/mem0/search", json={"query": "x"}, headers=auth_headers).status_code == 400
    assert client.post("/api/mem0/all", json={}, headers=auth_headers).status_code == 400
    assert client.post("/api/mem0/add", json={"messages": []}, headers=auth_headers).status_code == 400
