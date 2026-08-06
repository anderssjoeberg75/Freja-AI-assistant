"""Server-side nonce store binding an OAuth `state` value to the user who requested it.

Provider callbacks (`/api/{strava,fitbit,withings}/callback`) are necessarily auth-exempt -
the browser lands there directly from the provider's redirect, with no way to attach our
own access token or JWT. Before this module existed, those callbacks trusted a plain
`user_id` digit parsed straight out of the unauthenticated `state` query parameter, which let
anyone who could reach the callback (no login required) bind a freshly obtained refresh token
to an arbitrary `user_id` - overwriting any existing user's stored credential via
`set_api_key`'s upsert. `generate_oauth_state` mints a random nonce for the authenticated
caller who is about to start the OAuth dance; `consume_oauth_state` is the only way the
callback recovers a `user_id`, and it only does so for a nonce this module itself issued.
"""

import secrets
import time

_OAUTH_STATE_TTL_SECONDS = 600
_pending_states: dict[str, dict] = {}  # state -> {"user_id": int, "expires_at": float}


def generate_oauth_state(user_id: int) -> str:
    """Mints a one-time nonce bound to user_id, valid for _OAUTH_STATE_TTL_SECONDS."""
    _prune_expired()
    state = secrets.token_urlsafe(32)
    _pending_states[state] = {"user_id": user_id, "expires_at": time.time() + _OAUTH_STATE_TTL_SECONDS}
    return state


def consume_oauth_state(state: str | None) -> int | None:
    """Validates and single-use-consumes a nonce, returning the bound user_id or None.

    Pops the entry regardless of outcome (expired or not) so a state value can never be
    replayed, matching the pattern already used for Instagram's OAuth CSRF nonce.
    """
    if not state:
        return None
    entry = _pending_states.pop(state, None)
    if not entry:
        return None
    if time.time() > entry["expires_at"]:
        return None
    return entry["user_id"]


def _prune_expired():
    now = time.time()
    expired = [s for s, e in _pending_states.items() if now > e["expires_at"]]
    for s in expired:
        _pending_states.pop(s, None)
