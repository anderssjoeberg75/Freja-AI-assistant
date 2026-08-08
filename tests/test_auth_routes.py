"""Tests for backend/routes/auth.py (user registration, login, JWT validation)."""

import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_db_session, init_db
from backend.models import User

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_db_session() as db:
        # Clean up test users
        db.query(User).filter(User.email.in_(["testuser@example.com", "loginuser@example.com"])).delete(synchronize_session=False)
        db.commit()


def test_register_and_login_flow():
    # 1. Register a new user
    reg_resp = client.post(
        "/api/auth/register",
        json={"email": "testuser@example.com", "password": "securepassword123", "name": "Test User"}
    )
    assert reg_resp.status_code == 200
    data = reg_resp.json()
    assert "access_token" in data
    assert data["email"] == "testuser@example.com"
    token = data["access_token"]

    # 2. Get /api/auth/me with Bearer token
    me_resp = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == "testuser@example.com"
    assert me_data["name"] == "Test User"

    # 3. Login with registered credentials
    login_resp = client.post(
        "/api/auth/login",
        json={"email": "testuser@example.com", "password": "securepassword123"}
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert "access_token" in login_data

    # 4. Login with invalid password fails
    fail_resp = client.post(
        "/api/auth/login",
        json={"email": "testuser@example.com", "password": "wrongpassword"}
    )
    assert fail_resp.status_code == 401


def test_user_chat_isolation():
    res_a = client.post("/api/auth/register", json={"email": "usera_iso@example.com", "password": "passa123"}).json()
    if "access_token" not in res_a:
        res_a = client.post("/api/auth/login", json={"email": "usera_iso@example.com", "password": "passa123"}).json()

    res_b = client.post("/api/auth/register", json={"email": "userb_iso@example.com", "password": "passb123"}).json()
    if "access_token" not in res_b:
        res_b = client.post("/api/auth/login", json={"email": "userb_iso@example.com", "password": "passb123"}).json()

    token_a = res_a["access_token"]
    token_b = res_b["access_token"]

    # User A posts a message
    client.post(
        "/api/chat/message",
        json={"sender": "user", "content": "Hemligt meddelande från User A", "channel": "web"},
        headers={"Authorization": f"Bearer {token_a}"}
    )

    # User B posts a message
    client.post(
        "/api/chat/message",
        json={"sender": "user", "content": "Hemligt meddelande från User B", "channel": "web"},
        headers={"Authorization": f"Bearer {token_b}"}
    )

    # User A fetches history
    hist_a = client.get("/api/chat/history", headers={"Authorization": f"Bearer {token_a}"}).json()
    contents_a = [m["content"] for m in hist_a]
    assert "Hemligt meddelande från User A" in contents_a
    assert "Hemligt meddelande från User B" not in contents_a

    # User B fetches history
    hist_b = client.get("/api/chat/history", headers={"Authorization": f"Bearer {token_b}"}).json()
    contents_b = [m["content"] for m in hist_b]
    assert "Hemligt meddelande från User B" in contents_b
    assert "Hemligt meddelande från User A" not in contents_b
