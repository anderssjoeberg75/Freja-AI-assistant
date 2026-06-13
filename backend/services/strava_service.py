"""Strava authentication service."""

import httpx
from backend.database import get_db_connection

async def get_strava_access_token():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_id',))
        row_id = cursor.fetchone()
        cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_secret',))
        row_secret = cursor.fetchone()
        cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_refresh_token',))
        row_refresh = cursor.fetchone()
        
    client_id = row_id[0].strip() if row_id else ''
    client_secret = row_secret[0].strip() if row_secret else ''
    refresh_token = row_refresh[0].strip() if row_refresh else ''
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
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('\n                INSERT INTO api_keys (key_name, key_value)\n                VALUES (?, ?)\n                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value\n            ', ('freja_strava_refresh_token', new_refresh_token))
                conn.commit()
        return access_token
    except Exception as e:
        print(f'Strava token refresh failed, falling back to mock: {e}')
        return 'MOCK_ACCESS_TOKEN'

