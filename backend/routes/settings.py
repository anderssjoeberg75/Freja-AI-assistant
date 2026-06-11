"""Settings API route router using FastAPI."""

import sqlite3
from fastapi import APIRouter, HTTPException, Request
from backend.config import DB_FILE

router = APIRouter()

@router.get("/api/keys")
async def get_keys():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT key_name, key_value FROM api_keys')
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/keys")
async def post_keys(request: Request):
    try:
        data = await request.json()
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        for key_name, key_value in data.items():
            cursor.execute('''
                INSERT INTO api_keys (key_name, key_value)
                VALUES (?, ?)
                ON CONFLICT(key_name) DO UPDATE SET key_value = excluded.key_value
            ''', (key_name, key_value))
        conn.commit()
        conn.close()
        return {'status': 'success'}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
