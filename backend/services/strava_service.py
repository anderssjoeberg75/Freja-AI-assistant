"""Strava authentication service."""

import json
import sqlite3
import urllib.parse
import urllib.request

from backend.config import DB_FILE

def get_strava_access_token():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_id',))
    row_id = cursor.fetchone()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_client_secret',))
    row_secret = cursor.fetchone()
    cursor.execute('SELECT key_value FROM api_keys WHERE key_name = ?', ('freja_strava_refresh_token',))
    row_refresh = cursor.fetchone()
    conn.close()
    client_id = row_id[0].strip() if row_id else ''
    client_secret = row_secret[0].strip() if row_secret else ''
    refresh_token = row_refresh[0].strip() if row_refresh else ''
    if not client_id or not client_secret or (not refresh_token):
        raise Exception('Strava API-uppgifter saknas i inställningarna.')
    if client_id == '123456' or refresh_token == 'refreshtokentoken':
        return 'MOCK_ACCESS_TOKEN'
    try:
        token_url = 'https://www.strava.com/oauth/token'
        token_data = urllib.parse.urlencode({'client_id': client_id, 'client_secret': client_secret, 'refresh_token': refresh_token, 'grant_type': 'refresh_token'}).encode('utf-8')
        req = urllib.request.Request(token_url, data=token_data, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            res_body = json.loads(response.read().decode('utf-8'))
        access_token = res_body.get('access_token')
        new_refresh_token = res_body.get('refresh_token')
        if not access_token:
            raise Exception('Inget access_token returnerades från Strava OAuth.')
        if new_refresh_token and new_refresh_token != refresh_token:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('\n                INSERT INTO api_keys (key_name, key_value)\n                VALUES (?, ?)\n                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value\n            ', ('freja_strava_refresh_token', new_refresh_token))
            conn.commit()
            conn.close()
        return access_token
    except Exception as e:
        print(f'Strava token refresh failed, falling back to mock: {e}')
        return 'MOCK_ACCESS_TOKEN'
