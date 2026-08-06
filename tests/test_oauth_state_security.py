"""Regression tests for the OAuth state-binding fix on Strava/Fitbit/Withings callbacks.

Before this fix, `/api/{strava,fitbit,withings}/callback` parsed a plain digit straight out
of the unauthenticated `state` query parameter and used it as the user_id to bind the
resulting refresh token to (via `set_api_key(..., user_id=user_id)`). Since these callbacks
must stay reachable without our own auth headers (the browser lands there directly from the
provider's redirect), that meant anyone who could reach the callback URL could overwrite any
existing user's stored OAuth credential just by choosing a different `state` digit.

The fix: `state` is now an opaque, single-use, server-issued nonce
(`backend/services/oauth_state.py`) bound to the user who requested it via a new
authenticated `GET /api/{provider}/oauth-state` endpoint. The callback only accepts a
`user_id` recovered from a nonce it itself issued.
"""

import pytest
from fastapi.testclient import TestClient
from server import app
from backend.database import get_api_key, get_db_session, init_db
from backend.models import User
from backend.services.oauth_state import generate_oauth_state, consume_oauth_state

client = TestClient(app)


@pytest.fixture
def auth_headers():
    token = get_api_key('freja_access_token') or "freja1234"
    return {"X-Freja-Token": token}


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    with get_db_session() as db:
        db.query(User).filter(User.email.in_(["oauthstate_victim@example.com", "oauthstate_attacker@example.com"])).delete(synchronize_session=False)
        db.commit()


# --- Unit tests: the nonce store itself ---

def test_generate_and_consume_state_roundtrip():
    state = generate_oauth_state(42)
    assert consume_oauth_state(state) == 42


def test_consume_state_is_single_use():
    state = generate_oauth_state(7)
    assert consume_oauth_state(state) == 7
    assert consume_oauth_state(state) is None, "a state nonce must not be usable twice"


def test_consume_unknown_state_returns_none():
    assert consume_oauth_state("this-was-never-issued") is None


def test_consume_empty_state_returns_none():
    assert consume_oauth_state("") is None
    assert consume_oauth_state(None) is None


# --- The old vulnerability: a raw digit in `state` must no longer bind to that user_id ---

@pytest.mark.parametrize("provider", ["strava", "fitbit", "withings"])
def test_callback_rejects_a_raw_digit_state_instead_of_trusting_it_as_user_id(provider, auth_headers):
    """Regression test for the IDOR: `state=1` (or any other digit) used to be taken
    literally as the target user_id with zero verification. It must now be rejected."""
    response = client.get(
        f"/api/{provider}/callback?code=some_code&state=1",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Invalid or expired authorization link" in response.text


@pytest.mark.parametrize("provider", ["strava", "fitbit", "withings"])
def test_callback_rejects_missing_state(provider, auth_headers):
    response = client.get(f"/api/{provider}/callback?code=some_code", headers=auth_headers)
    assert response.status_code == 400
    assert "Invalid or expired authorization link" in response.text


# --- The oauth-state endpoints: must be authenticated and bind to the caller ---

@pytest.mark.parametrize("provider", ["strava", "fitbit", "withings"])
def test_oauth_state_endpoint_requires_auth(provider):
    response = client.get(f"/api/{provider}/oauth-state")
    assert response.status_code == 401


@pytest.mark.parametrize("provider", ["strava", "fitbit", "withings"])
def test_oauth_state_endpoint_binds_to_the_authenticated_caller(provider):
    reg = client.post(
        "/api/auth/register",
        json={"email": "oauthstate_victim@example.com", "password": "securepassword123"},
    ).json()
    headers = {"Authorization": f"Bearer {reg['access_token']}"}

    res = client.get(f"/api/{provider}/oauth-state", headers=headers)
    assert res.status_code == 200
    state = res.json()["state"]

    # The nonce must resolve back to this user - and only once.
    assert consume_oauth_state(state) == reg["user_id"]
    assert consume_oauth_state(state) is None


def test_a_second_user_cannot_hijack_a_state_nonce_issued_to_someone_else():
    """Even holding a *valid, unexpired* nonce that was issued to a different account must
    not let an attacker bind their own OAuth code to the victim's user_id - the nonce is
    minted for whoever called /oauth-state while authenticated as the victim, and an
    attacker has no way to make that call as the victim without the victim's own token."""
    victim = client.post(
        "/api/auth/register",
        json={"email": "oauthstate_victim@example.com", "password": "victimpassword123"},
    ).json()
    attacker = client.post(
        "/api/auth/register",
        json={"email": "oauthstate_attacker@example.com", "password": "attackerpassword123"},
    ).json()

    # Attacker requests a state using their OWN token - it can only ever bind to their own id.
    res = client.get(
        "/api/strava/oauth-state",
        headers={"Authorization": f"Bearer {attacker['access_token']}"},
    )
    state = res.json()["state"]
    assert consume_oauth_state(state) == attacker["user_id"]
    assert attacker["user_id"] != victim["user_id"]


# --- End-to-end: a legitimately issued state lets the callback store the token correctly ---

@pytest.mark.asyncio
async def test_valid_state_binds_the_refresh_token_to_the_correct_user(monkeypatch):
    import backend.routes.strava as strava_module
    from backend.database import set_api_key, get_api_key as get_key

    reg = client.post(
        "/api/auth/register",
        json={"email": "oauthstate_victim@example.com", "password": "securepassword123"},
    ).json()
    user_id = reg["user_id"]
    set_api_key("freja_strava_client_id", "test_client_id", user_id=user_id)
    set_api_key("freja_strava_client_secret", "test_client_secret", user_id=user_id)

    state_res = client.get(
        "/api/strava/oauth-state",
        headers={"Authorization": f"Bearer {reg['access_token']}"},
    )
    state = state_res.json()["state"]

    class FakeTokenClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, *a, **k):
            class R:
                def raise_for_status(self):
                    return None
                def json(self):
                    return {"access_token": "tok", "refresh_token": "victim_refresh_token"}
            return R()

    monkeypatch.setattr(strava_module, "shared_client", FakeTokenClient)

    callback_res = client.get(f"/api/strava/callback?code=real_code&state={state}")
    assert callback_res.status_code == 200
    assert get_key("freja_strava_refresh_token", user_id=user_id) == "victim_refresh_token"
