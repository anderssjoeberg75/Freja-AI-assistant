"""Tests for GET /api/system/token-qr, which encodes the access token + backend URL as a
QR-code PNG so a mobile client can scan its connection settings instead of typing them."""

import json

import pytest
from fastapi.testclient import TestClient

from server import app
from backend.database import get_api_key
from backend.routes import settings as settings_route


@pytest.fixture
def db_token():
    return get_api_key("freja_access_token") or "freja1234"


def test_payload_is_compact_json_with_url_and_token():
    payload = settings_route.build_token_qr_payload("http://192.168.107.15:8000/", "secret-tok")
    assert json.loads(payload) == {"url": "http://192.168.107.15:8000", "token": "secret-tok"}
    # compact: no spaces after separators
    assert ", " not in payload and '": ' not in payload


def test_token_qr_requires_a_token():
    assert TestClient(app).get("/api/system/token-qr").status_code == 401


def test_token_qr_returns_a_png(db_token):
    res = TestClient(app).get("/api/system/token-qr", headers={"X-Freja-Token": db_token})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content.startswith(b"\x89PNG\r\n\x1a\n"), "body must be a real PNG"


def test_url_override_is_honoured(db_token):
    res = TestClient(app).get(
        "/api/system/token-qr",
        params={"url": "http://freja.local:8000"},
        headers={"X-Freja-Token": db_token},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"


def test_400_when_no_access_token_is_configured(monkeypatch, db_token):
    # Blank only the endpoint's own lookup, so auth still accepts the header while the
    # handler sees "no token configured" and returns 400 instead of a QR.
    monkeypatch.setattr(settings_route, "get_api_key", lambda name: "")
    res = TestClient(app).get("/api/system/token-qr", headers={"X-Freja-Token": db_token})
    assert res.status_code == 400
