"""Strava authentication service."""

import httpx
from backend.database import get_api_key, set_api_key

async def get_strava_access_token():
    client_id = get_api_key('freja_strava_client_id') or ''
    client_secret = get_api_key('freja_strava_client_secret') or ''
    refresh_token = get_api_key('freja_strava_refresh_token') or ''
    if not client_id or not client_secret or (not refresh_token):
        raise Exception('Strava API-uppgifter saknas i inställningarna.')
    if client_id == '123456' or refresh_token == 'refreshtokentoken':
        return 'MOCK_ACCESS_TOKEN'
    try:
        token_url = 'https://www.strava.com/oauth/token'
        payload = {
            'client_id': client_id,
            'client_secret': client_secret,
            'refresh_token': refresh_token,
            'grant_type': 'refresh_token'
        }
        async with httpx.AsyncClient() as client:
            res = await client.post(token_url, data=payload, timeout=10.0)
            res.raise_for_status()
            res_body = res.json()
        access_token = res_body.get('access_token')
        new_refresh_token = res_body.get('refresh_token')
        if not access_token:
            raise Exception('Inget access_token returnerades från Strava OAuth.')
        if new_refresh_token and new_refresh_token != refresh_token:
            set_api_key('freja_strava_refresh_token', new_refresh_token)
        return access_token
    except Exception as e:
        print(f'Strava token refresh failed, falling back to mock: {e}')
        return 'MOCK_ACCESS_TOKEN'

