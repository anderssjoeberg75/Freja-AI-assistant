"""CORS origin policy (issues #19, #41, #55).

The policy is the outer boundary: with allow_origins=["*"] any page on the internet could
read an API response, so any lapse in authentication escalated straight to credential
disclosure. These tests pin which origins may read a response and which may not.
"""

import pytest
from fastapi.testclient import TestClient

from server import app
from backend.database import get_api_key, set_api_key
from backend.origins import is_allowed_origin, is_trusted_host, origin_of


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


PUBLIC_ORIGINS = [
    "https://evil.example",
    "http://evil.example",
    "https://freja-ai-assistant.com",
    "https://192.168.107.15.evil.com",   # look-alike host, publicly resolvable
    "https://localhost.evil.com",        # look-alike host
]

TRUSTED_ORIGINS = [
    "http://localhost:5000",
    "http://127.0.0.1:8000",
    "http://192.168.107.15:5000",
    "http://10.0.0.4:8000",
    "http://172.16.3.9:5000",
    "http://freja.local:5000",
]


@pytest.mark.parametrize("origin", PUBLIC_ORIGINS)
def test_public_origin_gets_no_cors_grant(origin):
    assert is_allowed_origin(origin) is False


@pytest.mark.parametrize("origin", TRUSTED_ORIGINS)
def test_loopback_and_lan_origins_are_trusted(origin):
    assert is_allowed_origin(origin) is True


def test_userinfo_cannot_smuggle_a_trusted_host():
    """"https://evil.com@127.0.0.1" must not read as loopback."""
    assert is_allowed_origin("https://127.0.0.1@evil.com") is False
    assert origin_of("https://evil.com@127.0.0.1") == "https://127.0.0.1"


def test_public_ip_is_not_trusted():
    assert is_trusted_host("8.8.8.8") is False
    assert is_trusted_host("172.32.0.1") is False   # just outside the 172.16/12 private block
    assert is_trusted_host("172.16.0.1") is True    # just inside it


def test_explicitly_configured_origin_is_allowed():
    """A public origin can still be opted into via freja_allowed_origins."""
    original = get_api_key('freja_allowed_origins')
    try:
        set_api_key('freja_allowed_origins', 'https://hud.example.com')
        assert is_allowed_origin("https://hud.example.com") is True
        assert is_allowed_origin("https://other.example.com") is False
    finally:
        set_api_key('freja_allowed_origins', original or "")


def test_api_response_is_not_readable_by_a_public_origin(auth_headers):
    """The end-to-end guarantee: even a correctly authenticated request must not hand a
    hostile page a readable response."""
    client = TestClient(app)
    res = client.get("/api/keys", headers={**auth_headers, "Origin": "https://evil.example"})
    # The request itself may succeed server-side; what matters is that the browser is never
    # told the response may be read cross-origin.
    assert res.headers.get("access-control-allow-origin") != "https://evil.example"
    assert res.headers.get("access-control-allow-origin") != "*"


def test_api_response_is_readable_by_the_hud_origin(auth_headers):
    """The HUD runs on a different port, so it is cross-origin and must still work."""
    client = TestClient(app)
    res = client.get("/api/keys", headers={**auth_headers, "Origin": "http://localhost:5000"})
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5000"


def test_preflight_from_public_origin_is_refused():
    client = TestClient(app)
    res = client.options(
        "/api/keys",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-freja-token",
        },
    )
    assert res.headers.get("access-control-allow-origin") not in ("https://evil.example", "*")


def test_preflight_from_hud_origin_is_allowed():
    client = TestClient(app)
    res = client.options(
        "/api/keys",
        headers={
            "Origin": "http://localhost:5000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-freja-token",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5000"


def test_auth_failure_does_not_reflect_an_arbitrary_origin():
    """Issue #41: the 401/403/429 path echoed back whatever Origin it was given."""
    client = TestClient(app)
    res = client.get("/api/keys", headers={"Origin": "https://evil.example"})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") != "https://evil.example"
    assert res.headers.get("access-control-allow-origin") != "*"


def test_auth_failure_still_reaches_the_hud():
    """A 401 must remain readable by the HUD, which keys its login prompt off it."""
    client = TestClient(app)
    res = client.get("/api/keys", headers={"Origin": "http://localhost:5000"})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5000"
