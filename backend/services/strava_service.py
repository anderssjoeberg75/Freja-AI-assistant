"""Strava authentication service."""

import time
import httpx
from backend.services.http_client import shared_client
from backend.database import get_api_key, set_api_key

# In-memory access-token cache, keyed by refresh token so a token rotation or reconnecting a
# different account can't serve a stale entry. Without this, every call (activity details,
# athlete stats, each activity/laps/zones request in a browsing session) refreshed the token
# from scratch, burning through Strava's 100-requests/15-min rate limit unnecessarily.
_token_cache = {"refresh_token": None, "access_token": None, "expires_at": 0.0}


async def get_strava_access_token():
    client_id = get_api_key('freja_strava_client_id') or ''
    client_secret = get_api_key('freja_strava_client_secret') or ''
    refresh_token = get_api_key('freja_strava_refresh_token') or ''
    if not client_id or not client_secret or (not refresh_token):
        raise Exception('Strava API credentials are missing from the settings.')
    if client_id == '123456' or refresh_token == 'refreshtokentoken':
        return 'MOCK_ACCESS_TOKEN'

    if (
        _token_cache["refresh_token"] == refresh_token
        and _token_cache["access_token"]
        and time.time() < _token_cache["expires_at"]
    ):
        return _token_cache["access_token"]

    try:
        token_url = 'https://www.strava.com/oauth/token'
        payload = {
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token'
        }
        async with shared_client() as client:
            res = await client.post(token_url, data=payload, timeout=10.0)
            res.raise_for_status()
            res_body = res.json()
        access_token = res_body.get('access_token')
        new_refresh_token = res_body.get('refresh_token')
        if not access_token:
            raise Exception('No access_token was returned from Strava OAuth.')
        if new_refresh_token and new_refresh_token != refresh_token:
            set_api_key('freja_strava_refresh_token', new_refresh_token)
        cache_refresh_token = new_refresh_token or refresh_token
        expires_at = res_body.get('expires_at')  # Strava returns an absolute unix timestamp
        expires_in = res_body.get('expires_in', 21600)
        _token_cache.update({
            "refresh_token": cache_refresh_token,
            "access_token": access_token,
            "expires_at": (float(expires_at) if expires_at else time.time() + expires_in) - 60,
        })
        return access_token
    except Exception as e:
        # A real, configured connection whose refresh call failed (network error, 429
        # rate-limit, revoked/expired refresh token, Strava outage) must not be treated the
        # same as the deliberate demo-mode sentinel above - callers used to receive
        # 'MOCK_ACCESS_TOKEN' either way and would silently serve fabricated activity data
        # with a 200 OK for what was actually a broken connection.
        raise Exception(f'Strava token refresh failed: {e}') from e

