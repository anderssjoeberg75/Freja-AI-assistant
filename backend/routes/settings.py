"""Settings HTTP route handlers."""

import json
import sqlite3

from backend.config import DB_FILE


def handle_get_keys(handler):
    handler.send_response(200)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
    handler.end_headers()
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT key_name, key_value FROM api_keys')
    rows = cursor.fetchall()
    conn.close()
    keys = {row[0]: row[1] for row in rows}
    handler.wfile.write(json.dumps(keys).encode('utf-8'))

def handle_post_keys(handler):
    content_length = int(handler.headers['Content-Length'])
    post_data = handler.rfile.read(content_length)
    try:
        data = json.loads(post_data.decode('utf-8'))
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        for key_name, key_value in data.items():
            cursor.execute('\n                        INSERT INTO api_keys (key_name, key_value)\n                        VALUES (?, ?)\n                        ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value\n                    ', (key_name, key_value))
        conn.commit()
        conn.close()
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'success'}).encode('utf-8'))
    except Exception as e:
        handler.send_response(400)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
